"""LIFE reference configuration on the existing simple V2 market maker."""
import asyncio
import math
import time
from copy import deepcopy
from decimal import ROUND_DOWN, Decimal, localcontext
from typing import Dict, List, Literal, Optional

from async_timeout import timeout
from pydantic import BaseModel, ConfigDict, Field, model_serializer, model_validator

from controllers.market_making import pmm_simple
from hummingbot.core.data_type.common import OrderType, PositionMode, PositionSide, PriceType, TradeType
from hummingbot.core.data_type.in_flight_order import OrderState
from hummingbot.core.engine_diagnostics import trace_runtime
from hummingbot.core.mm_readiness import MM_READINESS_REASONS
from hummingbot.core.utils.read_retry import retry_delay, transient_read
from hummingbot.data_feed.candles_feed.data_types import CandlesConfig
from hummingbot.strategy_v2.models.base import RunnableStatus
from hummingbot.strategy_v2.models.executor_actions import StopExecutorAction
from hummingbot.strategy_v2.models.executors import CloseType
from hummingbot.strategy_v2.utils.inventory_skew import InventorySkewConfig, inventory_skew_ratios
from hummingbot.strategy_v2.utils.listing_bootstrap import ListingBootstrap
from hummingbot.strategy_v2.utils.mm_spread import SpreadPolicy, SpreadPolicyConfig
from hummingbot.strategy_v2.utils.perpetual_reduction import InventoryReductionConfig
from hummingbot.strategy_v2.utils.perpetual_reduction_runtime import PerpetualReductionMixin, quote_price
from hummingbot.strategy_v2.utils.quote_guard import (
    MarketObservation,
    QuoteGuard,
    QuoteGuardConfig,
    SidewaysRange,
    observe_market,
)


class _ExecutorUpdateEvent(asyncio.Event):
    """Keep report readiness distinct from a coalesced request to run sooner."""

    def __init__(self, progress):
        super().__init__()
        self.progress = progress
        self.revision = 0

    def set(self):
        super().set()
        self.revision += 1
        self.progress.set()


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
    inventory_skew: InventorySkewConfig = Field(default_factory=InventorySkewConfig)
    spread_policy: SpreadPolicyConfig = Field(default_factory=SpreadPolicyConfig)
    inventory_reduction: InventoryReductionConfig = Field(default_factory=InventoryReductionConfig)
    quote_refresh_max_delay_seconds: int = Field(
        default=0, ge=0, le=10, strict=True,
        description="Optional bounded delay for rolling ordinary refresh; zero preserves batch refresh. Safety withdrawals are immediate.")

    @model_serializer(mode='wrap')
    def serialize_refresh_policy(self, handler):
        values = handler(self)
        # Keep the default catalogue/session snapshot compatible with readers
        # that predate this opt-in ordinary-refresh policy.
        if not self.quote_refresh_max_delay_seconds:
            values.pop('quote_refresh_max_delay_seconds', None)
        if self.inventory_reduction.mode == 'disabled':
            values.pop('inventory_reduction', None)
        return values

    @model_validator(mode="after")
    def validate_mm_policy(self):
        if self.inventory_reduction.mode == 'close_first' and (
                self.connector_name != 'okx_perpetual' or not self.quote_guards.enabled
                or not self.spread_policy.enabled or self.initial_positions):
            raise ValueError('Close-first requires guarded perpetual quotes and native recorded positions')
        if self.quote_refresh_max_delay_seconds > self.executor_refresh_time:
            raise ValueError('Refresh delay cannot exceed the ordinary refresh interval')
        vol = self.spread_policy.volatility
        if vol.defense is not None:
            if not self.quote_guards.enabled:
                raise ValueError('Volatility defense requires enabled quote guards')
            if vol.on_unavailable == 'static' and any(
                    fallback < Decimal(str(base)) for bases, fallbacks in
                    ((self.buy_spreads, vol.fallback_buy_spreads), (self.sell_spreads, vol.fallback_sell_spreads))
                    for base, fallback in zip(bases, fallbacks)):
                raise ValueError('Volatility defense fallback cannot narrow base spreads')
        if self.spread_policy.enabled:
            for spreads in (self.buy_spreads, self.sell_spreads):
                if spreads != sorted(spreads):
                    raise ValueError("Spread policy requires ordered base levels")
        if vol.enabled:
            if (vol.on_unavailable == "static" and
                    (len(vol.fallback_buy_spreads) != len(self.buy_spreads)
                     or len(vol.fallback_sell_spreads) != len(self.sell_spreads))):
                raise ValueError("Fallback must cover every configured quote level")
            if any(Decimal(str(x)) * vol.max_multiplier >= 1 for x in self.buy_spreads + self.sell_spreads):
                raise ValueError("Maximum dynamic spread must remain below one")
        if self.inventory_skew.enabled and self.connector_name != "okx":
            raise ValueError("Inventory skew is a spot-only account cash signal")
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


