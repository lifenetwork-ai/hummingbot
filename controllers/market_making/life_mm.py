"""LIFE reference configuration on the existing simple V2 market maker."""
from decimal import Decimal
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from controllers.market_making import pmm_simple
from hummingbot.core.data_type.common import OrderType, PositionMode, PositionSide, PriceType, TradeType
from hummingbot.core.utils.async_utils import safe_ensure_future
from hummingbot.strategy_v2.models.executor_actions import StopExecutorAction


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

    @model_validator(mode="after")
    def validate_mm_policy(self):
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
        super().__init__(config, *args, **kwargs)
        self._life_anchor = config.saved_life_anchor
        self._source_anchors = dict(config.saved_source_anchors)
        self._stops_requested = set()
        self._account_ready = config.connector_name == "okx"
        self._session_admitted = config.mm_session_id is None

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

    def update_reference(self):
        try:
            if not self._account_ready:
                raise ValueError("perpetual_account_not_confirmed")
            life_price = self._fresh_price(self.config.connector_name, self.config.trading_pair, self.config.initial_anchor)
            sources = {ref.pair: self._fresh_price(self.config.reference_connector, ref.pair)
                       for ref in self.config.references}
            life_anchor = self._life_anchor or life_price
            source_anchors = self._source_anchors or sources
            reference = life_anchor * (1 + sum(
                ref.weight * (sources[ref.pair] / source_anchors[ref.pair] - 1) for ref in self.config.references))
            if not reference.is_finite() or not self.config.min_price <= reference <= self.config.max_price:
                raise ValueError("reference_out_of_bounds")
            self.processed_data = {"reference_price": reference, "spread_multiplier": Decimal("1"), "ready": False}
            for level in self.get_not_active_levels_ids([]):
                price, _ = self.get_price_and_amount(level)
                if not self.config.min_price <= price <= self.config.max_price:
                    raise ValueError("quote_out_of_bounds")
            # Capture the complete basket together; a missing member never saves partial anchors.
            self._life_anchor, self._source_anchors = life_anchor, dict(source_anchors)
            self.processed_data["ready"] = True
        except Exception as error:
            self.processed_data = {"ready": False, "reason": str(error)}

    async def update_processed_data(self):
        self.update_reference()

    async def control_task(self):
        # Run freshness/cancellation even when the generic provider becomes unready.
        await self.update_processed_data()
        if (not self._session_admitted or not self.market_data_provider.ready or not self.processed_data["ready"]
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
        # Include not-yet-placed entries. Partial fills may be counted conservatively
        # until their executor enters is_trading; never net away opposing exposure.
        for executor in self.executors_info:
            if executor.is_active and not executor.is_trading:
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

    async def on_start(self):
        if self.config.connector_name == "okx_perpetual":
            connector = self.market_data_provider.get_connector_with_fallback(self.config.connector_name)
            async with connector._set_position_mode_lock:
                current_mode = await connector._fetch_account_position_mode()
                if current_mode is None:
                    raise ValueError("Perpetual account mode is unavailable")
                if current_mode != PositionMode.HEDGE:
                    if connector.account_positions or connector.in_flight_orders:
                        raise ValueError("Cannot change account mode with existing orders or positions")
                    success, reason = await connector._trading_pair_position_mode_set(
                        PositionMode.HEDGE, self.config.trading_pair)
                    if not success:
                        raise ValueError(f"Perpetual account mode rejected: {reason}")
                    if await connector._fetch_account_position_mode() != PositionMode.HEDGE:
                        raise ValueError("Perpetual account mode was not confirmed")
                connector._perpetual_trading.set_position_mode(PositionMode.HEDGE)
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
                await source._update_trading_rules()
            result = await self.market_data_provider.acquire_order_books(self.config.reference_connector, references, self)
            if not all(result.get(pair, False) for pair in references):
                await self.market_data_provider.release_order_books(self.config.reference_connector, references, self)
                raise ValueError("Reference order books are unavailable")
        await super().on_start()

    def on_stop(self):
        references = [ref.pair for ref in self.config.references]
        if references:
            safe_ensure_future(self.market_data_provider.release_order_books(
                self.config.reference_connector, references, self))
        super().on_stop()

    def get_executor_config(self, level_id, price, amount):
        if not self.config.min_price <= price <= self.config.max_price:
            return None
        return super().get_executor_config(level_id, price, amount).model_copy(update={
            "mm_session_id": self.config.mm_session_id, "mm_generation": self.config.mm_generation})
