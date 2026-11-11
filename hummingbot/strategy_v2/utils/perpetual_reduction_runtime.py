"""Resident, scoped reduction authority; no database/history/network reads here."""
import asyncio
import math
import time
from copy import deepcopy
from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal, localcontext
from typing import Optional, Tuple

from hummingbot.connector.okx_reduction_fee import FEE_POLL_SECONDS, FeeObservation, read_reduction_fee
from hummingbot.core.data_type.common import OrderType, PositionAction, PositionMode, PositionSide, PriceType, TradeType
from hummingbot.core.data_type.in_flight_order import InFlightOrder
from hummingbot.strategy_v2.executors.data_types import ExecutorConfigBase
from hummingbot.strategy_v2.executors.order_executor.data_types import ExecutionStrategy, OrderExecutorConfig
from hummingbot.strategy_v2.executors.position_executor.data_types import PositionExecutorConfig
from hummingbot.strategy_v2.models.base import RunnableStatus
from hummingbot.strategy_v2.models.executor_actions import CreateExecutorAction, StopExecutorAction
from hummingbot.strategy_v2.models.executors import CloseType
from hummingbot.strategy_v2.utils.perpetual_reduction import CloseReservation, HedgeCostBasis, ReductionView

ZERO = Decimal(0)
MAX_LIVE_REDUCTION_EXECUTORS = 100
# StrategyV2 keeps its default 100 closed DTOs in addition to live executors.
MAX_REDUCTION_RESIDENT_ROWS = 200


def quote_effect(config):
    return getattr(config, 'position_action', PositionAction.OPEN)


def quote_price(config):
    return config.price if isinstance(config, OrderExecutorConfig) else config.entry_price


@dataclass(frozen=True)
class ExecutorQuoteState:
    config: ExecutorConfigBase
    status: RunnableStatus
    applied: bool
    order_ids: Tuple[str, ...] = ()
    orders: Tuple[InFlightOrder, ...] = ()


@dataclass(frozen=True)
class ReductionRuntime:
    long: Optional[HedgeCostBasis]
    short: Optional[HedgeCostBasis]
    executors: Tuple[ExecutorQuoteState, ...]
    durable: bool