class LifeMMController(PerpetualReductionMixin, pmm_simple.PMMSimpleController):
    STARTUP_OPERATION_TIMEOUT = 30
    manages_account_settings = True

    def __init__(self, config: LifeMMConfig, *args, **kwargs):
        if config.inventory_reduction.mode == 'close_first' and (not config.runtime_config_managed or not config.mm_session_id):
            raise ValueError('Close-first requires a managed PostgreSQL MM session')
        if config.spread_policy.volatility.defense is not None and (not config.runtime_config_managed or not config.mm_session_id):
            raise ValueError('Volatility defense requires managed PostgreSQL MM session ownership')
        if config.quote_guards.mode == "bootstrap_then_market" and (not config.runtime_config_managed or not config.mm_session_id):
            raise ValueError("Listing bootstrap requires managed MM session ownership")
        super().__init__(config, *args, **kwargs)
        self._executor_progress = asyncio.Event() if config.runtime_config_managed and config.mm_session_id else None
        if self._executor_progress is not None:
            self.executors_update_event = _ExecutorUpdateEvent(self._executor_progress)
        self._bootstrap = (ListingBootstrap(config.quote_guards, anchor=config.initial_anchor, sideways_range=config.sideways_range,
                                            max_gap_seconds=config.max_data_age_seconds)
                           if config.quote_guards.mode == "bootstrap_then_market" else None)
        self._inventory_factors = (Decimal("1"), Decimal("1"), Decimal("1"))
        self._inventory_status = None
        self._life_anchor = config.saved_life_anchor
        self._source_anchors = dict(config.saved_source_anchors)
        self._stops_requested = set()
        self._account_ready = config.connector_name == "okx"
        self._session_admitted = config.mm_session_id is None
        self._startup_retry = None
        self._startup_stage = "not_started"
        self._startup_retry_sleep = asyncio.sleep
        self._quote_guard = QuoteGuard(config.quote_guards, config.sideways_range)
        self._spread_policy = SpreadPolicy(config.spread_policy)
        self._volatility_saved_pause = None
        self._volatility_checkpoint = None
        self._last_defense_log = None
        self._effective_spreads = None
        self._quote_diagnostics = None
        self.initialize_reduction()

    @property
    def quote_diagnostics(self):
        """Last proposal evaluation; it is not exchange admission or a live balance check."""
        return deepcopy(self._quote_diagnostics)

    def _reset_quote_diagnostics(self):
        self._reduction_status_cache = None
        self._quote_diagnostics = dict(observed_at=self.market_data_provider.time(),
                                       reasons={'BUY': [], 'SELL': []}, proposed={'BUY': 0, 'SELL': 0})

    def _quote_reason(self, reason, side=None):
        if self._quote_diagnostics is not None:
            for name in (side.name,) if side is not None else ('BUY', 'SELL'):
                reasons = self._quote_diagnostics['reasons'][name]
                if reason not in reasons:
                    reasons.append(reason)

    def _quote_readiness_reason(self):
        if self._control_error:
            return 'controller_unavailable'
        if not self._session_admitted:
            return 'session_not_admitted'
        if not self.market_data_provider.ready:
            return 'market_data_unavailable'
        if not self.processed_data.get('ready'):
            reason = str(self.processed_data.get('reason') or '').split(':', 1)[0]
            return reason if reason in MM_READINESS_REASONS else 'readiness_unavailable'
        return None

    def _spread_candle_config(self):
        cfg = self.config.spread_policy.volatility
        return CandlesConfig(connector=self.config.connector_name, trading_pair=self.config.trading_pair,
                             interval=cfg.interval, max_records=cfg.length + 102)

    def initialize_candles(self):
        # Optional candles cannot make the shared provider globally unready:
        # this controller applies the explicit static/wait decision itself.
        if self.config.spread_policy.volatility.enabled:
            self.market_data_provider.acquire_candle_feed(self._spread_candle_config(), self)

    def spread_quote_valid(self, side, price, amount, *, level_id=None):
        if not self.config.spread_policy.enabled:
            return True
        if not self.processed_data.get("ready"):
            return False
        now = self.market_data_provider.time()
        if self._spread_policy.multiplier(now) is None and self.config.spread_policy.volatility.on_unavailable == "wait":
            return False
        reference = self.processed_data["reference_price"]
        spreads = self._effective_spreads[0 if side == TradeType.BUY else 1]
        actual = (reference - price if side == TradeType.BUY else price - reference) / reference
        connector = self.market_data_provider.get_connector_with_fallback(self.config.connector_name)
        if self._spread_policy.defense is not None:
            if level_id not in self.get_not_active_levels_ids([]) or self.get_trade_type_from_level_id(level_id) != side:
                return False
            index = int(self.get_level_from_level_id(level_id))
            target_price, target_amount = self.get_price_and_amount(level_id)
            if actual < spreads[index] or amount * price > target_price * target_amount:
                return False
        return actual >= min(spreads) and self._spread_policy.quote_valid(
            connector, self.config.trading_pair, side, price, amount, reference)

    def bind_volatility_defense(self, *, paused, checkpoint):
        self._volatility_saved_pause = paused
        self._volatility_checkpoint = checkpoint
        if paused:
            self._spread_policy.defense.state = 'paused'
        else:
            # A cold controller/profile cannot jump past reduced recovery.
            # It may quote conservatively while fresh low-volatility samples
            # qualify normal size; no extra persistent state is needed.
            self._spread_policy.defense.state = 'reduced'
        self._spread_policy.defense.reset_recovery()

    def sync_volatility_checkpoint(self):
        if self._spread_policy.defense is None:
            return True
        paused = self._spread_policy.defense.state == 'paused'
        if self._volatility_saved_pause is None or self._volatility_checkpoint is None:
            return False
        if self._volatility_saved_pause == paused:
            return True
        try:
            if not self._volatility_checkpoint(paused):
                return False
        except Exception:
            self.logger().error('MM volatility checkpoint failed controller=%s', self.config.id,
                                extra={'event': 'mm_volatility_checkpoint_failed', 'scope': self.config.id})
            raise
        self._volatility_saved_pause = paused
        return True

    def _spread_status(self, *, reason=None, sample=False):
        now = self.market_data_provider.time()
        result = self._spread_policy.status(now)
        defense = self._spread_policy.defense
        if defense is None:
            return result
        pending = self._volatility_saved_pause is None or self._volatility_saved_pause != (defense.state == 'paused')
        state = defense.state
        if state != 'paused':
            if pending:
                state, reason = 'checkpoint_pending', 'volatility_checkpoint_pending'
            elif reason is not None:
                state = 'waiting'
            elif self._spread_policy.multiplier(now) is None:
                state, reason = ('static_fallback' if self.config.spread_policy.volatility.on_unavailable == 'static' else 'waiting'), 'candles_unavailable'
        else:
            reason = 'volatility_paused'
        labels = dict(normal='Normal order sizing', reduced='Trading with smaller orders',
                      paused='Waiting for volatility to settle', waiting='Waiting for market data',
                      static_fallback='Trading with smaller orders using fallback spreads',
                      checkpoint_pending='Waiting for recovery state confirmation')
        factor = self._spread_policy.size_factor(now) if state in ('normal', 'reduced', 'static_fallback') else Decimal('0')
        value = dict(state=state, reason_code=reason, label=labels[state],
                     next_action=('Monitor working orders and inventory.' if state in ('normal', 'reduced', 'static_fallback') else
                                  'Wait for fresh market data to pass recovery checks. Do not restart to bypass this wait.'),
                     automatic_recovery_expected=state != 'normal' and self._control_error is None,
                     size_factor=factor, effective_allocation_quote=self._quote_budget() if factor else Decimal('0'),
                     evaluated_at=now, recovery_samples=defense.good_samples,
                     recovery_elapsed_seconds=max(0, time.monotonic() - defense.good_since) if defense.good_since is not None else 0,
                     paused_latch_persisted=self._volatility_saved_pause,
                     cancellation_pending=any(executor.id in self._stops_requested or getattr(executor, 'status', None) == RunnableStatus.SHUTTING_DOWN
                                              for executor in self.executors_info))
        result['defense'] = value
        signature = (state, reason)
        if sample and signature != self._last_defense_log:
            log = self.logger().info if state == 'normal' else self.logger().warning
            log('MM volatility defense controller=%s state=%s reason=%s', self.config.id, state, reason or '-',
                extra={'event': 'mm_volatility_defense_changed', 'scope': self.config.id, 'reason_code': reason or state})
            self.logger().debug('MM volatility sizing controller=%s natr=%s factor=%s', self.config.id, result['raw_natr'], factor)
            self._last_defense_log = signature
        return result

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

    @property
    def session_admitted(self):
        return self._session_admitted

    def revoke_session(self):
        self._session_admitted = False

    def prepare_session_anchors(self, source_anchors=None):
        """Prepare a copied basket; the host persists all markets before admission."""
        self._life_anchor = None
        self._source_anchors = dict(source_anchors or {})
        self.update_reference()
        if not (self.processed_data.get("ready") or (self.processed_data.get("quote_guard") or {}).get("can_admit")):
            return None
        return dict(self._source_anchors)

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
                # Read both sides before deciding: an empty side must not hide
                # a failure reading the other side. No complete book/DataFrames.
                bid = next(iter(book.bid_entries()), None)
                ask = next(iter(book.ask_entries()), None)
                if bid is None or ask is None:
                    return initial_anchor
            raise ValueError(f"invalid_book:{connector_name}:{pair}")
        return (bid + ask) / 2

    def _inventory_valuation_price(self, guard_status):
        if guard_status is not None:
            bootstrap = guard_status.get("bootstrap")
            if not guard_status["ready"] or (bootstrap and not bootstrap["market_qualified"]):
                raise ValueError("Inventory skew requires a qualified outside price")
            return guard_status["market_mid"]
        # Reuse bounded known-order filtering even if the price guard is off.
        # Neither own quotes nor a model/session/initial anchor value inventory.
        connector = self.market_data_provider.get_connector_with_fallback(self.config.connector_name)
        book = self.market_data_provider.get_order_book(self.config.connector_name, self.config.trading_pair)
        value = observe_market(connector, book, self.config.trading_pair, now=self.market_data_provider.time(),
                               max_age_seconds=self.config.max_data_age_seconds,
                               max_levels=self.config.quote_guards.max_book_levels,
                               max_orders=self.config.quote_guards.max_own_orders)
        if value.market_mid is None:
            raise ValueError("Inventory valuation requires outside two-sided depth")
        return value.market_mid

    def _update_inventory_skew(self, guard_status):
        if not self.config.inventory_skew.enabled:
            return
        connector = self.market_data_provider.get_connector_with_fallback(self.config.connector_name)
        base_asset = self.config.trading_pair.split("-")[0]
        # Existing mandatory engine balances still fence admission. Optional
        # cash metadata/ratio failures below must not conceal corrupt balances.
        balances = [Decimal(str(connector.get_balance(asset))) for asset in (base_asset, "USDT")]
        if any(not value.is_finite() or value < 0 for value in balances):
            raise ValueError("account_balance_unavailable")
        now = self.market_data_provider.time()
        from hummingbot.connector.exchange_py_base import ExchangePyBase
        maximum_age = 2 * ExchangePyBase.LONG_POLL_INTERVAL
        observations = getattr(connector, "account_balance_observations", {})
        # A known stale account observation invalidates its engine balance too.
        # Absent optional cash metadata alone still permits neutral fallback.
        if isinstance(observations, dict):
            for asset in (base_asset, "USDT"):
                snapshot = observations.get(asset)
                stamp = snapshot.get("observed_at") if isinstance(snapshot, dict) else None
                if stamp is not None:
                    try:
                        fresh = -1 <= now - float(stamp) <= maximum_age
                    except (ValueError, TypeError, OverflowError):
                        fresh = False
                    if not fresh:
                        raise ValueError("account_balance_unavailable")
        self._inventory_status = dict(state="neutral", reason_code="inventory_skew_unavailable",
                                      scope="account_cash", base_pct=None, valuation_price=None, cash_quote=None, base_cash=None,
                                      buy_multiplier=Decimal("1"), sell_multiplier=Decimal("1"), budget_scale=Decimal("1"),
                                      balance_observed_at=None, evaluated_at=now)
        try:
            observations = connector.account_balance_observations
            base, quote = observations[base_asset], observations["USDT"]
            # Reuse the connector's normal 120s REST fallback cadence; a quiet
            # account must not become stale on a ten-second market-price timer.
            stamps = [float(value["observed_at"]) for value in (base, quote)]
            if any(not -1 <= now - stamp <= maximum_age for stamp in stamps):
                raise ValueError("Inventory cash observations are stale")
            base, quote = Decimal(str(base["cash_balance"])), Decimal(str(quote["cash_balance"]))
            price = self._inventory_valuation_price(guard_status)
            levels = [(self.get_trade_type_from_level_id(level), *self._baseline_price_and_amount(level))
                      for level in self.get_not_active_levels_ids([])]
            base_range = sum((amount for _, _, amount in levels), Decimal("0")) * self.config.inventory_skew.inventory_range_multiplier
            buy, sell = inventory_skew_ratios(base, quote, price, self.config.inventory_skew.target_base_pct, base_range)
            proposed = sum((p * amount * (buy if side == TradeType.BUY else sell) for side, p, amount in levels), Decimal("0"))
            with localcontext() as context:
                context.rounding = ROUND_DOWN
                scale = min(Decimal("1"), self._quote_budget() / proposed) if proposed > 0 else Decimal("1")
            self._inventory_factors = (buy, sell, scale)
            self._inventory_status.update(state="active", reason_code=None, base_pct=base * price / (base * price + quote),
                                          valuation_price=price, cash_quote=quote, base_cash=base, buy_multiplier=buy, sell_multiplier=sell,
                                          budget_scale=scale, balance_observed_at=min(stamps))
        except Exception:
            # This optional enhancement never restarts the controller or weakens
            # the ordinary balance, exposure, price or owner/send checks.
            pass
        self.processed_data["inventory_skew"] = self._inventory_status

    def _quote_budget(self):
        base = (self.config.quote_guards.bootstrap.max_quote
                if self._bootstrap is not None and not self._bootstrap.market_qualified else self.config.total_amount_quote)
        return base * self._spread_policy.size_factor(self.market_data_provider.time())

    def update_reference(self, *, sample_guard=False):
        guard_status = None
        self._inventory_factors = (Decimal("1"), Decimal("1"), Decimal("1"))
        self._inventory_status = None
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
            if self.config.spread_policy.enabled:
                provider = self.market_data_provider
                cfg = self.config.spread_policy.volatility
                if cfg.defense is not None and self._volatility_saved_pause is None:
                    raise ValueError('volatility_checkpoint_pending')
                if sample_guard and cfg.enabled:
                    candle = self._spread_candle_config()
                    key = provider._generate_candle_feed_key(candle)
                    # Use existing feed only. Readiness/send checks never create/fetch a feed.
                    self._spread_policy.refresh(provider.candles_feeds.get(key), provider.time())
                    self._spread_policy.sample_defense(provider.time(), time.monotonic())
                if cfg.defense is not None:
                    if self._spread_policy.defense.state == 'paused':
                        raise ValueError('volatility_paused')
                    if self._volatility_saved_pause is not False:
                        raise ValueError('volatility_checkpoint_pending')
                connector = provider.get_connector_with_fallback(self.config.connector_name)
                buy, buy_quote = self.config.get_spreads_and_amounts_in_quote(TradeType.BUY)
                sell, sell_quote = self.config.get_spreads_and_amounts_in_quote(TradeType.SELL)
                if cfg.defense is not None:
                    scale = self._quote_budget() / self.config.total_amount_quote
                    buy_quote, sell_quote = ([value * scale for value in allocations] for allocations in (buy_quote, sell_quote))
                self._effective_spreads = self._spread_policy.compose(
                    connector, self.config.trading_pair, reference, buy, sell, buy_quote, sell_quote, now=provider.time())
            self._update_inventory_skew(guard_status)
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
            if self.config.spread_policy.enabled:
                self.processed_data['spread_policy'] = self._spread_status(reason=self.processed_data.get('reason'), sample=sample_guard)
        except Exception as error:
            reason = str(error).split(":", 1)[0]
            if self._spread_policy.defense is not None:
                if sample_guard and reason not in ('volatility_paused', 'volatility_checkpoint_pending'):
                    self._spread_policy.defense.reset_recovery()
                if guard_status is not None:
                    guard_status = {**guard_status, 'can_admit': False}
            if self.config.quote_guards.enabled and guard_status is None:
                guard_status = self._evaluate_guard(
                    MarketObservation(None, None, self.market_data_provider.time(), detail="required_data_unavailable"),
                    Decimal("1"), False)
                guard_status["reason_code"] = reason if reason in MM_READINESS_REASONS else "market_price_unavailable"
            self.processed_data = {"ready": False, "reason": str(error) if reason in MM_READINESS_REASONS else type(error).__name__,
                                   "spread_multiplier": spread_multiplier, "quote_guard": guard_status,
                                   "inventory_skew": self._inventory_status,
                                   "spread_policy": self._spread_status(reason=reason, sample=sample_guard) if self.config.spread_policy.enabled else None,
                                   "internal_error": not isinstance(error, (ValueError, ArithmeticError, OSError))}

    async def update_processed_data(self):
        self.update_reference(sample_guard=True)

    @trace_runtime('controller.cycle')
    async def control_task(self):
        if self._executor_progress is not None:
            self._executor_progress.clear()
        # Run freshness/cancellation even when the generic provider becomes unready.
        await self.update_processed_data()
        report_ready = self.executors_update_event.is_set()
        if (self._control_error or not self._session_admitted or not self.market_data_provider.ready or not self.processed_data["ready"]
                or (not report_ready and not self.exposure_within_caps()
                    and not (self.reduction_enabled and self.reduction_ready()))):
            self._reset_quote_diagnostics()
            self._quote_reason(self._quote_readiness_reason() or 'inventory_limit')
            await self.send_actions(self.stop_quote_actions())
        elif report_ready:
            await self.send_actions(self.determine_executor_actions())
        # Publish cancellation requests before the synchronous, owner-fenced
        # transition write. A failed/pending write never reopens admission.
        if self._spread_policy.defense is not None:
            previous = self._volatility_saved_pause
            if self.sync_volatility_checkpoint() and previous != self._volatility_saved_pause:
                self.update_reference()

    async def _wait_for_control_tick(self):
        if self._executor_progress is None:
            return await super()._wait_for_control_tick()
        try:
            await asyncio.wait_for(self._executor_progress.wait(), timeout=self.update_interval)
        except asyncio.TimeoutError:
            pass  # Periodic freshness and cancellation checks still run without publications.

    async def send_actions(self, executor_actions):
        event = self.executors_update_event
        revision = event.revision if isinstance(event, _ExecutorUpdateEvent) else None
        await super().send_actions(executor_actions)
        # The generic sender consumes readiness after put(). A publication
        # arriving during a blocked handoff belongs to the next iteration.
        if revision is not None and event.revision != revision:
            event.set()

    def stop_quote_actions(self):
        active = {executor.id for executor in self.executors_info if executor.is_active}
        if self.reduction_enabled:
            try:
                active.update(row.config.id for row in self._reduction_runtime().executors
                              if row.status in (RunnableStatus.NOT_STARTED, RunnableStatus.RUNNING))
            except (ValueError, ArithmeticError, TypeError, AttributeError):
                pass
        self._stops_requested.intersection_update(active)
        actions = [StopExecutorAction(controller_id=self.config.id, executor_id=executor_id, keep_position=True)
                   for executor_id in active - self._stops_requested]
        self._stops_requested.update(active)
        return actions

    def determine_executor_actions(self):
        if self.reduction_enabled:
            if self._control_error or not self._session_admitted or not self.processed_data.get('ready') or not self.market_data_provider.ready:
                return self.stop_quote_actions()
            try:
                safety = self._reduction_refresh_actions()
                if safety:
                    return safety
                refresh = self._completed_entry_actions() + self._defense_refresh_actions()
                return refresh or (self._create_reduction_actions() + self.stop_actions_proposal())
            except (ValueError, ArithmeticError, AttributeError, TypeError) as error:
                self._reset_quote_diagnostics()
                self._quote_reason(str(error) if str(error).startswith('reduction_') else 'reduction_basis_unavailable')
                return self.stop_quote_actions()
        exposure, within_caps = None, False
        if self._session_admitted and self.processed_data.get("ready"):
            try:
                exposure = self._exposure()
                within_caps = self._within_caps(*exposure)
            except (ValueError, ArithmeticError, TypeError):
                pass
        if not within_caps:
            self._reset_quote_diagnostics()
            self._quote_reason(self._quote_readiness_reason() or 'inventory_limit')
            return self.stop_quote_actions()
        refresh = self._completed_entry_actions() + self._defense_refresh_actions()
        if refresh or (self._spread_policy.defense is not None and self._stops_requested):
            return refresh
        # Preserve the upstream create-then-stop ordering. Pass a local snapshot
        # only along this synchronous proposal call; public/send gates stay fresh
        # and no snapshot survives the queue await in control_task.
        return self._create_actions_proposal(exposure) + self.stop_actions_proposal()

    def _completed_entry_actions(self):
        # A fully filled entry has no resting liquidity. Transfer it through the
        # existing hold/shutdown path instead of occupying its level until age60.
        # Fresh native account observations must cover the fill before the entry
        # reservation is released; missing data keeps the ordinary refresh path.
        resident = {executor.id for executor in self.executors_info
                    if executor.is_active or executor.status == RunnableStatus.SHUTTING_DOWN}
        self._stops_requested.intersection_update(resident)
        actions = []
        for executor in self.executors_info:
            if not executor.is_active or executor.id in self._stops_requested:
                continue
            info = getattr(executor, 'custom_info', None)
            if not isinstance(info, dict) or info.get('entry_order_filled') is not True:
                continue
            if executor.config.type != 'position_executor':
                continue
            barriers = executor.config.triple_barrier_config
            if any(getattr(barriers, field) is not None for field in ('stop_loss', 'take_profit', 'time_limit', 'trailing_stop')):
                continue
            stamp = info.get('entry_order_last_fill_timestamp')
            if not self._entry_account_covers_fill(executor, stamp):
                continue
            self._stops_requested.add(executor.id)
            actions.append(StopExecutorAction(controller_id=self.config.id, executor_id=executor.id, keep_position=True))
            self.logger().debug('Completed entry handed off for fresh quotes: executor_id=%s level=%s',
                                executor.id, executor.config.level_id,
                                extra={'event': 'mm_entry_handoff', 'reason_code': 'entry_filled', 'scope': 'controller'})
        return actions

    def _entry_account_covers_fill(self, executor, fill_at):
        now = self.market_data_provider.time()

        def fresh(observation):
            stamp = observation.get('observed_at') if isinstance(observation, dict) else None
            return (isinstance(stamp, (float, int)) and not isinstance(stamp, bool) and math.isfinite(stamp)
                    and stamp >= fill_at and 0 <= now - stamp <= self.config.max_data_age_seconds)

        if not isinstance(fill_at, (float, int)) or isinstance(fill_at, bool) or not math.isfinite(fill_at) or fill_at <= 0:
            return False
        connector = self.market_data_provider.get_connector_with_fallback(self.config.connector_name)
        if self.config.connector_name == 'okx':
            observations = connector.account_balance_observations
            for asset in self.config.trading_pair.split('-'):
                observation = observations.get(asset) if isinstance(observations, dict) else None
                if not fresh(observation):
                    return False
                cash = observation.get('cash_balance')
                if not isinstance(cash, Decimal) or not cash.is_finite() or cash < 0:
                    return False
            return True
        side = PositionSide.LONG if executor.config.side == TradeType.BUY else PositionSide.SHORT
        keys = [key for key, position in connector.account_positions.items()
                if position.trading_pair == self.config.trading_pair and position.position_side == side]
        observations = connector.position_observations
        return len(keys) == 1 and isinstance(observations, dict) and fresh(observations.get(keys[0]))

    def _resting_quote_amount(self, executor):
        # Only quote validity uses the acknowledged remainder. Account exposure
        # and pending allocation must continue reserving the full config amount.
        config = executor.config
        try:
            identities = (getattr(executor, 'custom_info', None) or {}).get('order_ids', [])
            if not isinstance(identities, (list, tuple)) or not 1 <= len(identities) <= 3:
                return config.amount
            connector = self.market_data_provider.get_connector_with_fallback(config.connector_name)
            orders = [connector.in_flight_orders.get(identity) for identity in dict.fromkeys(identities)]
            matches = [order for order in orders if order is not None and order.trading_pair == config.trading_pair
                       and order.trade_type == config.side]
            if len(matches) == 1:
                order = matches[0]
                amount, filled = order.amount, order.executed_amount_base
                if (order.exchange_order_id and order.current_state in (OrderState.OPEN, OrderState.PARTIALLY_FILLED)
                        and order.price == quote_price(config) and amount.is_finite() and filled.is_finite()
                        and 0 < filled < amount <= config.amount):
                    return amount - filled
        except (AttributeError, ArithmeticError, TypeError, ValueError):
            pass
        # Missing, ambiguous or inconsistent entry metadata cannot reduce size.
        return config.amount

    def _defense_refresh_actions(self):
        if self._spread_policy.defense is None:
            return []
        resident = {executor.id for executor in self.executors_info
                    if executor.is_active or executor.status == RunnableStatus.SHUTTING_DOWN}
        self._stops_requested.intersection_update(resident)
        actions = []
        for executor in self.executors_info:
            if executor.is_active and executor.id not in self._stops_requested and not self.spread_quote_valid(
                    executor.config.side, quote_price(executor.config), self._resting_quote_amount(executor),
                    level_id=executor.config.level_id):
                self._stops_requested.add(executor.id)
                actions.append(StopExecutorAction(controller_id=self.config.id, executor_id=executor.id, keep_position=True))
        return actions

    def get_levels_to_execute(self):
        if self.reduction_enabled:
            return self._reduction_levels()
        levels = super().get_levels_to_execute()
        # An entry retiring for repricing (or awaiting cancel confirmation) still
        # owns its level until terminal bookkeeping completes, without defense.
        pending_levels = set()
        for executor in self.executors_info:
            retiring = executor.status == RunnableStatus.SHUTTING_DOWN
            if retiring:
                pending_levels.add(executor.config.level_id)
            if executor.is_active or retiring:
                self._quote_reason('active_executor', executor.config.side)
            elif (executor.close_type == CloseType.STOP_LOSS
                  and self.market_data_provider.time() - executor.close_timestamp < self.config.cooldown_time):
                self._quote_reason('cooldown', executor.config.side)
        return [level for level in levels if level not in pending_levels]

    def _exposure(self):
        if self.reduction_enabled:
            return self._reduction_exposure()
        connector = self.market_data_provider.get_connector_with_fallback(self.config.connector_name)
        if self.config.connector_name == "okx":
            long = Decimal(str(connector.get_balance(self.config.trading_pair.split("-")[0])))
            # Validate the account observation before positive entry reservations
            # can mask an invalid negative cash balance.
            if not long.is_finite() or long < 0:
                raise ValueError("Invalid account exposure")
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

    def exposure_within_caps(self):
        try:
            return self._within_caps(*self._exposure())
        except (ValueError, ArithmeticError, TypeError):
            return False

    def create_actions_proposal(self):
        if self.reduction_enabled:
            from hummingbot.core.order_submission_guard import recovery_side_permitted
            if not recovery_side_permitted(self.config.connector_name, TradeType.SELL):
                self._reset_quote_diagnostics()
                self._quote_reason('uncertain_order_risk_paused')
                return []
            return self._create_reduction_actions()
        return self._create_actions_proposal()

    def _create_actions_proposal(self, exposure=None):
        self._reset_quote_diagnostics()
        if not self._session_admitted or not self.processed_data.get("ready") or not self.market_data_provider.ready:
            self._quote_reason(self._quote_readiness_reason() or 'readiness_unavailable')
            return []
        try:
            long, short = self._exposure() if exposure is None else exposure
            if not self._within_caps(long, short):
                self._quote_reason('inventory_limit')
                return []
            connector = self.market_data_provider.get_connector_with_fallback(self.config.connector_name)
            available_quote = Decimal(str(connector.get_available_balance("USDT")))
            available_base = Decimal(str(connector.get_available_balance(self.config.trading_pair.split("-")[0]))) \
                if self.config.connector_name == "okx" else Decimal("0")
            if any(not value.is_finite() or value < 0 for value in (available_quote, available_base)):
                self._quote_reason('account_balance_unavailable')
                return []
            accepted = []
            pending_allocation = sum((executor.config.amount * executor.config.entry_price for executor in self.executors_info
                                      if executor.is_active or executor.status == RunnableStatus.SHUTTING_DOWN), Decimal("0")) \
                if self.config.inventory_skew.enabled or self._spread_policy.defense is not None else Decimal("0")
            if self._spread_policy.defense is not None and any(
                    executor.status == RunnableStatus.SHUTTING_DOWN for executor in self.executors_info):
                self._quote_reason('allocation_limit')
                return []
            for action in super().create_actions_proposal():
                config = action.executor_config
                from hummingbot.core.order_submission_guard import recovery_side_permitted
                if not recovery_side_permitted(self.config.connector_name, config.side):
                    self._quote_reason('uncertain_order_risk_paused', config.side)
                    continue
                if (self.config.inventory_skew.enabled or self._spread_policy.defense is not None) and pending_allocation + config.amount * config.entry_price > self._quote_budget():
                    self._quote_reason('allocation_limit', config.side)
                    continue
                is_buy = config.side == TradeType.BUY
                proposed_long = long + config.amount if is_buy else long
                proposed_short = short + config.amount if not is_buy and self.config.connector_name == "okx_perpetual" else short
                required_quote = config.amount * config.entry_price / config.leverage
                if self.config.runtime_config_managed and self.config.connector_name == "okx" and is_buy:
                    from hummingbot.core.order_submission_guard import spot_buy_preview
                    if not spot_buy_preview(connector, config.amount, config.entry_price):
                        self._quote_reason('spot_outflow_blocked', config.side)
                        continue
                if not self._within_caps(proposed_long, proposed_short):
                    self._quote_reason('inventory_limit', config.side)
                    continue
                if self.config.connector_name == "okx" and not is_buy:
                    if config.amount > available_base:
                        self._quote_reason('insufficient_base_balance', config.side)
                        continue
                    available_base -= config.amount
                else:
                    if required_quote > available_quote:
                        self._quote_reason('insufficient_quote_balance', config.side)
                        continue
                    available_quote -= required_quote
                long, short = proposed_long, proposed_short
                pending_allocation += config.amount * config.entry_price
                accepted.append(action)
                self._quote_diagnostics['proposed'][config.side.name] += 1
            return accepted
        except (ValueError, ArithmeticError, TypeError) as error:
            self._quote_diagnostics['proposed'] = {'BUY': 0, 'SELL': 0}
            self._quote_diagnostics['error_type'] = type(error).__name__
            self._quote_reason('proposal_evaluation_unavailable')
            return []

    def executors_to_refresh(self):
        now = self.market_data_provider.time()
        expired = [executor for executor in self.executors_info if executor.is_active
                   and now - executor.timestamp > self.config.executor_refresh_time]
        if not expired:
            return []
        delay = self.config.quote_refresh_max_delay_seconds
        if not delay:
            return [StopExecutorAction(controller_id=self.config.id, executor_id=executor.id, keep_position=True)
                    for executor in expired]
        active = [executor for executor in self.executors_info if executor.is_active]
        valid = set()
        for executor in active:
            order = self._acknowledged_refresh_entry(executor)
            if order is not None and self.spread_quote_valid(
                    executor.config.side, quote_price(executor.config), order.amount - order.executed_amount_base,
                    level_id=executor.config.level_id):
                valid.add(executor.id)
        mandatory = {executor.id for executor in expired
                     if (len(self.config.buy_spreads if executor.config.side == TradeType.BUY else self.config.sell_spreads) < 2
                         or now - executor.timestamp >= self.config.executor_refresh_time + delay or executor.id not in valid)}
        selected = [StopExecutorAction(controller_id=self.config.id, executor_id=executor.id, keep_position=True)
                    for executor in expired if executor.id in mandatory]
        sides = {executor.config.side for executor in expired if executor.id in mandatory}
        retiring = {executor.config.side for executor in self.executors_info
                    if executor.status == RunnableStatus.SHUTTING_DOWN}
        for executor in sorted(expired, key=lambda item: item.timestamp):
            config = executor.config
            # A retiring/pending level cannot protect another quote's removal.
            # Its original timestamp also bounds the wait if replacement stalls.
            if (executor.id in mandatory or config.side in sides or config.side in retiring or not any(
                    peer.id != executor.id and peer.config.side == config.side
                    and peer.id in valid and peer.id not in mandatory for peer in active)):
                continue
            selected.append(StopExecutorAction(controller_id=self.config.id, executor_id=executor.id, keep_position=True))
            sides.add(config.side)
        return selected

    def _acknowledged_refresh_entry(self, executor):
        """Only a live native entry can protect continuity; never make a read RPC."""
        try:
            config = executor.config
            identities = (executor.custom_info or {}).get('order_ids', [])
            if not isinstance(identities, (list, tuple)) or not 1 <= len(identities) <= 3:
                return None
            connector = self.market_data_provider.get_connector_with_fallback(config.connector_name)
            orders = [connector.in_flight_orders.get(identity) for identity in dict.fromkeys(identities)]
            matches = [order for order in orders if order is not None and order.trading_pair == config.trading_pair
                       and order.trade_type == config.side]
            if len(matches) == 1:
                order = matches[0]
                if (order.exchange_order_id and order.current_state in (OrderState.OPEN, OrderState.PARTIALLY_FILLED)
                        and order.price == quote_price(config) and order.amount.is_finite()
                        and order.executed_amount_base.is_finite()
                        and 0 <= order.executed_amount_base < order.amount <= config.amount):
                    return order
        except (AttributeError, ArithmeticError, TypeError, ValueError):
            pass
        return None

    async def _startup_once(self, operation):
        """One bounded startup await; account mutations must never be retried.

        Use the same task so cancellation cannot strand a successfully acquired
        lock in an unobserved child task. Completion is actual progress before
        the next startup stage; the ordinary control-loop deadline is unchanged.
        """
        self._control_progress_at = time.monotonic()
        self._control_wait_until = self._control_progress_at + self.STARTUP_OPERATION_TIMEOUT + 1
        try:
            async with timeout(self.STARTUP_OPERATION_TIMEOUT):
                return await operation()
        finally:
            self._control_progress_at = time.monotonic()
            self._control_wait_until = None

    async def _startup_read(self, operation):
        """Retry only the supplied read; never replay an enclosing account mutation."""
        attempt, first = 0, time.time()
        while True:
            try:
                result = await self._startup_once(operation)
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
                self._control_progress_at = time.monotonic()
                self._control_wait_until = None

    async def on_start(self):
        if self.config.connector_name == "okx_perpetual":
            connector = self.market_data_provider.get_connector_with_fallback(self.config.connector_name)
            self._startup_stage = 'position_mode_lock'
            await self._startup_once(connector._set_position_mode_lock.acquire)
            try:
                self._startup_stage = "position_mode_read"
                current_mode = await self._startup_read(connector._fetch_account_position_mode)
                if current_mode is None:
                    raise ValueError("Perpetual account mode is unavailable")
                if current_mode != PositionMode.HEDGE:
                    if connector.account_positions or connector.in_flight_orders:
                        raise ValueError("Cannot change account mode with existing orders or positions")
                    self._startup_stage = "position_mode_change"
                    success, reason = await self._startup_once(lambda: connector._trading_pair_position_mode_set(
                        PositionMode.HEDGE, self.config.trading_pair))
                    if not success:
                        raise ValueError(f"Perpetual account mode rejected: {reason}")
                    self._startup_stage = "position_mode_confirmation"
                    if await self._startup_read(connector._fetch_account_position_mode) != PositionMode.HEDGE:
                        raise ValueError("Perpetual account mode was not confirmed")
                connector._perpetual_trading.set_position_mode(PositionMode.HEDGE)
            finally:
                connector._set_position_mode_lock.release()
            self._startup_stage = "leverage_change"
            success, reason = await self._startup_once(lambda: connector._set_trading_pair_leverage(
                self.config.trading_pair, self.config.leverage))
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
        self.market_data_provider.acquire_book_recovery(
            self.config.connector_name, [self.config.trading_pair], self, self.config.max_data_age_seconds)
        if references:
            self.market_data_provider.acquire_book_recovery(
                self.config.reference_connector, references, self, self.config.max_data_age_seconds)
        await super().on_start()
        if self.reduction_enabled:
            self._reduction_fee_task = asyncio.create_task(self.poll_reduction_fees(), name='mm-reduction-fees')
            self._reduction_position_task = asyncio.create_task(self.poll_reduction_positions(), name='mm-reduction-positions')
        self._startup_stage = "ready"

    async def on_stop(self):
        if self._reduction_position_task is not None:
            self._reduction_position_task.cancel()
            await asyncio.gather(self._reduction_position_task, return_exceptions=True)
            self._reduction_position_task = None
        if self._reduction_fee_task is not None:
            self._reduction_fee_task.cancel()
            await asyncio.gather(self._reduction_fee_task, return_exceptions=True)
            self._reduction_fee_task = None
            self._reduction_fee_observation = None
        self.market_data_provider.release_book_recovery(self)
        if self.config.spread_policy.volatility.enabled:
            self.market_data_provider.release_candle_feed(self._spread_candle_config(), self)
        references = [ref.pair for ref in self.config.references]
        if references:
            await self.market_data_provider.release_order_books(
                self.config.reference_connector, references, self)
        super().on_stop()

    def _baseline_price_and_amount(self, level_id):
        if self.config.spread_policy.enabled:
            side = self.get_trade_type_from_level_id(level_id)
            index = int(self.get_level_from_level_id(level_id))
            spread = self._effective_spreads[0 if side == TradeType.BUY else 1][index]
            price = self.processed_data["reference_price"] * (1 + (-1 if side == TradeType.BUY else 1) * spread)
            _, allocations = self.config.get_spreads_and_amounts_in_quote(side)
            amount = Decimal(allocations[index]) / price
        else:
            price, amount = super().get_price_and_amount(level_id)
        if self._bootstrap is not None and not self._bootstrap.market_qualified:
            if not self.config.spread_policy.enabled:
                side = self.get_trade_type_from_level_id(level_id)
                _, allocations = self.config.get_spreads_and_amounts_in_quote(side)
            with localcontext() as context:
                context.rounding = ROUND_DOWN
                amount = (Decimal(allocations[int(self.get_level_from_level_id(level_id))])
                          * self.config.quote_guards.bootstrap.max_quote / self.config.total_amount_quote / price)
        if self._spread_policy.defense is not None:
            with localcontext() as context:
                context.rounding = ROUND_DOWN
                amount *= self._spread_policy.size_factor(self.market_data_provider.time())
        return price, amount

    def get_price_and_amount(self, level_id):
        price, amount = self._baseline_price_and_amount(level_id)
        if self.config.inventory_skew.enabled:
            buy, sell, scale = self._inventory_factors
            with localcontext() as context:
                context.rounding = ROUND_DOWN
                amount = amount * (buy if self.get_trade_type_from_level_id(level_id) == TradeType.BUY else sell) * scale
        return price, amount

    def get_executor_config(self, level_id, price, amount):
        if self.reduction_enabled:
            return self._make_reduction_config(level_id, price, amount)
        return self._get_open_executor_config(level_id, price, amount)

    def _get_open_executor_config(self, level_id, price, amount):
        if self.config.spread_policy.enabled:
            connector = self.market_data_provider.get_connector_with_fallback(self.config.connector_name)
            rounded = connector.quantize_order_price(self.config.trading_pair, price)
            if self.get_trade_type_from_level_id(level_id) == TradeType.SELL and rounded < price:
                rounded = connector.quantize_order_price(
                    self.config.trading_pair, rounded + connector.get_order_price_quantum(self.config.trading_pair, price))
            if rounded > price:
                with localcontext() as context:
                    context.rounding = ROUND_DOWN
                    amount = amount * price / rounded
            price = rounded
        if self.config.inventory_skew.enabled or self.config.spread_policy.enabled:
            connector = self.market_data_provider.get_connector_with_fallback(self.config.connector_name)
            raw_amount = amount
            price = connector.quantize_order_price(self.config.trading_pair, price)
            amount = connector.quantize_order_amount(self.config.trading_pair, amount)
            rule = connector.trading_rules[self.config.trading_pair]
            if (amount <= 0 or amount > raw_amount or amount < rule.min_order_size
                    or price * amount < rule.min_notional_size):
                self._quote_reason('order_size_rejected', self.get_trade_type_from_level_id(level_id))
                return None
        if not self.quote_price_valid(price):
            self._quote_reason('quote_price_rejected', self.get_trade_type_from_level_id(level_id))
            return None
        if self.config.spread_policy.enabled:
            try:
                if not self.spread_quote_valid(self.get_trade_type_from_level_id(level_id), price, amount, level_id=level_id):
                    self._quote_reason('spread_policy_rejected', self.get_trade_type_from_level_id(level_id))
                    return None
            except ValueError:
                self._quote_reason('spread_policy_unavailable', self.get_trade_type_from_level_id(level_id))
                return None
        return super().get_executor_config(level_id, price, amount).model_copy(update={
            "mm_session_id": self.config.mm_session_id, "mm_generation": self.config.mm_generation,
            "use_configured_entry_price": self.config.spread_policy.enabled or (self._bootstrap is not None and not self._bootstrap.market_qualified)})

    def prepare_entry_order(self, original):
        """Refresh a queued first entry before any order identity/intent exists.

        Never increase its original base or quote budget. The normal owner and
        pre-transport guards still decide whether the resulting order may send.
        """
        if self.reduction_enabled:
            return self.prepare_reduction_quote(original)
        if ((original.mm_session_id, original.mm_generation) != (self.config.mm_session_id, self.config.mm_generation)
                or original.controller_id != self.config.id or not self._session_admitted or self._control_error
                or original.id in self._stops_requested
                or original.level_id not in self.get_not_active_levels_ids([])):
            return None
        self.update_reference()
        if not self.processed_data.get('ready') or not self.market_data_provider.ready or not self.exposure_within_caps():
            return None
        price, amount = self.get_price_and_amount(original.level_id)
        with localcontext() as context:
            context.rounding = ROUND_DOWN
            quote_budget = min(original.amount * original.entry_price, price * amount)
        if (original.triple_barrier_config.open_order_type == OrderType.LIMIT_MAKER
                and (self._bootstrap is None or self._bootstrap.market_qualified)):
            # Preserve the upstream executor's passive-price bound, but freeze
            # it here before identity/intent creation instead of changing a
            # configured order at the transport boundary. Empty-book bootstrap
            # retains its explicit seed policy.
            try:
                self._fresh_price(self.config.connector_name, self.config.trading_pair)
                kind = PriceType.BestBid if original.side == TradeType.BUY else PriceType.BestAsk
                maker = Decimal(str(self.market_data_provider.get_price_by_type(
                    self.config.connector_name, self.config.trading_pair, kind)))
            except (ValueError, ArithmeticError, EnvironmentError, KeyError, TypeError):
                self._quote_reason('market_price_unavailable', original.side)
                return None
            price = min(price, maker) if original.side == TradeType.BUY else max(price, maker)
        with localcontext() as context:
            context.rounding = ROUND_DOWN
            amount = min(amount, original.amount, quote_budget / price)
        fresh = self.get_executor_config(original.level_id, price, amount)
        if (fresh is None or fresh.amount > original.amount
                or fresh.amount * fresh.entry_price > quote_budget):
            return None
        return original.model_copy(update={'entry_price': fresh.entry_price, 'amount': fresh.amount})
