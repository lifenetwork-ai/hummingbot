import asyncio
import logging
import math
import uuid
from contextlib import closing
from copy import deepcopy
from decimal import Decimal
from typing import TYPE_CHECKING, Dict, List, Optional, Union

from sqlalchemy.engine import Row

from hummingbot.connector.markets_recorder import MarketsRecorder
from hummingbot.core.data_type.common import PositionAction, PositionMode, PriceType, TradeType
from hummingbot.core.data_type.in_flight_order import OrderState
from hummingbot.logger import HummingbotLogger
from hummingbot.model.executors import Executors
from hummingbot.model.position import Position

if TYPE_CHECKING:
    from hummingbot.strategy.strategy_v2_base import StrategyV2Base

from hummingbot.strategy_v2.executors.arbitrage_executor.arbitrage_executor import ArbitrageExecutor
from hummingbot.strategy_v2.executors.data_types import PositionSummary
from hummingbot.strategy_v2.executors.dca_executor.dca_executor import DCAExecutor
from hummingbot.strategy_v2.executors.executor_base import ExecutorBase
from hummingbot.strategy_v2.executors.grid_executor.grid_executor import GridExecutor
from hummingbot.strategy_v2.executors.lp_executor.lp_executor import LPExecutor
from hummingbot.strategy_v2.executors.order_executor.data_types import OrderExecutorConfig
from hummingbot.strategy_v2.executors.order_executor.order_executor import OrderExecutor
from hummingbot.strategy_v2.executors.position_executor.data_types import PositionExecutorConfig
from hummingbot.strategy_v2.executors.position_executor.position_executor import PositionExecutor
from hummingbot.strategy_v2.executors.twap_executor.twap_executor import TWAPExecutor
from hummingbot.strategy_v2.executors.xemm_executor.xemm_executor import XEMMExecutor
from hummingbot.strategy_v2.models.base import RunnableStatus
from hummingbot.strategy_v2.models.executor_actions import (
    CreateExecutorAction,
    ExecutorAction,
    StopExecutorAction,
    StoreExecutorAction,
)
from hummingbot.strategy_v2.models.executors import CloseType
from hummingbot.strategy_v2.models.executors_info import ExecutorInfo, PerformanceReport
from hummingbot.strategy_v2.utils.perpetual_reduction import HedgeCostBasis


class PositionHold:
    def __init__(self, connector_name: str, trading_pair: str, side: TradeType = None):
        self.connector_name = connector_name
        self.trading_pair = trading_pair
        self.side = side
        # Store only client order IDs
        self.order_ids = set()
        self.has_unstored_updates = True

        # Net position tracking (positive = long, negative = short)
        self.net_amount_base = Decimal("0")
        self.avg_entry_price = Decimal("0")

        # Accumulated metrics
        self.realized_pnl_quote = Decimal("0")
        self.volume_traded_quote = Decimal("0")
        self.cum_fees_quote = Decimal("0")
        self._reduction_basis = None

        # Separate tracking for buys and sells (for logging/diagnostics)
        self.buy_amount_base = Decimal("0")
        self.buy_amount_quote = Decimal("0")
        self.sell_amount_base = Decimal("0")
        self.sell_amount_quote = Decimal("0")

    @property
    def reduction_basis(self):
        if self.connector_name != 'okx_perpetual' or self.side not in (TradeType.BUY, TradeType.SELL):
            return None
        if not self.net_amount_base:
            return HedgeCostBasis(self.side, Decimal(0), Decimal(0), Decimal(0))
        basis = self._reduction_basis
        if (basis is not None and basis.leg == self.side and basis.amount == abs(self.net_amount_base)
                and (self.net_amount_base > 0) == (self.side == TradeType.BUY)):
            return basis
        return None

    def _apply_reduction_basis(self, order, is_buy, amount, quote, fee):
        basis = self.reduction_basis
        try:
            effect = PositionAction(order.get('position'))
            expected_buy = (self.side == TradeType.BUY) == (effect == PositionAction.OPEN)
            if (basis is None or is_buy != expected_buy or 'cumulative_fee_paid_quote' not in order
                    or order['cumulative_fee_paid_quote'] is None):
                raise ValueError('Unproven hedge cost scope')
            self._reduction_basis = basis.apply(effect, amount, quote, fee)
        except (ValueError, ArithmeticError, TypeError):
            # Retain native financial accounting, but grant no closing-price
            # authority to legacy, foreign, or unproven cost/effect evidence.
            self._reduction_basis = None

    def _process_order(self, is_buy: bool, amount_base: Decimal, amount_quote: Decimal):
        """
        Process an order incrementally: realize PnL when reducing position,
        update avg_entry_price when adding to position.
        """
        if amount_base == 0:
            return

        order_price = amount_quote / amount_base
        is_reducing = (self.net_amount_base > 0 and not is_buy) or (self.net_amount_base < 0 and is_buy)

        if is_reducing:
            abs_net = abs(self.net_amount_base)
            matched = min(amount_base, abs_net)

            # Realize PnL on matched portion
            if self.net_amount_base > 0:
                self.realized_pnl_quote += (order_price - self.avg_entry_price) * matched
            else:
                self.realized_pnl_quote += (self.avg_entry_price - order_price) * matched

            excess = amount_base - matched
            if excess > 0:
                # Position flipped to opposite side
                self.net_amount_base = excess if is_buy else -excess
                self.avg_entry_price = order_price
            elif matched == abs_net:
                # Position fully closed
                self.net_amount_base = Decimal("0")
                self.avg_entry_price = Decimal("0")
            else:
                # Position partially reduced, avg_entry_price stays the same
                if self.net_amount_base > 0:
                    self.net_amount_base -= matched
                else:
                    self.net_amount_base += matched
        else:
            # Adding to position or first order
            if self.net_amount_base == 0:
                self.net_amount_base = amount_base if is_buy else -amount_base
                self.avg_entry_price = order_price
            else:
                abs_net = abs(self.net_amount_base)
                total_cost = self.avg_entry_price * abs_net + amount_quote
                new_abs = abs_net + amount_base
                self.avg_entry_price = total_cost / new_abs
                self.net_amount_base = new_abs if is_buy else -new_abs

    def add_orders_from_executor(self, executor: ExecutorInfo):
        logger = logging.getLogger(__name__)
        debug = logger.isEnabledFor(logging.DEBUG)
        custom_info = executor.custom_info
        if "held_position_orders" not in custom_info or len(custom_info["held_position_orders"]) == 0:
            logging.getLogger(__name__).warning(
                f"PositionHold.add_orders: executor {executor.id[:8]} has no held_position_orders. "
                f"close_type={executor.close_type} side={executor.config.side} "
                f"level_id={custom_info.get('level_id', 'N/A')}"
            )
            return

        for order in custom_info["held_position_orders"]:
            # Skip if we've already processed this order
            order_id = order.get("client_order_id")
            if order_id in self.order_ids:
                if debug:
                    logger.debug(
                        f"PositionHold.add_orders: skipping duplicate order {order_id}"
                    )
                continue

            # Add the order ID to our set
            self.order_ids.add(order_id)
            self.has_unstored_updates = True

            # Update metrics incrementally
            executed_amount_base = Decimal(str(order.get("executed_amount_base", 0)))
            executed_amount_quote = Decimal(str(order.get("executed_amount_quote", 0)))

            is_buy = order.get("trade_type") == "BUY"
            if debug:
                order_type_label = order.get("order_type", "N/A")
                position_action = order.get("position", "N/A")
                prev_net = self.net_amount_base

            # Update volume traded in quote
            self.volume_traded_quote += executed_amount_quote

            # Update buy/sell totals (for logging/diagnostics)
            if is_buy:
                self.buy_amount_base += executed_amount_base
                self.buy_amount_quote += executed_amount_quote
            else:
                self.sell_amount_base += executed_amount_base
                self.sell_amount_quote += executed_amount_quote

            # Update fees (tx fees paid)
            fee_quote = Decimal(str(order.get("cumulative_fee_paid_quote", 0)))
            self.cum_fees_quote += fee_quote

            if executed_amount_base and self.connector_name == 'okx_perpetual' and self.side in (TradeType.BUY, TradeType.SELL):
                self._apply_reduction_basis(order, is_buy, executed_amount_base, executed_amount_quote, fee_quote)

            # Process order for incremental PnL calculation
            self._process_order(is_buy, executed_amount_base, executed_amount_quote)

            if debug:
                logger.debug(
                    f"PositionHold.add_orders: {self.trading_pair} | "
                    f"order={order_id[:12] if order_id else 'N/A'} | "
                    f"trade_type={'BUY' if is_buy else 'SELL'} | "
                    f"order_type={order_type_label} | position_action={position_action} | "
                    f"amount_base={executed_amount_base} amount_quote={executed_amount_quote:.2f} | "
                    f"executor_side={executor.config.side} level={custom_info.get('level_id', 'N/A')} | "
                    f"net_before={prev_net} -> net_after={self.net_amount_base} | "
                    f"avg_entry={self.avg_entry_price:.4f} realized_pnl={self.realized_pnl_quote:.4f} | "
                    f"buy_total={self.buy_amount_base} sell_total={self.sell_amount_base}"
                )

    def get_position_summary(self, mid_price: Decimal):
        logger = logging.getLogger(__name__)
        debug = logger.isEnabledFor(logging.DEBUG)
        abs_amount = abs(self.net_amount_base)
        is_net_long = self.net_amount_base >= 0

        # Calculate unrealized PnL for remaining open position
        unrealized_pnl_quote = Decimal("0")
        if abs_amount > 0:
            if is_net_long:
                unrealized_pnl_quote = (mid_price - self.avg_entry_price) * abs_amount
            else:
                unrealized_pnl_quote = (self.avg_entry_price - mid_price) * abs_amount

        summary = PositionSummary(
            connector_name=self.connector_name,
            trading_pair=self.trading_pair,
            volume_traded_quote=self.volume_traded_quote,
            amount=abs_amount,
            # A zero hedge leg still owns its independent fee/PnL checkpoint.
            # Mapping both flat legs to BUY lets a SHORT snapshot overwrite the
            # LONG row at persistence. Nonzero exposure and spot netting retain
            # their existing direction convention.
            side=(self.side if self.connector_name == 'okx_perpetual' and abs_amount == 0 and self.side is not None
                  else TradeType.BUY if is_net_long else TradeType.SELL),
            breakeven_price=self.avg_entry_price,
            unrealized_pnl_quote=unrealized_pnl_quote,
            realized_pnl_quote=self.realized_pnl_quote,
            cum_fees_quote=self.cum_fees_quote)

        if debug:
            logger.debug(
                f"PositionHold.summary: {self.trading_pair} | "
                f"net={'LONG' if is_net_long else 'SHORT'} {abs_amount} @ entry={self.avg_entry_price:.4f} | "
                f"unrealized_pnl={unrealized_pnl_quote:.4f} realized_pnl={self.realized_pnl_quote:.4f} | "
                f"mid_price={mid_price:.4f} orders={len(self.order_ids)}"
            )
        return summary