class PerpetualReductionMixin:
    def initialize_reduction(self):
        self._reduction_runtime_source = None
        self._reduction_queued = {}
        self._reduction_fee_observation = None
        self._reduction_fee_task = None
        self._reduction_position_task = None
        self._reduction_clock = time.monotonic
        self._reduction_rejection = None
        self._reduction_status_cache = None

    @property
    def reduction_enabled(self):
        return self.config.inventory_reduction.mode == 'close_first'

    def bind_reduction_runtime(self, source):
        if not callable(source):
            raise ValueError('reduction_basis_unavailable')
        self._reduction_runtime_source = source

    @property
    def reduction_status(self):
        """Last evaluated capacity only; HTTP/status readers never fetch authority."""
        if not self.reduction_enabled:
            return None
        value = deepcopy(self._reduction_status_cache or dict(
            mode='close_first', price_mode='breakeven', observed_at=None, fee_ready=None, sides={}))
        stamp, now = value['observed_at'], self.market_data_provider.time()
        value['stale'] = stamp is None or not 0 <= now - stamp <= 5
        return value

    def _cache_reduction_status(self, state):
        view = self._reduction_view(state)
        try:
            self._closing_fee()
            fee_ready = True
        except ValueError:
            fee_ready = False
        sides = {}
        for side in (TradeType.BUY, TradeType.SELL):
            decision = view.decide(side)
            basis, venue = (view.long, view.venue_long) if decision.leg == TradeType.BUY else (view.short, view.venue_short)
            reserved = sum((row.amount for row in view.reservations if row.leg == decision.leg and not row.released), ZERO)
            sides[side.name] = dict(effect=decision.effect.value if decision.effect else None,
                                    target_leg='LONG' if decision.leg == TradeType.BUY else 'SHORT',
                                    owned_base=str(basis.amount) if basis is not None else None, venue_base=str(venue),
                                    reserved_base=str(reserved), available_base=str(decision.available), reason_code=decision.reason)
        self._reduction_status_cache = dict(mode='close_first', price_mode='breakeven',
                                            observed_at=self.market_data_provider.time(), fee_ready=fee_ready, sides=sides)

    async def poll_reduction_fees(self):
        connector = self.market_data_provider.get_connector_with_fallback(self.config.connector_name)
        while True:
            try:
                self._reduction_fee_observation = await asyncio.wait_for(
                    read_reduction_fee(connector, self.config.trading_pair), timeout=5)
            except asyncio.CancelledError:
                raise
            except Exception:
                self._reduction_fee_observation = None
            await asyncio.sleep(FEE_POLL_SECONDS)

    def request_reduction_position_refresh(self):
        """Wake the native serialized account reader; never perform REST at a gate.

        An absent hedge leg needs a recent full snapshot. A healthy private WS
        does not keep that snapshot fresh: native backup reads can be 120s apart.
        Reuse its coalescing event rather than start a second competing reader.
        """
        if not self.reduction_enabled:
            return False
        connector = self.market_data_provider.get_connector_with_fallback(self.config.connector_name)
        stamp = connector.position_snapshot_times.get(self.config.trading_pair)
        now = self.market_data_provider.time()
        if not isinstance(now, (int, float)) or isinstance(now, bool) or not math.isfinite(now):
            raise ValueError('reduction_position_unavailable')
        if (isinstance(stamp, (int, float)) and not isinstance(stamp, bool) and math.isfinite(stamp)
                and 0 <= now - stamp <= float(self.config.max_data_age_seconds) / 2):
            return False
        notifier = connector._poll_notifier
        if not isinstance(notifier, asyncio.Event):
            raise ValueError('reduction_position_unavailable')
        if notifier.is_set():
            return False
        notifier.set()
        return True

    async def poll_reduction_positions(self):
        while True:
            try:
                self.request_reduction_position_refresh()
            except (ValueError, ArithmeticError, AttributeError, TypeError):
                pass  # Cached position freshness still independently fences sends.
            await asyncio.sleep(2)

    def _closing_fee(self):
        value = self._reduction_fee_observation
        if not isinstance(value, FeeObservation):
            raise ValueError('reduction_fee_unavailable')
        return value.current(self._reduction_clock())

    def _reduction_wait(self, reason, side=None):
        if self._quote_diagnostics is None:
            self._reset_quote_diagnostics()
        self._quote_reason(reason, side)

    def _reduction_runtime(self):
        if not callable(self._reduction_runtime_source):
            raise ValueError('reduction_basis_unavailable')
        state = self._reduction_runtime_source()
        if (not isinstance(state, ReductionRuntime) or state.durable is not True
                or not isinstance(state.executors, tuple) or len(state.executors) > MAX_REDUCTION_RESIDENT_ROWS):
            raise ValueError('reduction_basis_unavailable')
        seen = set()
        for row in state.executors:
            if (not isinstance(row, ExecutorQuoteState) or type(row.applied) is not bool
                    or not isinstance(row.status, RunnableStatus) or not isinstance(row.order_ids, tuple)
                    or not isinstance(row.orders, tuple) or len(row.order_ids) > 32
                    or len(set(row.order_ids)) != len(row.order_ids)
                    or any(not isinstance(identity, str) or not identity for identity in row.order_ids)
                    or any(not isinstance(order, InFlightOrder) or order.client_order_id not in row.order_ids for order in row.orders)
                    or len({order.client_order_id for order in row.orders}) != len(row.orders)):
                raise ValueError('reduction_runtime_conflict')
            config = row.config
            if (not isinstance(config, (PositionExecutorConfig, OrderExecutorConfig))
                    or not isinstance(config.id, str) or not config.id or config.id in seen or config.controller_id != self.config.id
                    or config.side not in (TradeType.BUY, TradeType.SELL) or type(config.mm_generation) is not int
                    or not isinstance(config.amount, Decimal) or not config.amount.is_finite() or config.amount <= 0
                    or not isinstance(quote_price(config), Decimal) or not quote_price(config).is_finite() or quote_price(config) <= 0
                    or config.level_id not in self.get_not_active_levels_ids([])
                    or (config.connector_name, config.trading_pair) != (self.config.connector_name, self.config.trading_pair)
                    or (config.mm_session_id, config.mm_generation) != (self.config.mm_session_id, self.config.mm_generation)
                    or config.type not in ('position_executor', 'order_executor')
                    or (config.type == 'order_executor' and (
                        quote_effect(config) != PositionAction.CLOSE or config.execution_strategy != ExecutionStrategy.LIMIT_MAKER))):
                raise ValueError('reduction_runtime_conflict')
            seen.add(config.id)
            queued = self._reduction_queued.get(config.id)
            if queued is not None:
                if (queued.type != config.type or quote_effect(queued) != quote_effect(config)
                        or queued.level_id != config.level_id or queued.side != config.side
                        or config.amount > queued.amount
                        or config.amount * quote_price(config) > queued.amount * quote_price(queued)):
                    raise ValueError('reduction_runtime_conflict')
                del self._reduction_queued[config.id]
        if sum(not row.applied for row in state.executors) + len(self._reduction_queued) > MAX_LIVE_REDUCTION_EXECUTORS:
            raise ValueError('reduction_reservation_limit')
        return state

    def _reduction_configs(self, state):
        return [row.config for row in state.executors if not row.applied] + list(self._reduction_queued.values())

    def _reduction_venue(self):
        connector = self.market_data_provider.get_connector_with_fallback(self.config.connector_name)
        if connector.position_mode != PositionMode.HEDGE:
            raise ValueError('reduction_position_unavailable')
        now = self.market_data_provider.time()

        def fresh(stamp):
            return (isinstance(stamp, (int, float)) and not isinstance(stamp, bool) and math.isfinite(stamp)
                    and 0 <= now - stamp <= self.config.max_data_age_seconds)

        amounts = []
        for leg in (PositionSide.LONG, PositionSide.SHORT):
            rows = [(key, value) for key, value in connector.account_positions.items()
                    if value.trading_pair == self.config.trading_pair and value.position_side == leg]
            if len(rows) > 1:
                raise ValueError('reduction_position_unavailable')
            if not rows:
                if not fresh(connector.position_snapshot_times.get(self.config.trading_pair)):
                    raise ValueError('reduction_position_unavailable')
                amounts.append(ZERO)
                continue
            key, position = rows[0]
            observation = connector.position_observations.get(key)
            if (not isinstance(observation, dict) or observation.get('margin_mode') != 'cross'
                    or not fresh(observation.get('observed_at')) or not position.amount.is_finite()
                    or (leg == PositionSide.LONG and position.amount < 0)
                    or (leg == PositionSide.SHORT and position.amount > 0)):
                raise ValueError('reduction_position_unavailable')
            amounts.append(abs(position.amount))
        return tuple(amounts)

    def _reduction_view(self, state=None):
        state = self._reduction_runtime() if state is None else state
        long, short = self._reduction_venue()
        reservations = tuple(CloseReservation(config.id, TradeType.SELL if config.side == TradeType.BUY else TradeType.BUY,
                                              config.amount) for config in self._reduction_configs(state)
                             if quote_effect(config) == PositionAction.CLOSE)
        return ReductionView(state.long, state.short, long, short, reservations)

    def _reduction_exposure(self):
        state = self._reduction_runtime()
        long, short = self._reduction_venue()
        for config in self._reduction_configs(state):
            if quote_effect(config) == PositionAction.OPEN:
                if config.side == TradeType.BUY:
                    long += config.amount
                else:
                    short += config.amount
        return long, short

    def reduction_ready(self):
        if not self.reduction_enabled:
            return False
        try:
            self._closing_fee()
            view = self._reduction_view()
            # Reserved working quotes still make this a reduction-capable market.
            # This status projection grants no order authority; exact quantities
            # and other reservations are checked independently at each send gate.
            rule = self.market_data_provider.get_connector_with_fallback(self.config.connector_name).trading_rules[self.config.trading_pair]
            return any(basis is not None and min(basis.amount, venue) >= rule.min_order_size
                       and min(basis.amount, venue) > 0
                       for basis, venue in ((view.long, view.venue_long), (view.short, view.venue_short)))
        except (ValueError, ArithmeticError, AttributeError, TypeError):
            return False

    def _reduction_levels(self):
        state = self._reduction_runtime()
        working = {config.level_id for config in self._reduction_configs(state)}
        # Native scoped reservations are authoritative. An older report DTO may
        # still call a proved/released executor active; it cannot reclaim a level.
        # Preserve the existing explicit historical stop-loss cooldown only.
        for info in self.executors_info:
            if (info.close_type == CloseType.STOP_LOSS
                    and self.market_data_provider.time() - info.close_timestamp < self.config.cooldown_time):
                working.add(info.config.level_id)
        for config in self._reduction_configs(state):
            self._quote_reason('active_executor', config.side)
        return self.get_not_active_levels_ids(working)

    def _reduction_maker_price(self, connector, side, price):
        rounded = connector.quantize_order_price(self.config.trading_pair, price)
        if side == TradeType.SELL and rounded < price:
            tick = connector.get_order_price_quantum(self.config.trading_pair, price)
            rounded = connector.quantize_order_price(self.config.trading_pair, rounded + tick)
        return rounded

    def _make_reduction_config(self, level_id, price, amount, *, reservation_id=None):
        side = self.get_trade_type_from_level_id(level_id)
        try:
            view = self._reduction_view()
            decision = view.decide(side, reservation_id=reservation_id)
            if decision.reason:
                self._reduction_wait(decision.reason, side)
                return None
            if decision.effect == PositionAction.OPEN:
                return self._get_open_executor_config(level_id, price, amount)
            fee = self._closing_fee()
            basis = view.short if side == TradeType.BUY else view.long
            connector = self.market_data_provider.get_connector_with_fallback(self.config.connector_name)
            budget = price * amount
            quantity = connector.quantize_order_amount(self.config.trading_pair, min(amount, decision.available))
            if quantity <= 0:
                self._reduction_wait('reduction_dust', side)
                return None
            tick = connector.get_order_price_quantum(self.config.trading_pair, price)
            boundary = basis.closing_price(quantity, fee, tick)
            price = min(price, boundary) if side == TradeType.BUY else max(price, boundary)
            kind = PriceType.BestBid if side == TradeType.BUY else PriceType.BestAsk
            maker = Decimal(str(self.market_data_provider.get_price_by_type(
                self.config.connector_name, self.config.trading_pair, kind)))
            if not maker.is_finite() or maker <= 0:
                raise ValueError('reduction_price_not_profitable')
            # Use the same resident passive bound as pre-identity preparation.
            # Delaying this until then can make even one remaining lot exceed
            # the artificially smaller queued notional on every fresh attempt.
            price = min(price, maker) if side == TradeType.BUY else max(price, maker)
            # Spend the level's original allocation at the actual maker tick.
            # Passing an unaligned small close to the OPEN builder would shrink
            # it against quantity * raw_price, inventing a smaller budget and
            # losing an amount lot even when the level can fund the whole close.
            price = self._reduction_maker_price(connector, side, price)
            with localcontext() as context:
                context.rounding = ROUND_DOWN
                quantity = connector.quantize_order_amount(self.config.trading_pair, min(quantity, budget / price))
            if quantity <= 0:
                self._reduction_wait('reduction_dust', side)
                return None
            boundary = basis.closing_price(quantity, fee, connector.get_order_price_quantum(self.config.trading_pair, price))
            price = min(price, boundary) if side == TradeType.BUY else max(price, boundary)
            if quantity * price > budget or not self.quote_price_valid(price):
                self._reduction_wait('reduction_price_not_profitable', side)
                return None
            baseline = self._get_open_executor_config(level_id, price, quantity)
            if baseline is None or not basis.covers(baseline.entry_price, baseline.amount, fee):
                self._reduction_wait('reduction_price_not_profitable', side)
                return None
            return OrderExecutorConfig(id=baseline.id, timestamp=baseline.timestamp, controller_id=self.config.id,
                                       connector_name=self.config.connector_name, trading_pair=self.config.trading_pair,
                                       side=side, amount=baseline.amount, price=baseline.entry_price, leverage=self.config.leverage,
                                       position_action=PositionAction.CLOSE, execution_strategy=ExecutionStrategy.LIMIT_MAKER,
                                       level_id=level_id, mm_session_id=self.config.mm_session_id, mm_generation=self.config.mm_generation)
        except (ValueError, ArithmeticError, AttributeError, TypeError) as error:
            reason = str(error)
            self._reduction_wait(reason if reason.startswith('reduction_') else 'reduction_basis_unavailable', side)
            return None

    def _create_reduction_actions(self):
        self._reset_quote_diagnostics()
        if self._control_error or not self._session_admitted or not self.processed_data.get('ready') or not self.market_data_provider.ready:
            self._quote_reason(self._quote_readiness_reason() or 'readiness_unavailable')
            return []
        created = []
        try:
            state = self._reduction_runtime()
            long, short = self._reduction_exposure()
            connector = self.market_data_provider.get_connector_with_fallback(self.config.connector_name)
            available = Decimal(str(connector.get_available_balance('USDT')))
            if not available.is_finite() or available < 0:
                raise ValueError('account_balance_unavailable')
            allocation = sum((config.amount * quote_price(config) for config in self._reduction_configs(state)), ZERO)
            actions = []
            for level in self.get_levels_to_execute():
                if sum(not row.applied for row in state.executors) + len(self._reduction_queued) >= MAX_LIVE_REDUCTION_EXECUTORS:
                    self._quote_reason('reduction_reservation_limit')
                    break
                config = self.get_executor_config(level, *self.get_price_and_amount(level))
                if config is None:
                    continue
                price = quote_price(config)
                if allocation + config.amount * price > self._quote_budget():
                    self._quote_reason('allocation_limit', config.side)
                    continue
                closing = quote_effect(config) == PositionAction.CLOSE
                proposed_long = long + (config.amount if not closing and config.side == TradeType.BUY else ZERO)
                proposed_short = short + (config.amount if not closing and config.side == TradeType.SELL else ZERO)
                if not closing and not self._within_caps(proposed_long, proposed_short):
                    self._quote_reason('inventory_limit', config.side)
                    continue
                fee = self._closing_fee() if closing else None
                required = config.amount * price * fee.rate + fee.flat_quote if closing else config.amount * price / config.leverage
                if required > available:
                    self._quote_reason('insufficient_quote_balance', config.side)
                    continue
                actions.append(CreateExecutorAction(controller_id=self.config.id, executor_config=config))
                self._reduction_queued[config.id] = config
                created.append(config.id)
                available -= required
                allocation += config.amount * price
                long, short = proposed_long, proposed_short
                self._quote_diagnostics['proposed'][config.side.name] += 1
            self._cache_reduction_status(state)
            return actions
        except (ValueError, ArithmeticError, AttributeError, TypeError) as error:
            for identity in created:
                self._reduction_queued.pop(identity, None)
            self._quote_diagnostics['proposed'] = {'BUY': 0, 'SELL': 0}
            reason = str(error)
            self._quote_reason(reason if reason.startswith('reduction_') else 'proposal_evaluation_unavailable')
            return []

    def reduction_order_valid(self, order, scope):
        """Validate exact effect/identity against live resident authority at send."""
        def reject(reason):
            self._reduction_rejection = reason
            self._reduction_wait(reason, order.trade_type)
            return False

        if not self.reduction_enabled or not isinstance(scope, dict):
            return reject('reduction_authority_unavailable')
        if (not self._session_admitted or self._control_error or not self.processed_data.get('ready')
                or not self.market_data_provider.ready):
            return reject('reduction_market_not_ready')
        try:
            state = self._reduction_runtime()
            row = next((row for row in state.executors if row.config.id == scope.get('id')), None)
            if (row is None or row.applied or order.client_order_id not in row.order_ids
                    or row.status not in (RunnableStatus.NOT_STARTED, RunnableStatus.RUNNING)
                    or row.config.id in self._stops_requested
                    or not any(native is order for native in row.orders)):
                return reject('reduction_authority_unavailable')
            config = row.config
            if scope != config.model_dump(mode='json'):
                return reject('reduction_identity_mismatch')
            if (order.trading_pair != config.trading_pair or order.trade_type != config.side
                    or order.position != quote_effect(config) or order.order_type != OrderType.LIMIT_MAKER
                    or order.price != quote_price(config) or not order.amount.is_finite() or order.amount <= 0):
                return reject('reduction_identity_mismatch')
            prior = sum((native.executed_amount_base for native in row.orders
                         if native.client_order_id != order.client_order_id), ZERO)
            if order.amount > config.amount - prior:
                return reject('reduction_quantity_unavailable')
            view = self._reduction_view(state)
            decision = view.decide(config.side, reservation_id=config.id)
            if decision.effect != quote_effect(config) or decision.reason:
                return reject(decision.reason or 'reduction_effect_changed')
            if decision.effect == PositionAction.CLOSE:
                basis = view.short if config.side == TradeType.BUY else view.long
                fee = self._closing_fee()
                available = Decimal(str(self.market_data_provider.get_connector_with_fallback(
                    self.config.connector_name).get_available_balance('USDT')))
                reserved = sum((other.amount * quote_price(other) * fee.rate + fee.flat_quote
                                for other in self._reduction_configs(state)
                                if other.id != config.id and quote_effect(other) == PositionAction.CLOSE), ZERO)
                if order.amount > decision.available:
                    return reject('reduction_quantity_unavailable')
                if not basis.covers(order.price, order.amount, fee):
                    return reject('reduction_price_not_profitable')
                if (not available.is_finite() or available < 0
                        or reserved + order.amount * order.price * fee.rate + fee.flat_quote > available):
                    return reject('reduction_fee_balance_unavailable')
            if not self.quote_price_valid(order.price) or not self.spread_quote_valid(
                    config.side, order.price, order.amount, level_id=config.level_id):
                return reject('reduction_price_rejected')
            self._reduction_rejection = None
            return True
        except (ValueError, ArithmeticError, AttributeError, TypeError) as error:
            return reject(str(error) if str(error).startswith('reduction_') else 'reduction_authority_unavailable')

    def prepare_reduction_quote(self, original):
        """Refresh before identity creation; neither effect nor budget can grow."""
        if ((original.mm_session_id, original.mm_generation) != (self.config.mm_session_id, self.config.mm_generation)
                or original.controller_id != self.config.id or not self._session_admitted or self._control_error
                or original.id in self._stops_requested or original.level_id not in self.get_not_active_levels_ids([])):
            return None
        try:
            self.update_reference()
            if not self.processed_data.get('ready') or not self.market_data_provider.ready:
                return None
            if quote_effect(original) == PositionAction.OPEN and not self.exposure_within_caps():
                return None
            price, amount = self.get_price_and_amount(original.level_id)
            self._fresh_price(self.config.connector_name, self.config.trading_pair)
            kind = PriceType.BestBid if original.side == TradeType.BUY else PriceType.BestAsk
            maker = Decimal(str(self.market_data_provider.get_price_by_type(self.config.connector_name, self.config.trading_pair, kind)))
            if not maker.is_finite() or maker <= 0:
                return None
            price = min(price, maker) if original.side == TradeType.BUY else max(price, maker)
            if quote_effect(original) == PositionAction.CLOSE:
                connector = self.market_data_provider.get_connector_with_fallback(self.config.connector_name)
                price = self._reduction_maker_price(connector, original.side, price)
            budget = min(original.amount * quote_price(original), price * amount)
            with localcontext() as context:
                context.rounding = ROUND_DOWN
                amount = min(amount, original.amount, budget / price)
            fresh = self._make_reduction_config(original.level_id, price, amount, reservation_id=original.id)
            if (fresh is None or fresh.type != original.type or quote_effect(fresh) != quote_effect(original)
                    or fresh.amount > original.amount or fresh.amount * quote_price(fresh) > budget):
                return None
            field = 'price' if original.type == 'order_executor' else 'entry_price'
            return original.model_copy(update={field: quote_price(fresh), 'amount': fresh.amount})
        except (ValueError, ArithmeticError, AttributeError, TypeError):
            return None

    def _reduction_refresh_actions(self):
        state = self._reduction_runtime()
        view = self._reduction_view(state)
        self._cache_reduction_status(state)
        actions = []
        for row in state.executors:
            config = row.config
            if row.applied or row.status not in (RunnableStatus.NOT_STARTED, RunnableStatus.RUNNING) or config.id in self._stops_requested:
                continue
            decision = view.decide(config.side, reservation_id=config.id)
            invalid = decision.effect != quote_effect(config) or decision.reason is not None
            if not invalid and quote_effect(config) == PositionAction.CLOSE:
                native = next((order for order in row.orders if not order.is_done), None)
                amount = native.amount - native.executed_amount_base if native is not None else config.amount
                basis = view.short if config.side == TradeType.BUY else view.long
                try:
                    invalid = (amount > decision.available or not basis.covers(quote_price(config), amount, self._closing_fee())
                               or not self.quote_price_valid(quote_price(config))
                               or not self.spread_quote_valid(config.side, quote_price(config), amount, level_id=config.level_id))
                except (ValueError, ArithmeticError, TypeError):
                    invalid = True
            if invalid:
                self._stops_requested.add(config.id)
                actions.append(StopExecutorAction(controller_id=self.config.id, executor_id=config.id, keep_position=True))
        return actions
