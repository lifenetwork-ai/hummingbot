import asyncio
import logging
import math
from collections import defaultdict
from copy import copy
from decimal import Decimal
from itertools import chain
from typing import TYPE_CHECKING, Callable, Dict, NamedTuple, Optional, Union
from weakref import WeakValueDictionary

from cachetools import TTLCache

from hummingbot.core.data_type.common import TradeType
from hummingbot.core.data_type.in_flight_order import InFlightOrder, OrderState, OrderUpdate, TradeUpdate
from hummingbot.core.data_type.trade_fee import TradeFeeBase
from hummingbot.core.engine_diagnostics import observe_spot_quote_open, trace_runtime
from hummingbot.core.event.events import (
    BuyOrderCompletedEvent,
    BuyOrderCreatedEvent,
    MarketEvent,
    MarketOrderFailureEvent,
    OrderCancelledEvent,
    OrderFilledEvent,
    SellOrderCompletedEvent,
    SellOrderCreatedEvent,
)
from hummingbot.core.utils.async_utils import safe_ensure_future
from hummingbot.logger.logger import HummingbotLogger

if TYPE_CHECKING:
    from hummingbot.connector.connector_base import ConnectorBase

cot_logger = None


class _StagedOrderAcknowledgement(NamedTuple):
    tag: MarketEvent
    event: Union[BuyOrderCreatedEvent, SellOrderCreatedEvent, OrderCancelledEvent]
    tracking_state: dict
    apply: Callable
    publish: Callable