class ExecutorOrchestrator:
    """
    Orchestrator for various executors.
    """
    _logger = None
    _executor_mapping = {
        "position_executor": PositionExecutor,
        "grid_executor": GridExecutor,
        "dca_executor": DCAExecutor,
        "arbitrage_executor": ArbitrageExecutor,
        "twap_executor": TWAPExecutor,
        "xemm_executor": XEMMExecutor,
        "order_executor": OrderExecutor,
        "lp_executor": LPExecutor,
    }

    @classmethod
    def logger(cls) -> HummingbotLogger:
        if cls._logger is None:
            cls._logger = logging.getLogger(__name__)
        return cls._logger

    def __init__(self,
                 strategy: "StrategyV2Base",
                 executors_update_interval: float = 1.0,
                 executors_max_retries: int = 10,
                 initial_positions_by_controller: Optional[dict] = None):
        self.strategy = strategy
        self.executors_update_interval = executors_update_interval
        self.executors_max_retries = executors_max_retries
        self.active_executors = {}
        self.positions_held = {}
        # Deduplicate exactly the resident executors, then retire their IDs
        # after persistence. A recent-only cache can forget still-resident holds.
        self.executors_ids_position_held = set()
        self.cached_performance = {}
        self.initial_positions_by_controller = initial_positions_by_controller or {}
        self._initial_positions_initialized = False
        self._checkpoint_candidates = []
        self._initialize_cached_performance()

    def reduction_runtime(self, controller_id):
        """Project resident ownership/receipts only; never fetch history here."""
        from hummingbot.core.order_submission_guard import active_guard
        from hummingbot.strategy_v2.utils.perpetual_reduction_runtime import (
            MAX_REDUCTION_RESIDENT_ROWS,
            ExecutorQuoteState,
            ReductionRuntime,
        )
        recorder = MarketsRecorder.get_instance()
        if (recorder.durable_position_checkpoints is not True or recorder.sql_manager.persistence_healthy is not True
                or getattr(self, 'mm_scope_incomplete', False)):
            raise ValueError('reduction_basis_unavailable')
        controller = self.strategy.controllers.get(controller_id)
        config = controller.config if controller is not None else None
        if config is None or config.connector_name != 'okx_perpetual' or config.inventory_reduction.mode != 'close_first':
            raise ValueError('reduction_basis_unavailable')
        bases = {TradeType.BUY: HedgeCostBasis(TradeType.BUY, Decimal(0), Decimal(0), Decimal(0)),
                 TradeType.SELL: HedgeCostBasis(TradeType.SELL, Decimal(0), Decimal(0), Decimal(0))}
        seen = set()
        for hold in self.positions_held.get(controller_id, []):
            if (hold.connector_name, hold.trading_pair) != (config.connector_name, config.trading_pair):
                raise ValueError('reduction_basis_unavailable')
            if hold.side not in bases or hold.side in seen:
                raise ValueError('reduction_basis_unavailable')
            seen.add(hold.side)
            bases[hold.side] = hold.reduction_basis
        rows = self.active_executors.get(controller_id, [])
        if len(rows) > MAX_REDUCTION_RESIDENT_ROWS:
            raise ValueError('reduction_reservation_limit')
        guard = active_guard()
        states = []
        for executor in rows:
            if not isinstance(executor, (PositionExecutor, OrderExecutor)):
                raise ValueError('reduction_runtime_conflict')
            refs = ((executor._open_order, *executor._failed_orders, executor._close_order, executor._take_profit_limit_order)
                    if isinstance(executor, PositionExecutor) else tuple(executor._execution_orders(include_retired=True)))
            identities, orders = [], {}
            for tracked in refs:
                if tracked is None or not tracked.order_id or tracked.order_id in identities:
                    continue
                identities.append(tracked.order_id)
                native = tracked.order or executor.get_in_flight_order(executor.config.connector_name, tracked.order_id)
                if native is not None:
                    orders[tracked.order_id] = native
            if len(identities) > 32:
                raise ValueError('reduction_reservation_limit')
            applied = executor.config.id in self.executors_ids_position_held
            if executor.is_closed and executor.close_type != CloseType.POSITION_HOLD:
                # Positive terminal zero-fill proof releases a level even if
                # admission caches await the next unrelated before_order call.
                # Locally rejected unsent orders use the released registry path.
                resolved = len(orders) == len(identities) and all(
                    order.is_done and not order.executed_amount_base for order in orders.values())
                released = guard is not None and all(
                    identity not in guard.active_intents and identity not in guard.sent_intents for identity in identities)
                proved = all(getattr(order, '_managed_terminal_proved', False) is True
                             and order.current_state in (OrderState.CANCELED, OrderState.FILLED) for order in orders.values())
                applied = not identities or (resolved and (released or proved))
            states.append(ExecutorQuoteState(executor.config, executor.status, applied, tuple(identities), tuple(orders.values())))
        return ReductionRuntime(bases[TradeType.BUY], bases[TradeType.SELL], tuple(states), True)

    def _mm_scope(self):
        scope = getattr(getattr(self.strategy, "config", None), "mm_drawdown_scope", None)
        return scope if isinstance(scope, dict) else {}

    def _mm_contains(self, controller_id, connector_name, trading_pair):
        market = self._mm_scope().get(controller_id)
        return market is not None and (market.connector_name, market.trading_pair) == (connector_name, trading_pair)

    def _mm_mark_price(self, controller_id, connector_name, trading_pair):
        from hummingbot.core.operational_warnings import DrawdownValuationUnavailable
        if not self._mm_contains(controller_id, connector_name, trading_pair):
            raise ValueError("drawdown_position_scope_unavailable")
        provider = self.strategy.market_data_provider
        try:
            book = provider.get_order_book(connector_name, trading_pair)
            stamp = max(book.snapshot_uid, book.last_diff_uid) / (1000 if connector_name == "okx_perpetual" else 1)
            age = provider.time() - stamp
            price = Decimal(str(provider.get_price_by_type(connector_name, trading_pair, PriceType.MidPrice)))
            if stamp <= 0 or not -1 <= age <= float(self.strategy.config.mm_drawdown_max_data_age_seconds):
                raise DrawdownValuationUnavailable(connector_name, trading_pair, 'book_not_fresh', age)
            if not price.is_finite() or price <= 0:
                raise DrawdownValuationUnavailable(connector_name, trading_pair, 'invalid_mark_price', age)
            return price
        except DrawdownValuationUnavailable:
            raise
        except (ValueError, KeyError, TypeError, ArithmeticError, EnvironmentError):
            raise DrawdownValuationUnavailable(connector_name, trading_pair, 'book_or_price_unavailable') from None

    def mm_valuation_ready(self):
        if getattr(self, "mm_scope_incomplete", False):
            return False
        required = set()
        try:
            for identity, positions in self.positions_held.items():
                for position in positions:
                    amount = Decimal(str(position.net_amount_base))
                    if not amount.is_finite():
                        return False
                    if amount:
                        required.add((identity, position.connector_name, position.trading_pair))
            for identity, executors in self.active_executors.items():
                for executor in executors:
                    # Read current exposure without constructing PnL/custom DTOs.
                    # Retain the DTO-only adapter interface for legacy integrations.
                    filled = Decimal(str(executor.filled_amount_quote if isinstance(executor, ExecutorBase)
                                         else executor.executor_info.filled_amount_quote))
                    if not filled.is_finite():
                        return False
                    if not filled and isinstance(executor, PositionExecutor):
                        filled = Decimal(str(executor.open_filled_amount))
                        if not filled.is_finite():
                            return False
                    if filled:
                        required.add((identity, executor.config.connector_name, executor.config.trading_pair))
            for identity, connector, pair in required:
                self._mm_mark_price(identity, connector, pair)
        except (ValueError, ArithmeticError):
            return False
        return True

    def _initialize_cached_performance(self):
        """
        Initialize cached performance by querying the database for stored executors and positions.
        If initial positions are provided for a controller, skip loading database positions for that controller.
        """
        scope = self._mm_scope()
        controller_ids = list(scope or self.strategy.controllers)
        self.mm_scope_incomplete = bool(scope and MarketsRecorder.get_instance().has_uncovered_positions(controller_ids))
        for controller_id in controller_ids:
            if controller_id not in self.cached_performance:
                self.cached_performance[controller_id] = PerformanceReport()
                self.active_executors[controller_id] = []
                self.positions_held[controller_id] = []
        with closing(MarketsRecorder.get_instance().iter_executor_performance(controller_ids)) as db_executors:
            for executor in db_executors:
                controller_id = executor.controller_id
                if controller_id not in controller_ids:
                    continue
                self._update_cached_performance(controller_id, executor)

        # Load positions from database only for controllers without initial position overrides
        db_positions = MarketsRecorder.get_instance().get_all_positions(
            controller_ids=[cid for cid in controller_ids if cid not in self.initial_positions_by_controller])
        for position in db_positions:
            controller_id = position.controller_id
            # Skip if this controller has initial position overrides
            if controller_id in self.initial_positions_by_controller or controller_id not in controller_ids:
                continue
            # Skip if the connector/trading pair is not in the current strategy markets
            if scope and not self._mm_contains(controller_id, position.connector_name, position.trading_pair):
                self.mm_scope_incomplete = True
                continue
            if (not scope and (position.connector_name not in self.strategy.markets or
                               position.trading_pair not in self.strategy.markets.get(position.connector_name, set()))):
                self.logger().warning(f"Skipping position for {position.connector_name}.{position.trading_pair} - "
                                      f"not available in current strategy markets")
                continue
            self._load_position_from_db(controller_id, position)
        # Cost evidence only applies to retained perpetual holds. Keep Spot-only
        # and empty startup on their original two-query/zero-query paths. Even a
        # disabled reduction policy still restores an existing perpetual basis.
        perpetual_holds = any(hold.connector_name == 'okx_perpetual'
                              for holds in self.positions_held.values() for hold in holds)
        bases = MarketsRecorder.get_instance().get_reduction_bases() if perpetual_holds else {}
        if isinstance(bases, dict):
            for controller_id, holds in self.positions_held.items():
                for hold in holds:
                    key = (controller_id, hold.connector_name, hold.trading_pair, hold.side.name if hold.side else None)
                    basis = bases.get(key)
                    if (isinstance(basis, HedgeCostBasis) and basis.amount == abs(hold.net_amount_base)
                            and basis.leg == hold.side):
                        hold._reduction_basis = basis

    def _update_cached_performance(self, controller_id: str, executor_info: Union[ExecutorInfo, Executors, Row]):
        """
        Update the cached performance for a specific controller with an executor's information.
        """
        if controller_id not in self.cached_performance:
            self.cached_performance[controller_id] = PerformanceReport()
        report = self.cached_performance[controller_id]
        # Match Executors.to_executor_info() for the projected historical rows.
        close_type = CloseType(executor_info.close_type) if executor_info.close_type else None
        pnl, volume = Decimal(executor_info.net_pnl_quote), Decimal(executor_info.filled_amount_quote)
        # ExecutorInfo's Decimal validation previously rejected NaN/infinity.
        # Retain that fail-closed behavior without hydrating unrelated JSON fields.
        if not pnl.is_finite() or not volume.is_finite():
            raise ValueError("Executor performance must contain finite PnL and volume.")
        # Only add to realized PnL if not a position hold (consistent with generate_performance_report)
        if close_type != CloseType.POSITION_HOLD:
            report.realized_pnl_quote += pnl
            report.volume_traded += volume
        if close_type:
            report.close_type_counts[close_type] = report.close_type_counts.get(close_type, 0) + 1

    def _load_position_from_db(self, controller_id: str, db_position: Position):
        """
        Load a position from the database and recreate it as a PositionHold object.
        Since the database only stores net position data, we reconstruct the PositionHold
        with the assumption that it represents the remaining net position.
        """
        # Convert the database position back to a PositionHold object
        side = TradeType.BUY if db_position.side == "BUY" else TradeType.SELL
        position_hold = PositionHold(db_position.connector_name, db_position.trading_pair, side)

        # Set the aggregated values from the database
        position_hold.volume_traded_quote = db_position.volume_traded_quote
        position_hold.cum_fees_quote = db_position.cum_fees_quote

        # Restore net position and avg entry price from database
        if db_position.side == "BUY":
            position_hold.net_amount_base = db_position.amount
            position_hold.buy_amount_base = db_position.amount
            position_hold.buy_amount_quote = db_position.amount * db_position.breakeven_price
        else:
            position_hold.net_amount_base = -db_position.amount
            position_hold.sell_amount_base = db_position.amount
            position_hold.sell_amount_quote = db_position.amount * db_position.breakeven_price
        position_hold.avg_entry_price = db_position.breakeven_price
        # Restore realized PnL if available
        position_hold.realized_pnl_quote = getattr(db_position, 'realized_pnl_quote', Decimal("0"))
        position_hold.has_unstored_updates = False

        # Add to positions held
        self.positions_held[controller_id].append(position_hold)

    def initialize_initial_positions(self):
        """
        Initialize initial positions from config overrides.
        Must be called after connectors are ready (order books available).
        """
        if self._initial_positions_initialized:
            return
        self._initial_positions_initialized = True
        self._create_initial_positions()

    def _create_initial_positions(self):
        """
        Create initial positions from config overrides.
        Quote amounts are calculated using current mid_price.
        """
        for controller_id, initial_positions in self.initial_positions_by_controller.items():
            if controller_id not in self.cached_performance:
                self.cached_performance[controller_id] = PerformanceReport()
                self.active_executors[controller_id] = []
                self.positions_held[controller_id] = []

            for position_config in initial_positions:
                # Get mid_price for quote amount calculation
                mid_price = self.strategy.market_data_provider.get_price_by_type(
                    position_config.connector_name, position_config.trading_pair, PriceType.MidPrice
                )
                quote_amount = position_config.amount * mid_price

                # Create PositionHold object
                position_hold = PositionHold(
                    position_config.connector_name,
                    position_config.trading_pair,
                    position_config.side
                )

                # Set net position and avg entry price
                if position_config.side == TradeType.BUY:
                    position_hold.net_amount_base = position_config.amount
                    position_hold.buy_amount_base = position_config.amount
                    position_hold.buy_amount_quote = quote_amount
                else:
                    position_hold.net_amount_base = -position_config.amount
                    position_hold.sell_amount_base = position_config.amount
                    position_hold.sell_amount_quote = quote_amount
                position_hold.avg_entry_price = mid_price

                # Add to positions held
                self.positions_held[controller_id].append(position_hold)

                self.logger().info(f"Created initial position for controller {controller_id}: {position_config.amount} "
                                   f"{position_config.side.name} {position_config.trading_pair} on {position_config.connector_name}")

    def _all_executors_done(self) -> bool:
        return all(executor.executor_info.is_done
                   for executors_list in self.active_executors.values()
                   for executor in executors_list)

    def _executors_shutdown_signature(self) -> tuple:
        """
        Observable shutdown progress across all executors. Any change — a status or
        close_type transition, an executor finishing, or an internal state advance the
        executor reports through custom_info["state"] (e.g. an LP unwind moving from
        CLOSING to SWAPPING) — counts as progress and earns the shutdown wait more time.
        """
        signature = []
        for executors_list in self.active_executors.values():
            for executor in executors_list:
                info = executor.executor_info
                signature.append((
                    executor.config.id,
                    executor.status,
                    info.is_done,
                    executor.close_type,
                    str(info.custom_info.get("state")),
                ))
        return tuple(signature)

    async def stop(self, max_executors_close_attempts: int = 3, *, shutdown_timeout: Optional[float] = None,
                   keep_position: bool = False, before_position_checkpoint=None):
        """
        Stop the orchestrator task and all active executors.

        Executors that are already SHUTTING_DOWN keep the close_type their controller
        chose; the wait extends while executors make observable progress (an on-chain
        unwind takes several ticks); and any executor still unfinished at the deadline
        is force-stopped synchronously, converting whatever it executed into a
        position hold so no exposure goes untracked.
        """
        if keep_position and before_position_checkpoint is not None:
            candidates = {executor.config.id: executor for executor in self._checkpoint_candidates}
            candidates.update({executor.config.id: executor for rows in self.active_executors.values()
                               for executor in rows if not executor.is_closed})
            # These executors retain live InFlightOrder references after their
            # loops/listeners freeze. Other types retain the guarded legacy path.
            supported = all((isinstance(executor, PositionExecutor) or (
                isinstance(executor, OrderExecutor) and executor.config.mm_session_id is not None
                and executor.config.mm_generation is not None)) and not executor._held_position_orders
                for executor in candidates.values())
            if self._checkpoint_candidates and not supported:
                raise ValueError('Pending position checkpoint cannot be finalized safely')
            if supported:
                self._checkpoint_candidates = list(candidates.values())
                orders = {}
                for executor in self._checkpoint_candidates:
                    references = ((*executor._failed_orders, executor._open_order,
                                   executor._close_order, executor._take_profit_limit_order)
                                  if isinstance(executor, PositionExecutor) else
                                  (*executor._failed_orders, *executor._canceled_orders,
                                   *executor._partial_filled_orders, executor._order))
                    for tracked in references:
                        if tracked is None or not tracked.order_id:
                            continue
                        if tracked.order is None:
                            tracked.order = executor.get_in_flight_order(executor.config.connector_name, tracked.order_id)
                        if tracked.order is None:
                            raise ValueError('Terminal position checkpoint order is unavailable')
                        orders[tracked.order_id] = (executor.config.connector_name, tracked.order)
                    # Freeze control loops/listeners, but retain order objects.
                    # None prevents report ticks from committing an unfinished
                    # POSITION_HOLD before terminal trade proof arrives.
                    executor.stop()
                    executor.close_type = None
                proof = await before_position_checkpoint(orders)
                if not isinstance(proof, dict) or not orders.keys() <= proof.keys():
                    raise ValueError('Terminal position checkpoint proof is unavailable')
                if any(not order.is_done for _, order in orders.values()):
                    raise ValueError('Terminal position checkpoint still has live orders')
                terminal_states = {str(state.value) for state in (OrderState.CANCELED, OrderState.FILLED, OrderState.FAILED)}
                # Validate every candidate before making any POSITION_HOLD visible
                # to report ticks. No await separates proof, snapshots and storage.
                held = {order['client_order_id']: order for executor in self._checkpoint_candidates
                        for order in executor._collect_held_position_orders()}
                for order_id, expected in proof.items():
                    if order_id not in orders:
                        continue
                    saved = held.get(order_id, {})
                    if (expected.get('last_state') not in terminal_states
                            or Decimal(str(saved.get('executed_amount_base', 0))) != Decimal(expected['executed_amount_base'])
                            or Decimal(str(saved.get('executed_amount_quote', 0))) != Decimal(expected['executed_amount_quote'])
                            or saved.get('order_fills', {}) != expected.get('order_fills', {})):
                        raise ValueError('Position hold does not match terminal order proof')
                for executor in self._checkpoint_candidates:
                    executor.force_stop_with_position_hold()
                self._checkpoint_candidates = []

        for controller_id, executors_list in self.active_executors.items():
            for executor in executors_list:
                if keep_position and not executor.is_closed:
                    executor.force_stop_with_position_hold()
                    continue
                if executor.is_closed or executor.status == RunnableStatus.SHUTTING_DOWN:
                    # A SHUTTING_DOWN executor already had its close_type chosen (by a
                    # StopExecutorAction or its own logic). Calling early_stop again
                    # would overwrite it with this type's default keep_position — e.g.
                    # flipping an LP mid-unwind from EARLY_STOP to POSITION_HOLD, which
                    # silently skips its close-out swap.
                    continue
                executor.early_stop()

        # Wait for executors to finish, extending the deadline while any of them makes
        # observable progress. max_executors_close_attempts keeps its historical
        # meaning as a budget of ~2s units, but the budget now bounds *stall* time
        # rather than total time: a draining grid or a mid-swap LP keeps earning time,
        # while a hung executor still hits the deadline.
        poll_interval = 1.0
        stall_budget = max_executors_close_attempts * 2.0
        hard_cap = max(30.0, stall_budget)
        if shutdown_timeout is not None:
            hard_cap = max(0.0, shutdown_timeout)
        elapsed = stalled = 0.0
        last_signature = None
        while not self._all_executors_done():
            if stalled >= stall_budget or elapsed >= hard_cap:
                break
            if last_signature is None:
                last_signature = self._executors_shutdown_signature()
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval
            signature = self._executors_shutdown_signature()
            if signature != last_signature:
                stalled = 0.0
                last_signature = signature
            else:
                stalled += poll_interval

        unfinished_executors = [
            (controller_id, executor)
            for controller_id, executors_list in self.active_executors.items()
            for executor in executors_list
            if not executor.executor_info.is_done
        ]
        if unfinished_executors:
            executor_ids = [executor.config.id for _, executor in unfinished_executors]
            self.logger().error(
                f"Executors {executor_ids} did not finish closing before shutdown. "
                f"Force-stopping them and converting executed exposure into position holds.")
            for controller_id, executor in unfinished_executors:
                try:
                    executor.force_stop_with_position_hold()
                except Exception:
                    self.logger().exception(
                        f"Error forcing executor {executor.config.id} for controller {controller_id} to stop.")

        # Convert executors that ended holding exposure into persisted position records
        # while connector prices and strategy market registrations are still available.
        self._update_positions_from_done_executors()
        # Store all positions and executors
        self.store_all_positions()
        self.store_all_executors()
        # Clear executors and trigger garbage collection
        self.active_executors.clear()

    def store_all_positions(self):
        """
        Store or update all positions in the database.
        """
        markets_recorder = MarketsRecorder.get_instance()
        snapshots = []
        for controller_id, positions_list in self.positions_held.items():
            if controller_id is None:
                self.logger().warning(f"Skipping {len(positions_list)} position(s) with no controller_id")
                continue
            for position in positions_list:
                # Skip if the connector/trading pair is not in the current strategy markets
                if (not self._mm_contains(controller_id, position.connector_name, position.trading_pair)
                        and (position.connector_name not in self.strategy.markets or
                             position.trading_pair not in self.strategy.markets.get(position.connector_name, set()))):
                    self.logger().warning(f"Skipping position storage for {position.connector_name}.{position.trading_pair} - "
                                          f"not available in current strategy markets")
                    continue
                try:
                    mid_price = self.strategy.market_data_provider.get_price_by_type(
                        position.connector_name, position.trading_pair, PriceType.MidPrice)
                    if self._mm_scope() and (not mid_price.is_finite() or mid_price <= 0):
                        raise ValueError("drawdown_checkpoint_mark_unavailable")
                except (ValueError, KeyError, EnvironmentError):
                    if self._mm_scope() and not position.has_unstored_updates:
                        continue  # An unchanged loaded row already preserves this exposure.
                    raise
                position_summary = position.get_position_summary(mid_price if not mid_price.is_nan() else Decimal("0"))

                # Create a Position record (id will only be used for new positions)
                position_record = Position(
                    id=str(uuid.uuid4()),
                    controller_id=controller_id,
                    connector_name=position_summary.connector_name,
                    trading_pair=position_summary.trading_pair,
                    side=position_summary.side.name,
                    # Startup recovery can fail before the strategy receives its
                    # first clock tick. Persist held exposure with an actual UTC
                    # observation time rather than converting the NaN sentinel.
                    timestamp=int((self.strategy.current_timestamp if math.isfinite(self.strategy.current_timestamp)
                                   else self.strategy.market_data_provider.time()) * 1e3),
                    volume_traded_quote=position_summary.volume_traded_quote,
                    amount=position_summary.amount,
                    breakeven_price=position_summary.breakeven_price,
                    unrealized_pnl_quote=position_summary.unrealized_pnl_quote,
                    realized_pnl_quote=position_summary.realized_pnl_quote,
                    cum_fees_quote=position_summary.cum_fees_quote,
                )
                snapshots.append(position_record)

        # Validate/prepare every loop-owned mark before any write. One failed
        # batch must leave all holds resident for recovery, rather than commit
        # a prefix of the checkpoint or clear exposure before its acknowledgment.
        markets_recorder.update_or_store_positions(snapshots)
        # Clear all positions after storing (avoid modifying list while iterating)
        self.positions_held.clear()

    def store_all_executors(self):
        pending = [(controller_id, executor) for controller_id, executors in self.active_executors.items()
                   for executor in executors]
        if pending:
            MarketsRecorder.get_instance().store_or_update_executors([executor for _, executor in pending])
        # Only committed rows may advance cached performance or leave memory.
        for controller_id, executor in pending:
            self._update_cached_performance(controller_id, executor.executor_info)
        self.active_executors = {}
        self.executors_ids_position_held.clear()

    def execute_action(self, action: ExecutorAction):
        """
        Execute the action and handle executors based on action type.
        """
        controller_id = action.controller_id
        if controller_id is None:
            self.logger().error(f"Received action with controller_id=None: {action}. "
                                "Check that the controller config has a valid 'id' field.")
            return
        if controller_id not in self.cached_performance:
            self.active_executors[controller_id] = []
            self.positions_held[controller_id] = []
            self.cached_performance[controller_id] = PerformanceReport()

        if isinstance(action, CreateExecutorAction):
            self.create_executor(action)
        elif isinstance(action, StopExecutorAction):
            self.stop_executor(action)
        elif isinstance(action, StoreExecutorAction):
            self.store_executor(action)

    def execute_actions(self, actions: List[ExecutorAction]):
        """
        Execute a list of actions.
        """
        for action in actions:
            self.execute_action(action)

    def create_executor(self, action: CreateExecutorAction):
        """
        Create an executor based on the configuration in the action.
        """
        controller_id = action.controller_id
        executor_config = action.executor_config

        # For now, we replace the controller ID in the executor config with the actual controller object to mantain
        # compa
        executor_config.controller_id = controller_id

        executor_class = self._executor_mapping.get(executor_config.type)
        if executor_class is not None:
            executor = executor_class(
                strategy=self.strategy,
                config=executor_config,
                update_interval=self.executors_update_interval,
                max_retries=self.executors_max_retries,
            )
        else:
            raise ValueError("Unsupported executor config type")

        executor.start()
        self.active_executors[controller_id].append(executor)
        # MarketsRecorder.get_instance().store_or_update_executor(executor)
        self.logger().debug(f"Created {type(executor).__name__} for controller {controller_id}")

    def stop_executor(self, action: StopExecutorAction):
        """
        Stop an executor based on the action details.
        """
        controller_id = action.controller_id
        executor_id = action.executor_id

        executor = next(
            (executor for executor in self.active_executors[controller_id] if executor.config.id == executor_id),
            None)
        if not executor:
            self.logger().error(f"Executor ID {executor_id} not found for controller {controller_id}.")
            return
        # Actions are queued from report snapshots. A delayed duplicate must
        # not turn a terminated task back into SHUTTING_DOWN: its loop has
        # already exited and cannot release its reservation a second time.
        if executor.is_closed:
            return
        executor.early_stop(action.keep_position)

    def _update_positions_from_done_executors(self, executor_reports=None):
        recorder = MarketsRecorder._shared_instance
        reports = executor_reports if executor_reports is not None else self.get_executors_report()
        # Session durability is independent of the optional drawdown policy.
        # Preserve the legacy path for executors outside managed MM sessions.
        managed = self._mm_scope() or any(
            getattr(info.config, 'mm_session_id', None) is not None
            and getattr(info.config, 'mm_generation', None) is not None
            for infos in reports.values() for info in infos)
        durable = managed and recorder is not None and recorder.durable_position_checkpoints is True
        if not durable:
            return self._apply_position_updates(reports)
        candidates = [info for infos in reports.values() for info in infos
                      if info.is_done and info.close_type == CloseType.POSITION_HOLD
                      and info.id not in self.executors_ids_position_held]
        if not candidates:
            return self._apply_position_updates(reports)
        applied = recorder.applied_position_executors([info.id for info in candidates])
        old_positions, old_ids = self.positions_held, self.executors_ids_position_held
        self.positions_held = deepcopy(old_positions)
        self.executors_ids_position_held = set(old_ids) | applied
        try:
            self._apply_position_updates(reports)
            fresh = [info for info in candidates if info.id not in applied]
            if fresh:
                snapshots = []
                for controller_id, holds in self.positions_held.items():
                    for held in holds:
                        summary = held.get_position_summary(held.avg_entry_price)
                        snapshots.append(Position(id=str(uuid.uuid4()), controller_id=controller_id,
                                                  timestamp=int(self.strategy.market_data_provider.time() * 1000),
                                                  **{key: (value.name if key == 'side' else value)
                                                     for key, value in summary.model_dump().items()}))
                bases = {(controller_id, hold.connector_name, hold.trading_pair, hold.side.name): hold.reduction_basis
                         for controller_id, holds in self.positions_held.items() for hold in holds
                         if hold.reduction_basis is not None}
                recorder.checkpoint_held_positions(snapshots, fresh, reduction_bases=bases)
        except BaseException:
            self.positions_held, self.executors_ids_position_held = old_positions, old_ids
            raise

    def _apply_position_updates(self, executor_reports=None):
        """
        Update positions from executors that are done but haven't been processed yet.
        This is called before generating reports to ensure position state is current.
        """
        resident_ids = {executor.config.id for rows in self.active_executors.values() for executor in rows}
        self.executors_ids_position_held = set(self.executors_ids_position_held) & resident_ids
        if executor_reports is None:
            executor_reports = self.get_executors_report()
        for controller_id, executor_infos in executor_reports.items():
            # Filter executors that need position updates
            executors_to_process = [
                info for info in executor_infos
                if (info.is_done and info.close_type == CloseType.POSITION_HOLD and
                    info.config.id not in self.executors_ids_position_held)
            ]

            # Skip if no executors to process
            if not executors_to_process:
                continue

            positions = self.positions_held.get(controller_id, [])

            for executor_info in executors_to_process:
                self.executors_ids_position_held.add(executor_info.config.id)

                # Determine position side (handling perpetual markets)
                position_side = self._determine_position_side(executor_info)

                held_orders = executor_info.custom_info.get("held_position_orders", [])
                held_summary = [
                    f"{o.get('trade_type')}:{o.get('executed_amount_base')}@{o.get('position', 'N/A')}"
                    for o in held_orders
                ]

                self.logger().debug(
                    f"Processing POSITION_HOLD executor: {executor_info.id[:8]} | "
                    f"controller={controller_id} | side={executor_info.config.side} | "
                    f"level={executor_info.custom_info.get('level_id', 'N/A')} | "
                    f"position_side_resolved={position_side} | "
                    f"held_orders({len(held_orders)}): {held_summary} | "
                    f"existing_positions={len(positions)}"
                )

                # Find or create position
                existing_position = self._find_existing_position(positions, executor_info, position_side)

                config = executor_info.config
                if (isinstance(config, OrderExecutorConfig) and config.position_action == PositionAction.CLOSE
                        and config.mm_session_id is not None and config.connector_name == 'okx_perpetual'):
                    from hummingbot.core.order_submission_guard import SubmissionBlocked
                    amounts = [Decimal(str(order.get('executed_amount_base', 0))) for order in held_orders]
                    closed = sum(amounts, Decimal(0))
                    valid = (all(amount.is_finite() and amount >= 0 for amount in amounts)
                             and all(order.get('position') == 'CLOSE' and order.get('trade_type') == config.side.name
                                     for order in held_orders))
                    if (not valid or closed and (existing_position is None or position_side is None
                                                 or existing_position.net_amount_base * (1 if position_side == TradeType.BUY else -1) <= 0
                                                 or closed > abs(existing_position.net_amount_base))):
                        raise SubmissionBlocked('recovery_position_application_conflict')
                    if not closed:
                        continue

                if existing_position:
                    existing_position.add_orders_from_executor(executor_info)
                    self.logger().debug(
                        f"Merged executor {executor_info.id[:8]} into existing position: "
                        f"net={existing_position.net_amount_base} avg_entry={existing_position.avg_entry_price:.4f}"
                    )
                else:
                    # Create new position (handles both spot/perp and LP)
                    assigned_side = position_side
                    self.logger().debug(
                        f"Creating new PositionHold for executor {executor_info.id[:8]} with side={assigned_side}"
                    )
                    position = PositionHold(
                        executor_info.connector_name,
                        executor_info.trading_pair,
                        assigned_side
                    )
                    position.add_orders_from_executor(executor_info)
                    positions.append(position)

    def _determine_position_side(self, executor_info: ExecutorInfo) -> Optional[TradeType]:
        """
        Determine the position side used to bucket a position hold.

        Only perpetual markets in HEDGE mode can hold a long and a short position
        simultaneously, so only in that case do we return a specific side (allowing
        a separate long and short position hold per trading pair). For spot markets
        and perpetual markets in ONEWAY mode there can only be a single net position
        per trading pair, so we return None and let all activity merge into one
        PositionHold (whose net direction is derived from its buy/sell amounts).
        """
        is_perpetual = "_perpetual" in executor_info.connector_name
        if not is_perpetual:
            return None

        market = self.strategy.connectors.get(executor_info.connector_name)
        if not market or not hasattr(market, 'position_mode'):
            return None

        position_mode = market.position_mode
        if position_mode == PositionMode.HEDGE and isinstance(executor_info.config, PositionExecutorConfig):
            # A PositionExecutor entry always opens its configured direction.
            # It has no position_action field; netting its independent hedge
            # legs would lose exposure on stop/restart and fail reconciliation.
            return executor_info.config.side

        if hasattr(executor_info.config, "position_action") and position_mode == PositionMode.HEDGE:
            opposite_side = TradeType.BUY if executor_info.config.side == TradeType.SELL else TradeType.SELL
            return opposite_side if executor_info.config.position_action == PositionAction.CLOSE else executor_info.config.side

        # Spot or perpetual ONEWAY: a single net position per trading pair (one side at a time).
        return None

    def _find_existing_position(self, positions: List[PositionHold],
                                executor_info: ExecutorInfo,
                                position_side: Optional[TradeType]) -> Optional[PositionHold]:
        """
        Find an existing position that matches the executor's trading pair and side.
        """
        for position in positions:
            if (position.trading_pair == executor_info.trading_pair and
                    position.connector_name == executor_info.connector_name):

                # If we have a specific position side, match it
                if position_side is not None:
                    if position.side == position_side:
                        return position
                else:
                    # No specific side requirement, return first match
                    return position

        return None

    def store_executor(self, action: StoreExecutorAction):
        """
        Store executor data based on the action details and update cached performance.
        """
        controller_id = action.controller_id
        executor_id = action.executor_id

        executor = next(
            (executor for executor in self.active_executors[controller_id] if executor.config.id == executor_id),
            None)
        if not executor:
            self.logger().error(f"Executor ID {executor_id} not found for controller {controller_id}.")
            return
        if executor.is_active:
            self.logger().error(f"Executor ID {executor_id} is still active.")
            return
        try:
            MarketsRecorder.get_instance().store_or_update_executor(executor)
            self._update_cached_performance(controller_id, executor.executor_info)
        except Exception as e:
            self.logger().error(f"Error storing executor id {executor_id}: {str(e)}.")
            self.logger().error(f"Executor info: {executor.executor_info} | Config: {executor.config}")
            return  # Keep both exposure and deduplication state for the retry.

        self.active_executors[controller_id].remove(executor)
        self.executors_ids_position_held = set(self.executors_ids_position_held) - {executor_id}
        # Position totals already include retired fills. Their identities only
        # need to outlive resident executors that might be visited again.
        retained_orders = {order.get("client_order_id")
                           for remaining in self.active_executors[controller_id]
                           for order in remaining.executor_info.custom_info.get("held_position_orders", [])}
        for position in self.positions_held.get(controller_id, []):
            position.order_ids.intersection_update(retained_orders)
        del executor
        # Trigger garbage collection after executor cleanup

    def get_executors_report(self) -> Dict[str, List[ExecutorInfo]]:
        """
        Generate a report of all executors.
        """
        report = {}
        for controller_id, executors_list in self.active_executors.items():
            report[controller_id] = [executor.executor_info for executor in executors_list if executor]
        return report

    def get_positions_report(self) -> Dict[str, List[PositionSummary]]:
        """
        Generate a report of all positions held.
        """
        report = {}
        for controller_id, positions_list in self.positions_held.items():
            positions_summary = []
            for position in positions_list:
                if self._mm_scope():
                    mid_price = self._mm_mark_price(controller_id, position.connector_name, position.trading_pair) if position.net_amount_base else Decimal("0")
                else:
                    mid_price = self.strategy.market_data_provider.get_price_by_type(
                        position.connector_name, position.trading_pair, PriceType.MidPrice)
                positions_summary.append(position.get_position_summary(mid_price if not mid_price.is_nan() else Decimal("0")))
            report[controller_id] = positions_summary
        return report

    def get_all_reports(self) -> Dict[str, Dict]:
        """
        Generate a unified report containing executors, positions, and performance for all controllers.
        Returns a dictionary with controller_id as key and a dict containing all reports as value.
        """
        if getattr(self, "mm_scope_incomplete", False):
            raise ValueError("drawdown_position_scope_unavailable")
        # A synchronous report uses one DTO per resident executor for hold
        # transitions, display and PnL. Nothing is retained for the next call.
        executors_report = self.get_executors_report()
        self._update_positions_from_done_executors(executors_report)
        positions_report = self.get_positions_report()

        # Get all controller IDs
        all_controller_ids = set(list(self.active_executors.keys()) +
                                 list(self.positions_held.keys()) +
                                 list(self.cached_performance.keys()))

        # Use dict comprehension to compile reports for each controller
        return {
            controller_id: {
                "executors": executors_report.get(controller_id, []),
                "positions": positions_report.get(controller_id, []),
                "performance": self.generate_performance_report(
                    controller_id, executor_infos=executors_report.get(controller_id, []),
                    position_summaries=positions_report.get(controller_id, []))
            }
            for controller_id in all_controller_ids
        }

    def _active_reduction_adjustment(self, controller_id, info, reserved):
        """Replace a filled close's old mark contribution until its atomic hold transfer.

        The owned hold stays unchanged for risk/capacity authority. This report-only
        correction removes that filled quantity's marked PnL and adds its original
        execution proceeds/cost and closing fee. Terminal holds already include it.
        """
        from hummingbot.model.runtime_journal import held_order_snapshot
        config = info.config
        executor = next((item for item in self.active_executors.get(controller_id, [])
                         if item.config.id == config.id and isinstance(item, OrderExecutor)), None)
        if executor is None:
            raise ValueError('drawdown_reduction_evidence_unavailable')
        amount, quote, fee = Decimal(0), Decimal(0), Decimal(0)
        for tracked in executor._execution_orders(include_retired=True):
            order = tracked.order or executor.get_in_flight_order(config.connector_name, tracked.order_id)
            if order is None:
                if tracked.executed_amount_base:
                    raise ValueError('drawdown_reduction_evidence_unavailable')
                continue
            if not order.executed_amount_base:
                continue
            if order.position != PositionAction.CLOSE or order.trade_type != config.side:
                raise ValueError('drawdown_reduction_evidence_unavailable')
            saved = held_order_snapshot(order)
            amount += order.executed_amount_base
            quote += order.executed_amount_quote
            fee += Decimal(saved['cumulative_fee_paid_quote'])
        if quote != info.filled_amount_quote or amount > config.amount:
            raise ValueError('drawdown_reduction_evidence_unavailable')
        if not amount:
            return Decimal(0)
        leg = TradeType.BUY if config.side == TradeType.SELL else TradeType.SELL
        held = [hold for hold in self.positions_held.get(controller_id, [])
                if (hold.connector_name, hold.trading_pair, hold.side) == (config.connector_name, config.trading_pair, leg)]
        key = config.connector_name, config.trading_pair, leg
        reserved[key] = reserved.get(key, Decimal(0)) + amount
        if len(held) != 1 or reserved[key] > abs(held[0].net_amount_base):
            raise ValueError('drawdown_reduction_evidence_unavailable')
        mark = self._mm_mark_price(controller_id, config.connector_name, config.trading_pair)
        return (quote - amount * mark) * (1 if config.side == TradeType.SELL else -1) - fee

    def generate_performance_report(self, controller_id: str, *, executor_infos=None,
                                    position_summaries=None) -> PerformanceReport:
        # Create a new report starting from cached base values
        report = PerformanceReport()
        # The fresh output already supplies zero defaults on a cache miss.
        cached_report = self.cached_performance.get(controller_id, report)

        # Start with cached values (from DB)
        report.realized_pnl_quote = cached_report.realized_pnl_quote
        report.volume_traded = cached_report.volume_traded
        report.close_type_counts = cached_report.close_type_counts.copy() if cached_report.close_type_counts else {}

        # Add data from active executors
        if executor_infos is None:
            executor_infos = (executor.executor_info for executor in self.active_executors.get(controller_id, []))
        positions = self.positions_held.get(controller_id, []) if position_summaries is None else position_summaries

        reductions = {}
        for executor_info in executor_infos:
            if not executor_info.is_done:
                if self._mm_scope() and executor_info.filled_amount_quote:
                    self._mm_mark_price(controller_id, executor_info.config.connector_name, executor_info.config.trading_pair)
                report.unrealized_pnl_quote += executor_info.net_pnl_quote
                config = executor_info.config
                if (self._mm_scope() and isinstance(config, OrderExecutorConfig)
                        and config.mm_session_id is not None and config.position_action == PositionAction.CLOSE
                        and config.connector_name == 'okx_perpetual'):
                    report.unrealized_pnl_quote += self._active_reduction_adjustment(controller_id, executor_info, reductions)
                report.volume_traded += executor_info.filled_amount_quote
            else:
                # For done executors, only add to realized PnL if they're not already in position holds
                # Position holds will be counted separately to avoid double counting
                if executor_info.close_type != CloseType.POSITION_HOLD:
                    report.realized_pnl_quote += executor_info.net_pnl_quote
                    report.volume_traded += executor_info.filled_amount_quote
                if executor_info.close_type:
                    report.close_type_counts[executor_info.close_type] = report.close_type_counts.get(executor_info.close_type, 0) + 1

        # Add data from positions held and collect position summaries
        positions_summary = []
        for position in positions:
            # Skip if the connector/trading pair is not in the current strategy markets
            if (not self._mm_contains(controller_id, position.connector_name, position.trading_pair)
                    and (position.connector_name not in self.strategy.markets or
                         position.trading_pair not in self.strategy.markets.get(position.connector_name, set()))):
                self.logger().warning(f"Skipping position in performance report for {position.connector_name}.{position.trading_pair} - "
                                      f"not available in current strategy markets")
                continue
            if position_summaries is not None:
                position_summary = position
            else:
                if self._mm_scope():
                    mid_price = self._mm_mark_price(controller_id, position.connector_name, position.trading_pair) if position.net_amount_base else Decimal("0")
                else:
                    mid_price = self.strategy.market_data_provider.get_price_by_type(
                        position.connector_name, position.trading_pair, PriceType.MidPrice)
                position_summary = position.get_position_summary(mid_price if not mid_price.is_nan() else Decimal("0"))

            # Update report with position data
            # PositionHold realizes trading PnL and stores fees separately. The
            # managed MM adapter matches active/cached net-of-trade-fee reports;
            # legacy report behavior remains unchanged.
            report.realized_pnl_quote += position_summary.realized_pnl_quote
            if self._mm_scope():
                report.realized_pnl_quote -= position_summary.cum_fees_quote
            report.volume_traded += position_summary.volume_traded_quote
            report.unrealized_pnl_quote += position_summary.unrealized_pnl_quote
            positions_summary.append(position_summary)

        # Set the positions summary (don't use dynamic attribute)
        report.positions_summary = positions_summary

        # Calculate global PNL values
        report.global_pnl_quote = report.unrealized_pnl_quote + report.realized_pnl_quote
        report.global_pnl_pct = (report.global_pnl_quote / report.volume_traded) * 100 if report.volume_traded != 0 else Decimal(0)

        # Calculate individual PNL percentages
        report.unrealized_pnl_pct = (report.unrealized_pnl_quote / report.volume_traded) * 100 if report.volume_traded != 0 else Decimal(0)
        report.realized_pnl_pct = (report.realized_pnl_quote / report.volume_traded) * 100 if report.volume_traded != 0 else Decimal(0)

        return report
