"""LIFE reference configuration on the existing simple V2 market maker."""
import asyncio
import time
from decimal import ROUND_DOWN, Decimal, localcontext
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from controllers.market_making import pmm_simple
from hummingbot.core.data_type.common import OrderType, PositionMode, PositionSide, PriceType, TradeType
from hummingbot.core.utils.read_retry import retry_delay, transient_read
from hummingbot.strategy_v2.models.base import RunnableStatus
from hummingbot.strategy_v2.models.executor_actions import StopExecutorAction
from hummingbot.strategy_v2.utils.listing_bootstrap import ListingBootstrap
from hummingbot.strategy_v2.utils.quote_guard import (
    MarketObservation,
    QuoteGuard,
    QuoteGuardConfig,
    SidewaysRange,
    observe_market,
)


class ReferencePair(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    pair: str
    weight: Decimal = Field(gt=0, le=1, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_pair(self):
        tokens = self.pair.split("-")
        if len(tokens) != 2 or not tokens[0] or tokens[1] != "USDT" or self.pair != self.pair.upper():
            raise ValueError("References require a BASE-USDT pair")
        return self


class LifeMMConfig(pmm_simple.PMMSimpleConfig):
    model_config = ConfigDict(validate_default=True)
    controller_name: str = "life_mm"
    connector_name: Literal["okx", "okx_perpetual"] = "okx"
    trading_pair: str = "LIFE-USDT"
    reference_connector: Literal["okx"] = "okx"
    scenario: Literal["follow_market", "sideways", "follow_token"] = "sideways"
    references: List[ReferencePair] = Field(default_factory=list)
    max_reference_pairs: int = Field(default=8, ge=1, le=32)
    max_data_age_seconds: Decimal = Field(default=Decimal("10"), gt=0, allow_inf_nan=False)
    initial_anchor: Optional[Decimal] = Field(default=None, gt=0, allow_inf_nan=False)
    min_price: Decimal = Field(gt=0, allow_inf_nan=False)
    max_price: Decimal = Field(gt=0, allow_inf_nan=False)
    leverage: int = Field(default=1, ge=1, le=10)
    position_mode: PositionMode = PositionMode.HEDGE
    margin_mode: Literal["CROSS"] = "CROSS"
    max_base_inventory: Optional[Decimal] = Field(default=None, gt=0, allow_inf_nan=False)
    max_long_base: Optional[Decimal] = Field(default=None, gt=0, allow_inf_nan=False)
    max_short_base: Optional[Decimal] = Field(default=None, gt=0, allow_inf_nan=False)
    max_gross_base: Optional[Decimal] = Field(default=None, gt=0, allow_inf_nan=False)
    skip_rebalance: bool = True
    stop_loss: Optional[Decimal] = None
    take_profit: Optional[Decimal] = None
    time_limit: Optional[int] = None
    runtime_config_managed: bool = False
    mm_session_id: Optional[str] = None
    mm_generation: Optional[int] = Field(default=None, ge=0, strict=True)
    saved_life_anchor: Optional[Decimal] = Field(default=None, gt=0, allow_inf_nan=False)
    saved_source_anchors: Dict[str, Decimal] = Field(default_factory=dict)
    quote_guards: QuoteGuardConfig = Field(default_factory=QuoteGuardConfig)
    sideways_range: Optional[SidewaysRange] = None

    @model_validator(mode="after")
    def validate_mm_policy(self):
        if self.quote_guards.mode == "bootstrap_then_market":
            if self.initial_anchor is None or self.quote_guards.bootstrap.max_quote > self.total_amount_quote:
                raise ValueError("Bootstrap requires initial_anchor and a budget within ordinary quote allocation")
        if self.quote_guards.enabled and self.max_data_age_seconds > 30:
            raise ValueError("Strict guard freshness cannot exceed the existing 30-second terminal-order cache")
        if self.sideways_range is not None:
            if self.scenario != "sideways" or not self.quote_guards.enabled:
                raise ValueError("Manual range requires sideways with enabled strict quote guards")
            if not self.min_price <= self.sideways_range.lower_price < self.sideways_range.upper_price <= self.max_price:
                raise ValueError("Sideways range must be within absolute price bounds")
            if (self.quote_guards.mode == "bootstrap_then_market"
                    and not self.sideways_range.lower_price <= self.initial_anchor <= self.sideways_range.upper_price):
                raise ValueError("Bootstrap anchor must be inside the manual sideways range")
        if (self.mm_session_id is None) != (self.mm_generation is None) or self.mm_session_id == "":
            raise ValueError("MM session identity and generation must be supplied together")
        if self.scenario == "sideways":
            if self.references:
                raise ValueError("Sideways does not accept references")
        elif (not self.references or len(self.references) > self.max_reference_pairs
              or len({ref.pair for ref in self.references}) != len(self.references)
              or sum(ref.weight for ref in self.references) != Decimal("1")):
            raise ValueError("References require unique pairs within the basket limit and weights totaling 1")
        if self.scenario == "follow_token" and len(self.references) != 1:
            raise ValueError("follow_token requires exactly one reference")
        if self.saved_life_anchor is not None:
            if (set(self.saved_source_anchors) != {ref.pair for ref in self.references}
                    or any(not value.is_finite() or value <= 0 for value in self.saved_source_anchors.values())):
                raise ValueError("Saved anchors must cover the exact reference basket")
        elif self.saved_source_anchors:
            raise ValueError("Saved source anchors require a LIFE anchor")
        if self.min_price >= self.max_price:
            raise ValueError("min_price must be below max_price")
        if self.initial_anchor is not None and not self.min_price <= self.initial_anchor <= self.max_price:
            raise ValueError("initial_anchor must be within price bounds")
        if self.position_mode != PositionMode.HEDGE or (self.connector_name == "okx" and self.leverage != 1):
            raise ValueError("Use HEDGE; spot leverage must be 1")
        if self.connector_name == "okx" and self.max_base_inventory is None:
            raise ValueError("Spot requires max_base_inventory")
        if self.connector_name == "okx_perpetual" and any(
                cap is None for cap in (self.max_long_base, self.max_short_base, self.max_gross_base)):
            raise ValueError("Perpetual requires long, short and gross exposure caps")
        if not self.skip_rebalance or any(value is not None for value in (
                self.stop_loss, self.take_profit, self.time_limit, self.trailing_stop)):
            raise ValueError("LIFE MM preserves positions; automatic rebalance and exit barriers must be disabled")
        if (not self.total_amount_quote.is_finite() or self.total_amount_quote <= 0
                or self.executor_refresh_time <= 0 or self.cooldown_time < 0):
            raise ValueError("Quote budget and refresh must be positive; cooldown cannot be negative")
        for spreads, amounts in ((self.buy_spreads, self.buy_amounts_pct), (self.sell_spreads, self.sell_amounts_pct)):
            if not spreads or len(spreads) != len(amounts):
                raise ValueError("Each side requires matching quote levels and amounts")
            if any(not Decimal(str(spread)).is_finite() or not 0 < Decimal(str(spread)) < 1 for spread in spreads):
                raise ValueError("Spreads must be finite and between 0 and 1")
            if any(not Decimal(str(amount)).is_finite() or Decimal(str(amount)) <= 0 for amount in amounts):
                raise ValueError("Level allocations must be finite and positive")
        return self

    @property
    def triple_barrier_config(self):
        return super().triple_barrier_config.model_copy(update={"open_order_type": OrderType.LIMIT_MAKER})


class LifeMMController(pmm_simple.PMMSimpleController):
    def __init__(self, config: LifeMMConfig, *args, **kwargs):
        if config.quote_guards.mode == "bootstrap_then_market" and (not config.runtime_config_managed or not config.mm_session_id):
            raise ValueError("Listing bootstrap requires managed MM session ownership")
        super().__init__(config, *args, **kwargs)
        self._bootstrap = (ListingBootstrap(config.quote_guards, anchor=config.initial_anchor, sideways_range=config.sideways_range,
                                            max_gap_seconds=config.max_data_age_seconds)
                           if config.quote_guards.mode == "bootstrap_then_market" else None)
        self._life_anchor = config.saved_life_anchor
        self._source_anchors = dict(config.saved_source_anchors)
        self._stops_requested = set()
        self._account_ready = config.connector_name == "okx"
        self._session_admitted = config.mm_session_id is None
        self._startup_retry = None
        self._startup_stage = "not_started"
        self._startup_retry_sleep = asyncio.sleep
        self._quote_guard = QuoteGuard(config.quote_guards, config.sideways_range)

    def restore_bootstrap_episode(self, episode):
        if self._bootstrap is not None:
            self._bootstrap.restore(episode or dict(started_at=None, expires_at=None, market_qualified=False),
                                    bound=episode is not None)

    def quote_price_valid(self, price, *, bootstrap=None):
        if not price.is_finite() or not self.config.min_price <= price <= self.config.max_price:
            return False
        bootstrap = self._bootstrap is not None and not self._bootstrap.market_qualified if bootstrap is None else bootstrap
        if bootstrap:
            if not self._bootstrap.lower_price <= price <= self._bootstrap.upper_price:
                return False
            limits = self.config.sideways_range
            if limits is not None and not limits.lower_price <= price <= limits.upper_price:
                return False
        return True

    def check_maker_crossing(self, price, side):
        if self._bootstrap is None:
            return
        book = self.market_data_provider.get_order_book(self.config.connector_name, self.config.trading_pair)
        opposite = book.ask_entries() if side == TradeType.BUY else book.bid_entries()
        first = next(iter(opposite), None)
        if first is not None:
            best = Decimal(str(first.price))
            if not best.is_finite() or (price >= best if side == TradeType.BUY else price <= best):
                raise ValueError("bootstrap_quote_crossing")

    def _evaluate_guard(self, observation, reference, sample):
        if self._bootstrap is None:
            return self._quote_guard.evaluate(observation, reference, time.monotonic(), sample=sample)
        return self._bootstrap.evaluate(observation, reference, time.monotonic(), sample=sample,
                                        reference_model=bool(self.config.references),
                                        configured_anchor=self._life_anchor == self.config.initial_anchor)

    def admit_session(self, session_id, generation):
        if (session_id, generation) != (self.config.mm_session_id, self.config.mm_generation):
            raise ValueError("Session admission identity changed")
        self._session_admitted = True

    def reference_state(self):
        if self._life_anchor is None:
            raise ValueError("Reference anchors are not ready")
        return {"life": str(self._life_anchor), "sources": {pair: str(price) for pair, price in self._source_anchors.items()}}

    def update_config(self, new_config):
        if not self.config.runtime_config_managed:
            # Preserve the upstream loader/update path while validating the combined settings.
            previous = self.config
            super().update_config(new_config)
            try:
                self.config = LifeMMConfig.model_validate(self.config.model_dump())
            except ValueError:
                self.config = previous
                raise
            if (self.config.quote_guards, self.config.sideways_range) != (previous.quote_guards, previous.sideways_range):
                self._quote_guard = QuoteGuard(self.config.quote_guards, self.config.sideways_range)

    def _fresh_price(self, connector_name, pair, initial_anchor=None):
        connector = self.market_data_provider.get_connector_with_fallback(connector_name)
        if pair not in connector.trading_rules:
            raise ValueError(f"reference_pair_unavailable:{connector_name}:{pair}")
        book = self.market_data_provider.get_order_book(connector_name, pair)
        updated_at = max(book.snapshot_uid, book.last_diff_uid)
        # These existing OKX data sources use seconds (spot) and milliseconds (SWAP) as book IDs.
        if connector_name == "okx_perpetual":
            updated_at /= 1000
        age = self.market_data_provider.time() - updated_at
        if updated_at <= 0 or age < -1 or age > float(self.config.max_data_age_seconds):
            raise ValueError(f"stale_reference:{connector_name}:{pair}")
        try:
            bid = Decimal(str(self.market_data_provider.get_price_by_type(connector_name, pair, PriceType.BestBid)))
            ask = Decimal(str(self.market_data_provider.get_price_by_type(connector_name, pair, PriceType.BestAsk)))
        except EnvironmentError:
            bid, ask = Decimal("NaN"), Decimal("NaN")
        if not bid.is_finite() or not ask.is_finite() or not 0 < bid <= ask:
            if initial_anchor is not None:
                bids, asks = book.snapshot
                if len(bids) == 0 or len(asks) == 0:
                    return initial_anchor
            raise ValueError(f"invalid_book:{connector_name}:{pair}")
        return (bid + ask) / 2

    def update_reference(self, *, sample_guard=False):
        guard_status = None
        spread_multiplier = self.processed_data.get("spread_multiplier", Decimal("1"))
        try:
            if not self._account_ready:
                raise ValueError("perpetual_account_not_confirmed")
            observation = None
            if self.config.quote_guards.enabled:
                connector = self.market_data_provider.get_connector_with_fallback(self.config.connector_name)
                if self.config.trading_pair not in connector.trading_rules:
                    raise ValueError("reference_pair_unavailable")
                book = self.market_data_provider.get_order_book(self.config.connector_name, self.config.trading_pair)
                observation = observe_market(
                    connector, book, self.config.trading_pair, now=self.market_data_provider.time(),
                    max_age_seconds=self.config.max_data_age_seconds, max_levels=self.config.quote_guards.max_book_levels,
                    max_orders=self.config.quote_guards.max_own_orders)
                if observation.market_mid is None and self._bootstrap is None:
                    guard_status = self._evaluate_guard(observation, Decimal("1"), sample_guard)
                    raise ValueError("market_price_unavailable")
            life_price = (observation.market_mid if observation is not None else
                          self._fresh_price(self.config.connector_name, self.config.trading_pair, self.config.initial_anchor))
            sources = {ref.pair: self._fresh_price(self.config.reference_connector, ref.pair)
                       for ref in self.config.references}
            if self._bootstrap is not None and not self._bootstrap.market_qualified:
                life_price = self.config.initial_anchor
                if self._life_anchor is not None and self._life_anchor != life_price:
                    raise ValueError("bootstrap_anchor_mismatch")
            life_anchor = self._life_anchor or life_price
            if life_anchor is None:
                raise ValueError("market_price_unavailable")
            source_anchors = self._source_anchors or sources
            reference = life_anchor * (1 + sum(
                ref.weight * (sources[ref.pair] / source_anchors[ref.pair] - 1) for ref in self.config.references))
            if not reference.is_finite() or not self.config.min_price <= reference <= self.config.max_price:
                raise ValueError("reference_out_of_bounds")
            if observation is not None:
                guard_status = self._evaluate_guard(observation, reference, sample_guard)
                if not guard_status["ready"] and not guard_status.get("can_admit"):
                    raise ValueError(guard_status["reason_code"])
            self.processed_data = {"reference_price": reference, "spread_multiplier": spread_multiplier, "ready": False,
                                   "quote_guard": guard_status}
            for level in self.get_not_active_levels_ids([]):
                price, _ = self.get_price_and_amount(level)
                if not self.quote_price_valid(price):
                    raise ValueError("quote_out_of_bounds")
                if self._bootstrap is not None and not self._bootstrap.market_qualified:
                    connector = self.market_data_provider.get_connector_with_fallback(self.config.connector_name)
                    price = connector.quantize_order_price(self.config.trading_pair, price)
                    if not self.quote_price_valid(price):
                        raise ValueError("quote_out_of_bounds")
                    self.check_maker_crossing(price, self.get_trade_type_from_level_id(level))
            # Capture the complete basket together; a missing member never saves partial anchors.
            self._life_anchor, self._source_anchors = life_anchor, dict(source_anchors)
            self.processed_data["ready"] = guard_status is None or guard_status["ready"]
            if not self.processed_data["ready"]:
                self.processed_data["reason"] = guard_status["reason_code"]
        except Exception as error:
            reason = str(error).split(":", 1)[0]
            known = {"perpetual_account_not_confirmed", "reference_pair_unavailable", "stale_reference",
                     "invalid_book", "reference_out_of_bounds", "quote_out_of_bounds", "market_price_unavailable",
                     "reference_deviation", "sideways_breakout", "guard_recovering",
                     "bootstrap_binding_unavailable", "bootstrap_not_started", "bootstrap_checkpoint_pending",
                     "bootstrap_expired", "bootstrap_band", "bootstrap_range", "bootstrap_quote_crossing",
                     "market_liquidity_unqualified", "bootstrap_anchor_mismatch"}
            if self.config.quote_guards.enabled and guard_status is None:
                guard_status = self._evaluate_guard(
                    MarketObservation(None, None, self.market_data_provider.time(), detail="required_data_unavailable"),
                    Decimal("1"), False)
                guard_status["reason_code"] = reason if reason in known else "market_price_unavailable"
            self.processed_data = {"ready": False, "reason": str(error) if reason in known else type(error).__name__,
                                   "spread_multiplier": spread_multiplier, "quote_guard": guard_status,
                                   "internal_error": not isinstance(error, (ValueError, ArithmeticError, OSError))}

    async def update_processed_data(self):
        self.update_reference(sample_guard=True)

    async def control_task(self):
        # Run freshness/cancellation even when the generic provider becomes unready.
        await self.update_processed_data()
        if (self._control_error or not self._session_admitted or not self.market_data_provider.ready or not self.processed_data["ready"]
                or not self._exposure_within_caps()):
            await self.send_actions(self._stop_quotes())
        elif self.executors_update_event.is_set():
            await self.send_actions(self.determine_executor_actions())

    def _stop_quotes(self):
        active = {executor.id for executor in self.executors_info if executor.is_active}
        self._stops_requested.intersection_update(active)
        actions = [StopExecutorAction(controller_id=self.config.id, executor_id=executor_id, keep_position=True)
                   for executor_id in active - self._stops_requested]
        self._stops_requested.update(active)
        return actions

    def determine_executor_actions(self):
        if not self._session_admitted or not self.processed_data.get("ready") or not self._exposure_within_caps():
            return self._stop_quotes()
        return super().determine_executor_actions()

    def _exposure(self):
        connector = self.market_data_provider.get_connector_with_fallback(self.config.connector_name)
        if self.config.connector_name == "okx":
            long = Decimal(str(connector.get_balance(self.config.trading_pair.split("-")[0])))
            short = Decimal("0")
        else:
            positions = [position for position in connector.account_positions.values()
                         if position.trading_pair == self.config.trading_pair]
            long = sum((abs(position.amount) for position in positions if position.position_side == PositionSide.LONG), Decimal("0"))
            short = sum((abs(position.amount) for position in positions if position.position_side == PositionSide.SHORT), Decimal("0"))
        # Reserve each active entry through cancellation/hold. is_trading becomes
        # true on the first partial fill, while the remainder can still fill and
        # account updates may lag. Counting its full amount is conservative until
        # the executor terminates; then only the account exposure remains.
        for executor in self.executors_info:
            if executor.is_active or executor.status == RunnableStatus.SHUTTING_DOWN:
                if executor.config.side == TradeType.BUY:
                    long += executor.config.amount
                elif self.config.connector_name == "okx_perpetual":
                    short += executor.config.amount
        if any(not value.is_finite() or value < 0 for value in (long, short)):
            raise ValueError("Invalid account exposure")
        return long, short

    def _within_caps(self, long, short):
        if self.config.connector_name == "okx":
            return long <= self.config.max_base_inventory
        return (long <= self.config.max_long_base and short <= self.config.max_short_base
                and long + short <= self.config.max_gross_base)

    def _exposure_within_caps(self):
        try:
            return self._within_caps(*self._exposure())
        except (ValueError, ArithmeticError, TypeError):
            return False

    def create_actions_proposal(self):
        if not self._session_admitted or not self.processed_data.get("ready") or not self.market_data_provider.ready:
            return []
        try:
            long, short = self._exposure()
            if not self._within_caps(long, short):
                return []
            connector = self.market_data_provider.get_connector_with_fallback(self.config.connector_name)
            available_quote = Decimal(str(connector.get_available_balance("USDT")))
            available_base = Decimal(str(connector.get_available_balance(self.config.trading_pair.split("-")[0]))) \
                if self.config.connector_name == "okx" else Decimal("0")
            if any(not value.is_finite() or value < 0 for value in (available_quote, available_base)):
                return []
            accepted = []
            for action in super().create_actions_proposal():
                config = action.executor_config
                is_buy = config.side == TradeType.BUY
                proposed_long = long + config.amount if is_buy else long
                proposed_short = short + config.amount if not is_buy and self.config.connector_name == "okx_perpetual" else short
                required_quote = config.amount * config.entry_price / config.leverage
                if self.config.runtime_config_managed and self.config.connector_name == "okx" and is_buy:
                    from hummingbot.core.order_submission_guard import spot_buy_preview
                    if not spot_buy_preview(connector, config.amount, config.entry_price):
                        continue
                if not self._within_caps(proposed_long, proposed_short):
                    continue
                if self.config.connector_name == "okx" and not is_buy:
                    if config.amount > available_base:
                        continue
                    available_base -= config.amount
                else:
                    if required_quote > available_quote:
                        continue
                    available_quote -= required_quote
                long, short = proposed_long, proposed_short
                accepted.append(action)
            return accepted
        except (ValueError, ArithmeticError, TypeError):
            return []

    def executors_to_refresh(self):
        return [StopExecutorAction(controller_id=self.config.id, executor_id=executor.id, keep_position=True)
                for executor in self.executors_info if executor.is_active
                and self.market_data_provider.time() - executor.timestamp > self.config.executor_refresh_time]

    async def _startup_read(self, operation):
        """Retry only the supplied read; never replay an enclosing account mutation."""
        attempt, first = 0, time.time()
        while True:
            self._control_progress_at = time.monotonic()
            self._control_wait_until = time.monotonic() + 31
            try:
                result = await asyncio.wait_for(operation(), timeout=30)
            except Exception as error:
                if not transient_read(error):
                    self._startup_retry = None
                    raise
                attempt = min(attempt + 1, 60)
                delay = retry_delay(attempt, error)
                self._startup_retry = dict(reason_code="controller_startup_read_retry", scope=f"controller:{self.config.id}",
                                           severity="warning", detail=type(error).__name__, first_seen_at=first,
                                           last_seen_at=time.time(), last_healthy_at=None, retry_count=attempt,
                                           next_retry_at=time.time() + delay, active_fallback=None,
                                           operator_action_required=False)
                self._control_wait_until = time.monotonic() + delay + 1
                await self._startup_retry_sleep(delay)
            else:
                self._startup_retry = None
                return result
            finally:
                self._control_wait_until = None

    async def on_start(self):
        if self.config.connector_name == "okx_perpetual":
            connector = self.market_data_provider.get_connector_with_fallback(self.config.connector_name)
            async with connector._set_position_mode_lock:
                self._startup_stage = "position_mode_read"
                current_mode = await self._startup_read(connector._fetch_account_position_mode)
                if current_mode is None:
                    raise ValueError("Perpetual account mode is unavailable")
                if current_mode != PositionMode.HEDGE:
                    if connector.account_positions or connector.in_flight_orders:
                        raise ValueError("Cannot change account mode with existing orders or positions")
                    self._startup_stage = "position_mode_change"
                    success, reason = await connector._trading_pair_position_mode_set(
                        PositionMode.HEDGE, self.config.trading_pair)
                    if not success:
                        raise ValueError(f"Perpetual account mode rejected: {reason}")
                    self._startup_stage = "position_mode_confirmation"
                    if await connector._fetch_account_position_mode() != PositionMode.HEDGE:
                        raise ValueError("Perpetual account mode was not confirmed")
                connector._perpetual_trading.set_position_mode(PositionMode.HEDGE)
            self._startup_stage = "leverage_change"
            success, reason = await connector._set_trading_pair_leverage(self.config.trading_pair, self.config.leverage)
            if not success:
                raise ValueError(f"Perpetual leverage rejected: {reason}")
            connector._perpetual_trading.set_leverage(self.config.trading_pair, self.config.leverage)
            self._account_ready = True
        references = [ref.pair for ref in self.config.references]
        if references:
            source = self.market_data_provider.get_connector_with_fallback(self.config.reference_connector)
            if not source.is_trading_required:
                # Public-only connectors do not start the private trading-rules
                # polling task. Reuse the existing public instrument request.
                self._startup_stage = "reference_metadata"
                await self._startup_read(source._update_trading_rules)

            async def acquire():
                try:
                    result = await self.market_data_provider.acquire_order_books(self.config.reference_connector, references, self)
                    if not all(result.get(pair, False) for pair in references):
                        raise OSError("Reference order books are unavailable")
                except BaseException:
                    await self.market_data_provider.release_order_books(self.config.reference_connector, references, self)
                    raise

            self._startup_stage = "reference_books"
            await self._startup_read(acquire)
        await super().on_start()
        self._startup_stage = "ready"

    async def on_stop(self):
        references = [ref.pair for ref in self.config.references]
        if references:
            await self.market_data_provider.release_order_books(
                self.config.reference_connector, references, self)
        super().on_stop()

    def get_price_and_amount(self, level_id):
        price, amount = super().get_price_and_amount(level_id)
        if self._bootstrap is not None and not self._bootstrap.market_qualified:
            side = self.get_trade_type_from_level_id(level_id)
            _, allocations = self.config.get_spreads_and_amounts_in_quote(side)
            with localcontext() as context:
                context.rounding = ROUND_DOWN
                amount = (Decimal(allocations[int(self.get_level_from_level_id(level_id))])
                          * self.config.quote_guards.bootstrap.max_quote / self.config.total_amount_quote / price)
        return price, amount

    def get_executor_config(self, level_id, price, amount):
        if not self.quote_price_valid(price):
            return None
        return super().get_executor_config(level_id, price, amount).model_copy(update={
            "mm_session_id": self.config.mm_session_id, "mm_generation": self.config.mm_generation,
            "use_configured_entry_price": self._bootstrap is not None and not self._bootstrap.market_qualified})