class ClientOrderTracker:

    MAX_CACHE_SIZE = 1000
    CACHED_ORDER_TTL = 30.0  # seconds
    TRADE_FILLS_WAIT_TIMEOUT = 5  # seconds

    @classmethod
    def logger(cls) -> HummingbotLogger:
        global cot_logger
        if cot_logger is None:
            cot_logger = logging.getLogger(__name__)
        return cot_logger

    def __init__(self, connector: "ConnectorBase", lost_order_count_limit: int = 3) -> None:
        """
        Provides utilities for connectors to update in-flight orders and also handle order errors.
        Also it maintains cached orders to allow for additional updates to occur after the original order
        is determined to no longer be active.
        An error constitutes, but is not limited to, the following:
        (1) Order not found on exchange.
        (2) Cannot retrieve exchange_order_id of an order
        (3) Error thrown by exchange when fetching order status
        """
        self._connector: ConnectorBase = connector
        self._lost_order_count_limit = lost_order_count_limit
        self._in_flight_orders: Dict[str, InFlightOrder] = {}
        self._cached_orders: TTLCache = TTLCache(maxsize=self.MAX_CACHE_SIZE, ttl=self.CACHED_ORDER_TTL)
        self._lost_orders: Dict[str, InFlightOrder] = {}
        # Only in-flight event tasks retain the per-order locks.
        self._order_event_locks = WeakValueDictionary()

        self._order_tracking_task: Optional[asyncio.Task] = None
        self._last_poll_timestamp: int = -1
        self._order_not_found_records: Dict[str, int] = defaultdict(lambda: 0)

    @property
    def active_orders(self) -> Dict[str, InFlightOrder]:
        """
        Returns orders that are actively tracked
        """
        return self._in_flight_orders

    @property
    def cached_orders(self) -> Dict[str, InFlightOrder]:
        """
        Returns orders that are no longer actively tracked.
        """
        return {client_order_id: order for client_order_id, order in self._cached_orders.items()}

    @property
    def all_orders(self) -> Dict[str, InFlightOrder]:
        """
        Returns both active and cached order.
        """
        return {**self.active_orders, **self.cached_orders}

    @property
    def all_fillable_orders(self) -> Dict[str, InFlightOrder]:
        """
        Returns all orders that could still be impacted by trades: active orders, cached orders and lost orders
        """
        return {**self.active_orders, **self.cached_orders, **self.lost_orders}

    @property
    def all_fillable_orders_by_exchange_order_id(self) -> Dict[str, InFlightOrder]:
        """
        Same as `all_fillable_orders`, but the orders are mapped by exchange order ID.
        """
        orders_map = {
            order.exchange_order_id: order
            for order in chain(self.active_orders.values(), self.cached_orders.values(), self.lost_orders.values())
        }
        return orders_map

    @property
    def all_updatable_orders(self) -> Dict[str, InFlightOrder]:
        """
        Returns all orders that could receive status updates
        """
        return {**self.active_orders, **self.lost_orders}

    @property
    def all_updatable_orders_by_exchange_order_id(self) -> Dict[str, InFlightOrder]:
        """
        Same as `all_updatable_orders`, but the orders are mapped by exchange order ID.
        """
        orders_map = {
            order.exchange_order_id: order for order in chain(self.active_orders.values(), self.lost_orders.values())
        }
        return orders_map

    @property
    def current_timestamp(self) -> int:
        """
        Returns current timestamp in seconds.
        """
        return self._connector.current_timestamp

    @property
    def lost_orders(self) -> Dict[str, InFlightOrder]:
        """
        Returns a dictionary of all orders marked as failed after not being found more times than the configured limit
        """
        return {client_order_id: order for client_order_id, order in self._lost_orders.items()}

    @property
    def lost_order_count_limit(self) -> int:
        return self._lost_order_count_limit

    @lost_order_count_limit.setter
    def lost_order_count_limit(self, value: int):
        self._lost_order_count_limit = value

    def start_tracking_order(self, order: InFlightOrder):
        self._in_flight_orders[order.client_order_id] = order

    def stop_tracking_order(self, client_order_id: str):
        if client_order_id in self._in_flight_orders:
            self._cached_orders[client_order_id] = self._in_flight_orders[client_order_id]
            del self._in_flight_orders[client_order_id]
            if client_order_id in self._order_not_found_records:
                del self._order_not_found_records[client_order_id]

    def restore_tracking_states(self, tracking_states: Dict[str, any]):
        """
        Restore in-flight orders from saved tracking states.
        :param tracking_states: a dictionary associating order ids with the serialized order (JSON format).
        """
        for serialized_order in tracking_states.values():
            order = self._restore_order_from_json(serialized_order=serialized_order)
            if order.is_open:
                self.start_tracking_order(order)
            elif order.is_failure:
                # If the order is marked as failed but is still in the tracking states, it was a lost order
                self._lost_orders[order.client_order_id] = order

    def fetch_tracked_order(self, client_order_id: str) -> Optional[InFlightOrder]:
        return self._in_flight_orders.get(client_order_id, None)

    def fetch_cached_order(self, client_order_id: str) -> Optional[InFlightOrder]:
        return self._cached_orders.get(client_order_id, None)

    def restore_terminal_checkpoint_order(self, order: InFlightOrder) -> None:
        """Make the executor's retained order visible to native proof updates."""
        identity = order.client_order_id
        current = self._in_flight_orders.get(identity) or self._cached_orders.get(identity)
        if current is order:
            return
        if (current is not None or identity in self._lost_orders
                or order.current_state not in (OrderState.CANCELED, OrderState.FILLED, OrderState.FAILED)):
            raise ValueError("Terminal checkpoint order conflicts with native tracking")
        self._cached_orders[identity] = order

    def fetch_order(
        self, client_order_id: Optional[str] = None, exchange_order_id: Optional[str] = None
    ) -> Optional[InFlightOrder]:
        # Match the snapshot's precedence without building it for an exact ID.
        found_order = self._cached_orders.get(client_order_id, self._in_flight_orders.get(client_order_id))
        if found_order is None and exchange_order_id is not None:
            return next(
                (order for order in self.all_orders.values() if order.exchange_order_id == exchange_order_id), None
            )
        return found_order

    def fetch_lost_order(
        self, client_order_id: Optional[str] = None, exchange_order_id: Optional[str] = None
    ) -> Optional[InFlightOrder]:
        found_order = None

        if client_order_id in self._lost_orders:
            found_order = self._lost_orders[client_order_id]
        elif exchange_order_id is not None:
            found_order = next(
                (order for order in self._lost_orders.values() if order.exchange_order_id == exchange_order_id),
                None)

        return found_order

    def process_order_update(self, order_update: OrderUpdate):
        return safe_ensure_future(self._process_order_update(order_update))

    def process_trade_update(self, trade_update: TradeUpdate):
        client_order_id: str = trade_update.client_order_id
        tracked_order: Optional[InFlightOrder] = self.all_fillable_orders.get(client_order_id)

        if tracked_order:
            previous_executed_amount_base: Decimal = tracked_order.executed_amount_base
            updated: bool = tracked_order.update_with_trade_update(trade_update)
            if updated:
                self._trigger_order_fills(
                    tracked_order=tracked_order,
                    prev_executed_amount_base=previous_executed_amount_base,
                    fill_amount=trade_update.fill_base_amount,
                    fill_price=trade_update.fill_price,
                    fill_fee=trade_update.fee,
                    trade_id=trade_update.trade_id,
                    exchange_order_id=trade_update.exchange_order_id,
                    fill_timestamp=trade_update.fill_timestamp,
                )

    async def apply_trade_update(self, update: TradeUpdate):
        from hummingbot.connector.markets_recorder import MarketsRecorder
        recorder = MarketsRecorder.service_recorder_for(self._connector)
        if recorder is None:
            self.process_trade_update(update)
            return
        with recorder.native_order_activity() as owner:
            async with (self._order_event_lock(update.client_order_id),
                        recorder.native_order_lock(self._connector, owner, operation='fill')):
                await self._apply_recorded_trade_update(recorder, update)

    def _order_event_lock(self, identity):
        lock = self._order_event_locks.get(identity)
        if lock is None:
            lock = self._order_event_locks[identity] = asyncio.Lock()
        return lock

    async def _apply_recorded_trade_update(self, recorder, update):
        order = self.all_fillable_orders.get(update.client_order_id)
        if order is None:
            return False
        # Stage a copy without changing execution totals or notifying live
        # waiters until the matching fill and tracker checkpoint commit.
        staged = copy(order)
        staged.order_fills = dict(order.order_fills)
        staged.completely_filled_event = asyncio.Event()
        if not staged.update_with_trade_update(update):
            return False
        if staged.executed_amount_base <= order.executed_amount_base:
            order.update_with_trade_update(update)
            return False
        evt = self._filled_event(order, update.fill_base_amount, update.fill_price, update.fee,
                                 update.trade_id, update.exchange_order_id, update.fill_timestamp)
        tracking = dict(self._connector.tracking_states)
        if order.client_order_id in tracking:
            tracking[order.client_order_id] = staged.to_json()
        committed, cancelled = await recorder.persist_fill(self._connector, evt, tracking)
        order.update_with_trade_update(update)
        self.logger().info(
            f"The {order.trade_type.name.upper()} order {order.client_order_id} "
            f"amounting to {order.executed_amount_base}/{order.amount} {order.base_asset} "
            f"has been filled at {update.fill_price} {order.quote_asset}."
        )
        recorder.finish_recorded_fill(self._connector, evt, committed)
        recorder.publish_recorded_order_event(self._connector, MarketEvent.OrderFilled, evt)
        if cancelled is not None:
            raise cancelled
        return True

    async def persist_proved_trade(self, recorder, order, update):
        """Record complete venue evidence while the caller holds the recorder lock."""
        if await self._apply_recorded_trade_update(recorder, update):
            return
        # A retired order may lack a live tracker entry, or its existing local
        # fill may still need a durable receipt. The recorder verifies deduplication.
        evt = self._filled_event(order, update.fill_base_amount, update.fill_price, update.fee,
                                 update.trade_id, update.exchange_order_id, update.fill_timestamp)
        evt = evt._replace(timestamp=update.fill_timestamp)
        committed, cancelled = await recorder.persist_fill(self._connector, evt, dict(self._connector.tracking_states))
        recorder.finish_recorded_fill(self._connector, evt, committed)
        if cancelled is not None:
            raise cancelled

    async def process_order_not_found(self, client_order_id: str):
        """
        Increments and checks if the order specified has exceeded the order_not_found_count_limit.
        A failed event is triggered if necessary.

        :param client_order_id: Client order id of an order.
        :type client_order_id: str
        """
        from hummingbot.core.order_submission_guard import unresolved_submission
        if unresolved_submission(client_order_id):
            return
        # Only concerned with active orders.
        tracked_order: Optional[InFlightOrder] = self.fetch_tracked_order(client_order_id=client_order_id)

        if tracked_order is not None:
            self._order_not_found_records[client_order_id] += 1
            if self._order_not_found_records[client_order_id] > self._lost_order_count_limit:
                # Only mark the order as failed if it has not been marked as done already asynchronously
                if tracked_order.current_state not in [OrderState.CANCELED, OrderState.FILLED, OrderState.FAILED]:
                    self.logger().warning(
                        f"The order {client_order_id}({tracked_order.exchange_order_id}) will be "
                        f"considered lost. Please check its status in the exchange."
                    )
                    order_update: OrderUpdate = OrderUpdate(
                        client_order_id=client_order_id,
                        trading_pair=tracked_order.trading_pair,
                        update_timestamp=self.current_timestamp,
                        new_state=OrderState.FAILED,
                    )
                    await self._process_order_update(order_update)
                    del self._cached_orders[client_order_id]
                    self._lost_orders[tracked_order.client_order_id] = tracked_order
        else:
            lost_order = self._lost_orders.get(client_order_id)
            if lost_order is not None:
                self.logger().info(
                    f"The lost order {client_order_id}({lost_order.exchange_order_id}) was not found "
                    f"and will be removed"
                )
                order_update: OrderUpdate = OrderUpdate(
                    client_order_id=client_order_id,
                    trading_pair=lost_order.trading_pair,
                    update_timestamp=self.current_timestamp,
                    new_state=OrderState.FAILED,
                )
                await self._process_order_update(order_update)
            else:
                self.logger().debug(f"Order is not/no longer being tracked ({client_order_id})")

    async def _process_order_update(self, order_update: OrderUpdate):
        if not order_update.client_order_id and not order_update.exchange_order_id:
            self.logger().error("OrderUpdate does not contain any client_order_id or exchange_order_id", exc_info=True)
            return

        tracked_order: Optional[InFlightOrder] = self.fetch_order(
            order_update.client_order_id, order_update.exchange_order_id
        )

        if tracked_order:
            from hummingbot.connector.markets_recorder import MarketsRecorder
            recorder = MarketsRecorder.service_recorder_for(self._connector)
            if (recorder is None or not recorder.order_writes.locked()) and self._late_order_update(tracked_order, order_update):
                return
            proved_fills = None
            from hummingbot.core.order_submission_guard import (
                SubmissionBlocked,
                managed_terminal_order,
                terminal_proof_conflict,
                verify_terminal_update,
            )
            if (order_update.new_state in (OrderState.CANCELED, OrderState.FILLED)
                    and not tracked_order.is_done and managed_terminal_order(tracked_order)):
                if getattr(tracked_order, '_terminal_proof_active', False):
                    return  # The in-flight read revalidates the latest complete outcome.
                tracked_order._terminal_proof_active = True
                try:
                    order_update = await verify_terminal_update(self._connector, tracked_order, order_update)
                except (IOError, asyncio.TimeoutError):
                    if not getattr(tracked_order, '_terminal_proof_pending', False):
                        self.logger().warning(f"Terminal order evidence pending: order_id={tracked_order.client_order_id}")
                    tracked_order._terminal_proof_pending = True
                    return
                except SubmissionBlocked as error:
                    terminal_proof_conflict(str(error))
                    return
                finally:
                    tracked_order._terminal_proof_active = False
                if getattr(tracked_order, '_terminal_proof_pending', False):
                    self.logger().info(f"Terminal order evidence verified: order_id={tracked_order.client_order_id}")
                    tracked_order._terminal_proof_pending = False
                proved_fills = tuple(tracked_order.order_fills)
            if order_update.new_state == OrderState.FILLED and not tracked_order.is_done:
                try:
                    await asyncio.wait_for(
                        tracked_order.wait_until_completely_filled(), timeout=self.TRADE_FILLS_WAIT_TIMEOUT
                    )
                except asyncio.TimeoutError:
                    self.logger().warning(
                        f"The order fill updates did not arrive on time for {tracked_order.client_order_id}. "
                        f"The complete update will be processed with incomplete information."
                    )

            if recorder is None:
                self._apply_order_update(tracked_order, order_update)
            else:
                await self._apply_native_order_update(recorder, tracked_order, order_update, proved_fills)
        else:
            lost_order = self.fetch_lost_order(
                client_order_id=order_update.client_order_id, exchange_order_id=order_update.exchange_order_id
            )
            if lost_order:
                if order_update.new_state in [OrderState.CANCELED, OrderState.FILLED, OrderState.FAILED]:
                    # If the order officially reaches a final state after being lost it should be removed from the lost list
                    del self._lost_orders[lost_order.client_order_id]
            else:
                self.logger().debug(f"Order is not/no longer being tracked ({order_update})")

    async def _apply_native_order_update(self, recorder, order, update, proved_fills):
        with recorder.native_order_activity() as owner:
            async with self._order_event_lock(order.client_order_id):
                retry_open = update.new_state is OrderState.OPEN and order.is_pending_create

                def can_apply():
                    current = self.fetch_order(update.client_order_id, update.exchange_order_id)
                    if (current is not order or self._late_order_update(order, update)
                            or (retry_open and not order.is_pending_create)):
                        return False
                    if proved_fills is not None and tuple(order.order_fills) != proved_fills:
                        # Let normal polling obtain a proof with the new fills.
                        order._terminal_proof_pending = True
                        return False
                    return True

                async def apply():
                    if can_apply():
                        await self._persist_order_update(recorder, order, update, defer_ack_contention=retry_open)

                cancel_ack = (update.new_state is OrderState.CANCELED and order.is_open and not order.is_pending_create
                              and update.exchange_order_id == order.exchange_order_id)
                if (update.exchange_order_id and recorder.cleanup_operation_id is None
                        and (cancel_ack or retry_open and order.client_order_id in owner.sent_intents)):
                    def prepare():
                        if not can_apply():
                            return None
                        staged = self._stage_tracker_update(order, update)
                        if staged is None:
                            return None
                        tag, event = self._cancellation_event(staged) if cancel_ack else self._creation_event(staged)

                        def publish():
                            if not cancel_ack:
                                self._finish_tracker_creation(recorder, order, update.new_state, event)
                            recorder.publish_recorded_order_event(self._connector, tag, event)
                            if cancel_ack:
                                self._finish_tracker_terminal(order, update, tag)

                        return _StagedOrderAcknowledgement(
                            tag, event, staged.to_json(), lambda: order.update_with_order_update(update), publish)

                    await recorder.record_order_acknowledgement(self._connector, owner, prepare, cancellation=cancel_ack)
                elif retry_open:
                    await recorder.retry_order_acknowledgement(self._connector, owner, apply)
                else:
                    async with recorder.native_order_lock(self._connector, owner):
                        await apply()

    @staticmethod
    def _late_order_update(order, update):
        # A local cancel ACK can carry the initial exchange ID, but a late
        # REST or cancel ACK cannot reopen a proved terminal identity.
        if update.new_state == OrderState.PENDING_CANCEL and order.is_done:
            if order.exchange_order_id is None and update.exchange_order_id is not None:
                order.update_exchange_order_id(update.exchange_order_id)
            return True
        return (getattr(order, '_managed_terminal_proved', False) is True
                and order.current_state in (OrderState.CANCELED, OrderState.FILLED)
                and update.new_state not in (OrderState.CANCELED, OrderState.FILLED, OrderState.FAILED))

    def _apply_order_update(self, order, update):
        previous_state = order.current_state
        if order.update_with_order_update(update):
            self._trigger_order_creation(order, previous_state, update.new_state)
            self._trigger_order_completion(order, update)

    @staticmethod
    def _stage_tracker_update(order, update):
        staged = copy(order)
        # The copy must not wake the live waiters before PostgreSQL commits.
        staged.exchange_order_id_update_event = asyncio.Event()
        staged.processed_by_exchange_event = asyncio.Event()
        return staged if staged.update_with_order_update(update) else None

    def _finish_tracker_creation(self, recorder, order, state, event):
        recorder.finish_recorded_order_creation(self._connector, event)
        self._note_creation(order, state)

    def _finish_tracker_terminal(self, order, update, tag):
        self._note_completion(order, update, tag)
        if (tag in (MarketEvent.BuyOrderCompleted, MarketEvent.OrderCancelled)
                and self._connector.name == 'okx' and order.trade_type is TradeType.BUY):
            from hummingbot.core.order_submission_guard import spot_outflow_terminal_committed
            spot_outflow_terminal_committed(self._connector, order)

    async def _persist_order_update(self, recorder, order, update, *, defer_ack_contention=False):
        staged = self._stage_tracker_update(order, update)
        if staged is None:
            return

        created = self._should_emit_creation(order.current_state, update.new_state)
        events = [self._creation_event(staged)] if created else []
        terminal = self._terminal_event(staged, update) if not staged.is_open else None
        if terminal is not None:
            events.append(terminal)
        cancelled = None
        if events:
            tracking = dict(self._connector.tracking_states)
            if order.client_order_id in tracking:
                tracking[order.client_order_id] = staged.to_json()
            cancelled = await recorder.persist_order_update(self._connector, events, tracking,
                                                            defer_ack_contention=defer_ack_contention)

        order.update_with_order_update(update)
        if created:
            self._finish_tracker_creation(recorder, order, update.new_state, events[0][1])
        for tag, payload in events:
            recorder.publish_recorded_order_event(self._connector, tag, payload)
        if not staged.is_open:
            self._finish_tracker_terminal(order, update, terminal[0] if terminal else None)
        if cancelled is not None:
            raise cancelled

    def _trigger_created_event(self, order: InFlightOrder):
        self._connector.trigger_event(*self._creation_event(order))

    def _creation_event(self, order):
        event_tag = MarketEvent.BuyOrderCreated if order.trade_type is TradeType.BUY else MarketEvent.SellOrderCreated
        event_class: Callable = BuyOrderCreatedEvent if order.trade_type is TradeType.BUY else SellOrderCreatedEvent
        return (
            event_tag,
            event_class(
                self.current_timestamp,
                order.order_type,
                order.trading_pair,
                order.amount,
                order.price,
                order.client_order_id,
                order.creation_timestamp,
                exchange_order_id=order.exchange_order_id,
                leverage=order.leverage,
                position=order.position.value,
            ),
        )

    @trace_runtime('order.terminal', order_parameter='order')
    def _trigger_cancelled_event(self, order: InFlightOrder):
        self._connector.trigger_event(*self._cancellation_event(order))

    def _cancellation_event(self, order):
        return MarketEvent.OrderCancelled, OrderCancelledEvent(
            timestamp=self.current_timestamp,
            order_id=order.client_order_id,
            exchange_order_id=order.exchange_order_id,
        )

    def _trigger_filled_event(
        self,
        order: InFlightOrder,
        fill_amount: Decimal,
        fill_price: Decimal,
        fill_fee: TradeFeeBase,
        trade_id: str,
        exchange_order_id: str,
        fill_timestamp: Optional[float] = None,
    ):
        self._connector.trigger_event(MarketEvent.OrderFilled, self._filled_event(
            order, fill_amount, fill_price, fill_fee, trade_id, exchange_order_id, fill_timestamp))

    def _filled_event(self, order, fill_amount, fill_price, fill_fee, trade_id, exchange_order_id, fill_timestamp):
        return OrderFilledEvent(
            timestamp=self.current_timestamp if math.isfinite(self.current_timestamp) else fill_timestamp,
            order_id=order.client_order_id,
            trading_pair=order.trading_pair,
            trade_type=order.trade_type,
            order_type=order.order_type,
            price=fill_price,
            amount=fill_amount,
            trade_fee=fill_fee,
            exchange_trade_id=trade_id,
            leverage=int(order.leverage),
            position=order.position.value,
            exchange_order_id=exchange_order_id,
        )

    @trace_runtime('order.terminal', order_parameter='order')
    def _trigger_completed_event(self, order: InFlightOrder):
        self._connector.trigger_event(*self._completion_event(order))

    def _completion_event(self, order):
        event_tag = (
            MarketEvent.BuyOrderCompleted if order.trade_type is TradeType.BUY else MarketEvent.SellOrderCompleted
        )
        event_class = BuyOrderCompletedEvent if order.trade_type is TradeType.BUY else SellOrderCompletedEvent
        return (
            event_tag,
            event_class(
                self.current_timestamp,
                order.client_order_id,
                order.base_asset,
                order.quote_asset,
                order.executed_amount_base,
                order.executed_amount_quote,
                order.order_type,
                order.exchange_order_id,
            ),
        )

    @trace_runtime('order.terminal', order_parameter='order')
    def _trigger_failure_event(self, order: InFlightOrder, order_update: OrderUpdate):
        self._connector.trigger_event(*self._failure_event(order, order_update))

    def _failure_event(self, order, order_update):
        misc_updates = order_update.misc_updates or {}
        return (
            MarketEvent.OrderFailure,
            MarketOrderFailureEvent(
                timestamp=self.current_timestamp,
                order_id=order.client_order_id,
                order_type=order.order_type,
                error_type=misc_updates.get("error_type"),
                error_message=misc_updates.get("error_message")
            ),
        )

    @staticmethod
    def _should_emit_creation(previous_state, new_state):
        return previous_state is OrderState.PENDING_CREATE and new_state not in (
            OrderState.PENDING_CREATE, OrderState.CANCELED, OrderState.FAILED, OrderState.PENDING_CANCEL,
        )

    def _note_creation(self, order, new_state):
        if new_state in (OrderState.OPEN, OrderState.PARTIALLY_FILLED):
            observe_spot_quote_open(self._connector, order)
        self.logger().info(order.build_order_created_message())

    def _trigger_order_creation(self, tracked_order: InFlightOrder, previous_state: OrderState, new_state: OrderState):
        if self._should_emit_creation(previous_state, new_state):
            self._note_creation(tracked_order, new_state)
            self._trigger_created_event(tracked_order)

    def _trigger_order_fills(self,
                             tracked_order: InFlightOrder,
                             prev_executed_amount_base: Decimal,
                             fill_amount: Decimal,
                             fill_price: Decimal,
                             fill_fee: TradeFeeBase,
                             trade_id: str,
                             exchange_order_id: str, fill_timestamp: Optional[float] = None):
        if prev_executed_amount_base < tracked_order.executed_amount_base:
            self.logger().info(
                f"The {tracked_order.trade_type.name.upper()} order {tracked_order.client_order_id} "
                f"amounting to {tracked_order.executed_amount_base}/{tracked_order.amount} {tracked_order.base_asset} "
                f"has been filled at {fill_price} {tracked_order.quote_asset}."
            )
            self._trigger_filled_event(
                order=tracked_order,
                fill_amount=fill_amount,
                fill_price=fill_price,
                fill_fee=fill_fee,
                trade_id=trade_id,
                exchange_order_id=exchange_order_id,
                fill_timestamp=fill_timestamp,
            )

    def _terminal_event(self, order, update):
        if order.is_cancelled:
            return self._cancellation_event(order)
        if order.is_filled:
            return self._completion_event(order)
        if order.is_failure:
            return self._failure_event(order, update)

    def _trigger_order_completion(self, tracked_order: InFlightOrder, order_update: Optional[OrderUpdate] = None):
        if tracked_order.is_open:
            return

        tag = None
        if tracked_order.is_cancelled:
            tag = MarketEvent.OrderCancelled
            self._trigger_cancelled_event(tracked_order)
        elif tracked_order.is_filled:
            tag = MarketEvent.BuyOrderCompleted if tracked_order.trade_type is TradeType.BUY else MarketEvent.SellOrderCompleted
            self._trigger_completed_event(tracked_order)
        elif tracked_order.is_failure:
            tag = MarketEvent.OrderFailure
            self._trigger_failure_event(tracked_order, order_update)
        self._note_completion(tracked_order, order_update, tag)

    def _note_completion(self, order, update, tag):
        if tag is MarketEvent.OrderCancelled:
            self.logger().info(f"Successfully canceled order {order.client_order_id}.")
        elif tag in (MarketEvent.BuyOrderCompleted, MarketEvent.SellOrderCompleted):
            self.logger().info(f"{order.trade_type.name.upper()} order {order.client_order_id} completely filled.")
        elif tag is MarketEvent.OrderFailure:
            self.logger().info(f"Order {order.client_order_id} has failed. Order Update: {update}")
        self.stop_tracking_order(order.client_order_id)

    @staticmethod
    def _restore_order_from_json(serialized_order: Dict):
        order = InFlightOrder.from_json(serialized_order)
        return order
