import asyncio
import json
import logging
import os.path
import threading
import time
from contextlib import asynccontextmanager, contextmanager
from contextvars import copy_context
from dataclasses import dataclass
from decimal import Decimal
from itertools import islice
from shutil import move
from typing import Callable, Dict, Iterator, List, Optional, Tuple, Union

import pandas as pd
from sqlalchemy import and_, bindparam, case, column, func, or_, select, text, true, tuple_, union_all, values
from sqlalchemy.engine import Row
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Query, Session, defer, load_only

from hummingbot import data_path
from hummingbot.client.config.client_config_map import MarketDataCollectionConfigMap
from hummingbot.connector.connector_base import ConnectorBase
from hummingbot.connector.utils import TradeFillOrderDetails
from hummingbot.core.data_type.common import PriceType
from hummingbot.core.data_type.trade_fee import TradeFeeBase
from hummingbot.core.engine_diagnostics import submission_timing, timed_submission_lock, trace_runtime
from hummingbot.core.event.event_forwarder import SourceInfoEventForwarder
from hummingbot.core.event.events import (
    BuyOrderCompletedEvent,
    BuyOrderCreatedEvent,
    FundingPaymentCompletedEvent,
    MarketEvent,
    MarketOrderFailureEvent,
    OrderCancelledEvent,
    OrderExpiredEvent,
    OrderFilledEvent,
    PositionAction,
    RangePositionLiquidityAddedEvent,
    RangePositionLiquidityRemovedEvent,
    SellOrderCompletedEvent,
    SellOrderCreatedEvent,
)
from hummingbot.core.operational_warnings import OrderAcknowledgementBusy
from hummingbot.core.submission_admission import QueuedWrite, SubmissionAdmission
from hummingbot.logger import HummingbotLogger
from hummingbot.model.controllers import Controllers
from hummingbot.model.executors import Executors
from hummingbot.model.funding_payment import FundingPayment
from hummingbot.model.market_data import MarketData
from hummingbot.model.market_state import MarketState
from hummingbot.model.order import Order
from hummingbot.model.order_status import OrderStatus
from hummingbot.model.position import Position
from hummingbot.model.range_position_update import RangePositionUpdate
from hummingbot.model.sql_connection_manager import SQLConnectionManager
from hummingbot.model.trade_fill import TradeFill
from hummingbot.strategy_v2.controllers.controller_base import ControllerConfigBase
from hummingbot.strategy_v2.models.executors import CloseType
from hummingbot.strategy_v2.models.executors_info import ExecutorInfo

_QUOTE_FEE_NOT_CALCULATED = object()
_ORDER_FENCE_RETRY_SECONDS = 4.0
_ORDER_FENCE_RETRY_INTERVAL = 0.1
_ORDER_FENCE_FIRST_WRITE_RETRY = 0.0125
_ACK_RETRY_EXPIRED = object()
MAX_OPEN_ACK_BATCH = 8


@dataclass
class _QueuedAcknowledgement(QueuedWrite):
    market: ConnectorBase
    owner: object
    prepare: Callable
    deadline: Optional[float]


class MarketsRecorder:
    _logger = None
    _shared_instance: "MarketsRecorder" = None
    market_event_tag_map: Dict[int, MarketEvent] = {
        event_obj.value: event_obj
        for event_obj in MarketEvent.__members__.values()
    }

    @classmethod
    def logger(cls) -> HummingbotLogger:
        if cls._logger is None:
            cls._logger = logging.getLogger(__name__)
        return cls._logger

    @classmethod
    def get_instance(cls, *args, **kwargs) -> "MarketsRecorder":
        if cls._shared_instance is None:
            cls._shared_instance = MarketsRecorder(*args, **kwargs)
        return cls._shared_instance

    def __init__(self,
                 sql: SQLConnectionManager,
                 markets: List[ConnectorBase],
                 config_file_path: str,
                 strategy_name: str,
                 market_data_collection: MarketDataCollectionConfigMap):
        if threading.current_thread() != threading.main_thread():
            raise EnvironmentError("MarketsRecorded can only be initialized from the main thread.")

        self._ev_loop: asyncio.AbstractEventLoop = asyncio.get_event_loop()
        self._sql_manager: SQLConnectionManager = sql
        self._markets: List[ConnectorBase] = markets
        self._config_file_path: str = config_file_path
        self._strategy_name: str = strategy_name
        self.cleanup_operation_id = None
        self.order_writes = SubmissionAdmission(track_writes=True)
        self._published_order_event = None
        self._market_data_collection_config: MarketDataCollectionConfigMap = market_data_collection
        self._market_data_collection_task: Optional[asyncio.Task] = None
        for market in self._markets:
            self._restore_reconstruction_history(market)

        self._create_order_forwarder: SourceInfoEventForwarder = SourceInfoEventForwarder(self._did_create_order)
        self._fill_order_forwarder: SourceInfoEventForwarder = SourceInfoEventForwarder(self._did_fill_order)
        self._cancel_order_forwarder: SourceInfoEventForwarder = SourceInfoEventForwarder(self._did_cancel_order)
        self._fail_order_forwarder: SourceInfoEventForwarder = SourceInfoEventForwarder(self._did_fail_order)
        self._complete_order_forwarder: SourceInfoEventForwarder = SourceInfoEventForwarder(self._did_complete_order)
        self._expire_order_forwarder: SourceInfoEventForwarder = SourceInfoEventForwarder(self._did_expire_order)
        self._funding_payment_forwarder: SourceInfoEventForwarder = SourceInfoEventForwarder(self._did_complete_funding_payment)
        self._update_range_position_forwarder: SourceInfoEventForwarder = SourceInfoEventForwarder(self._did_update_range_position)

        self._event_pairs: List[Tuple[MarketEvent, SourceInfoEventForwarder]] = [
            (MarketEvent.BuyOrderCreated, self._create_order_forwarder),
            (MarketEvent.SellOrderCreated, self._create_order_forwarder),
            (MarketEvent.OrderFilled, self._fill_order_forwarder),
            (MarketEvent.OrderCancelled, self._cancel_order_forwarder),
            (MarketEvent.OrderFailure, self._fail_order_forwarder),
            (MarketEvent.BuyOrderCompleted, self._complete_order_forwarder),
            (MarketEvent.SellOrderCompleted, self._complete_order_forwarder),
            (MarketEvent.OrderExpired, self._expire_order_forwarder),
            (MarketEvent.FundingPaymentCompleted, self._funding_payment_forwarder),
            (MarketEvent.RangePositionLiquidityAdded, self._update_range_position_forwarder),
            (MarketEvent.RangePositionLiquidityRemoved, self._update_range_position_forwarder),
        ]
        MarketsRecorder._shared_instance = self

    @classmethod
    def service_recorder_for(cls, market):
        from hummingbot.client.config.runtime_config import postgres_requested

        recorder = cls._shared_instance
        if (recorder is not None and market in recorder._markets and market.name in ('okx', 'okx_perpetual')
                and recorder.durable_position_checkpoints and postgres_requested()):
            return recorder
        return None

    @contextmanager
    def native_order_activity(self):
        from hummingbot.core.order_submission_guard import active_guard

        owner = active_guard()
        task = asyncio.current_task()
        already_tracked = task in owner.terminal_tasks
        owner.terminal_tasks.add(task)
        try:
            yield owner
        finally:
            if not already_tracked:
                owner.terminal_tasks.discard(task)

    @asynccontextmanager
    async def native_order_lock(self, market, owner, *, operation='tracker_update'):
        from hummingbot.core.order_submission_guard import SubmissionBlocked, active_guard

        async with timed_submission_lock(self.order_writes.write(operation), 'tracker_recorder_wait_ms'):
            if (active_guard() is not owner or owner.engine is not self._sql_manager.engine
                    or self._shared_instance is not self or market not in self._markets):
                raise SubmissionBlocked('The queued recorder event no longer belongs to the active owner.')
            yield

    @asynccontextmanager
    async def native_order_write(self, market):
        with self.native_order_activity() as owner:
            async with self.native_order_lock(market, owner):
                yield

    @staticmethod
    async def _drain_order_write(write):
        job = asyncio.get_running_loop().run_in_executor(None, copy_context().run, write)
        return await MarketsRecorder._drain_order_job(job)

    @staticmethod
    async def _drain_order_job(job):
        cancelled = None
        while not job.done():
            try:
                await asyncio.shield(job)
            except asyncio.CancelledError as error:
                cancelled = error
        return job.result(), cancelled

    async def retry_order_acknowledgement(self, market, owner, apply):
        """Run a native OPEN update, rebuilding its snapshot on each proven retry."""
        async def attempt(deadline):
            async with self.native_order_lock(market, owner, operation='open_ack'):
                if deadline is not None and asyncio.get_running_loop().time() >= deadline:
                    return _ACK_RETRY_EXPIRED
                return await apply()

        await self._retry_acknowledgement(attempt)

    async def record_order_acknowledgement(self, market, owner, prepare):
        async def attempt(deadline):
            return await self._acknowledgement_turn(_QueuedAcknowledgement(market, owner, prepare, deadline))

        # The original tracker task keeps its per-order lock and shutdown
        # registration until the received ACK is committed and published.
        _, cancelled = await self._drain_order_job(asyncio.create_task(self._retry_acknowledgement(attempt)))
        if cancelled is not None:
            raise cancelled

    async def _retry_acknowledgement(self, attempt):
        loop = asyncio.get_running_loop()
        try:
            return await attempt(None)
        except OrderAcknowledgementBusy as error:
            failure, cancelled = error.error, error.cancellation
        deadline = loop.time() + _ORDER_FENCE_RETRY_SECONDS

        async def finish():
            current_failure = failure
            while loop.time() < deadline:
                await self._sleep(min(deadline - loop.time(), _ORDER_FENCE_RETRY_INTERVAL))
                if loop.time() >= deadline:
                    break
                try:
                    result = await attempt(deadline)
                    if result is _ACK_RETRY_EXPIRED:
                        break
                    return result
                except OrderAcknowledgementBusy as error:
                    current_failure = error.error
            self._sql_manager.persistence_healthy = False
            raise current_failure

        # The caller retains its per-order slot and its shutdown registration.
        # Drain the received ACK even if cancellation arrives during backoff.
        result, interrupted = await self._drain_order_job(loop.create_task(finish()))
        if interrupted is not None or cancelled is not None:
            raise interrupted or cancelled
        return result

    async def _acknowledgement_turn(self, work):
        from hummingbot.core.order_submission_guard import SubmissionBlocked, active_guard

        slot = self.order_writes
        with submission_timing('tracker_recorder_wait_ms') as acquired:
            work.on_claim = acquired
            # The venue has accepted this order. Use the same bounded
            # priority as transport receipts to publish that durable fact.
            result = await slot.acquire(final=True, work=work, operation='open_ack')
        if result is not True:
            if isinstance(result, BaseException):
                raise result
            return result

        peers = []
        results = [None]
        try:
            if (active_guard() is not work.owner or work.owner.engine is not self._sql_manager.engine
                    or self.service_recorder_for(work.market) is not self):
                raise SubmissionBlocked('The queued recorder event no longer belongs to the active owner.')
            # The HTTP writer updates the receipt field independently. Keep
            # its next turn, but let this batch include ready private OPEN
            # events on either side of that single intervening waiter.
            peers = slot.take_work(MAX_OPEN_ACK_BATCH - 1, lambda peer: (
                isinstance(peer, _QueuedAcknowledgement) and peer.market is work.market and peer.owner is work.owner),
                final=True, across_receipt=True)
            results.extend([None] * len(peers))
            prepared = []
            now = asyncio.get_running_loop().time()
            for index, item in enumerate([work, *(peer for peer, _ in peers)]):
                if item.deadline is not None and now >= item.deadline:
                    results[index] = _ACK_RETRY_EXPIRED
                    continue
                try:
                    plan = item.context.run(item.prepare)
                except Exception as error:
                    results[index] = error
                else:
                    if plan is not None:
                        prepared.append((index, item.context, plan))
            if prepared:
                cancelled = await self._persist_open_acknowledgements(work.market, prepared)
                for index, context, plan in prepared:
                    try:
                        context.run(plan.apply)
                    except Exception as error:
                        results[index] = error
                for index, context, plan in prepared:
                    if results[index] is not None:
                        continue
                    try:
                        context.run(plan.publish)
                    except Exception as error:
                        results[index] = error
                if cancelled is not None and results[0] is None:
                    results[0] = cancelled
        except BaseException as error:
            results = [error if result is None else result for result in results]
        finally:
            for (_, future), result in zip(peers, results[1:]):
                future.set_result(result)
            slot.release()
        if isinstance(results[0], BaseException):
            raise results[0]
        return results[0]

    async def _persist_open_acknowledgements(self, market, prepared):
        tracking = dict(market.tracking_states)
        fresh_order = None
        for _, _, plan in prepared:
            if plan.event.order_id in tracking:
                fresh_order = plan.event.order_id
                tracking[fresh_order] = plan.tracking_state

        def stage(session):
            with session.no_autoflush:
                evidence, tracking_basis = self._read_creation_evidence_batch(session, [
                    (plan.event.order_id, int(plan.event.creation_timestamp * 1e3), plan.tag.name)
                    for _, _, plan in prepared
                ], tracking=tracking if fresh_order is not None else None, market_name=market.display_name)
                orders = [context.run(self._stage_order_creation, session, plan.tag.value, market, plan.event,
                                      coalesce_snapshot=True, defer_snapshot=True,
                                      evidence=evidence.get(plan.event.order_id))
                          for _, context, plan in prepared]
                self.save_market_states(self._config_file_path, market, session, new_order_id=fresh_order,
                                        tracking_states=tracking, staged_orders=orders, tracking_basis=tracking_basis)

        _, cancelled = await self._run_native_write(stage, defer_contention=OrderAcknowledgementBusy)
        return cancelled

    async def _run_native_write(self, stage, *args, defer_contention=None):
        from hummingbot.core.bot_ownership import OwnershipLost
        from hummingbot.core.operational_warnings import ExecutorArchiveBusy, PositionCheckpointBusy
        from hummingbot.core.order_submission_guard import SubmissionBlocked, active_guard
        from hummingbot.model.intent_admission import confirmed_prewrite_failure

        owner = active_guard()
        if owner.engine is not self._sql_manager.engine:
            raise RuntimeError('Order recorder is outside the active ownership scope.')

        def write():
            with self._sql_manager.get_new_session() as session:
                try:
                    with session.begin():
                        self._sql_manager.assert_persistence_healthy()
                        session.info["worker_order_owner"] = owner
                        result = stage(session, *args)
                        session.flush()
                        if session.info.get("ownership_fenced_transaction") is session.get_transaction():
                            self._sql_manager.assert_persistence_healthy()
                            owner.fence_recorder_session(session)
                    return result, None
                except OperationalError as error:
                    if confirmed_prewrite_failure(session, error) and self._sql_manager.persistence_healthy:
                        return None, error
                    raise

        loop = asyncio.get_running_loop()
        started = loop.time()
        deadline = started + _ORDER_FENCE_RETRY_SECONDS
        retry_interval = _ORDER_FENCE_FIRST_WRITE_RETRY
        cancelled = None
        try:
            while True:
                if active_guard() is not owner:
                    raise SubmissionBlocked('Trading owner changed while waiting for the recorder lock.')
                owner.require_owner_connection()
                (result, lock_error), write_cancelled = await self._drain_order_write(write)
                cancelled = write_cancelled or cancelled
                if lock_error is None:
                    elapsed = loop.time() - started
                    if elapsed >= 1.0:
                        self.logger().info('Order recorder transaction completed after %.3fs', elapsed,
                                           extra={'event': 'order_recorder_write_completed'})
                    return result, cancelled
                if defer_contention is not None:
                    if defer_contention is OrderAcknowledgementBusy:
                        raise OrderAcknowledgementBusy(lock_error, cancelled) from None
                    raise defer_contention from None
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise lock_error
                try:
                    # Keep received fills ahead of new risk. A short initial
                    # retry avoids parking the shared slot after a brief row lock.
                    await asyncio.sleep(min(remaining, retry_interval))
                except asyncio.CancelledError as error:
                    # Drain the received venue fact under the same finite
                    # budget before shutdown can release its ownership.
                    cancelled = error
                retry_interval = min(_ORDER_FENCE_RETRY_INTERVAL, retry_interval * 2)
                if loop.time() >= deadline:
                    raise lock_error
        except (ExecutorArchiveBusy, PositionCheckpointBusy, OrderAcknowledgementBusy):
            if defer_contention is None:
                self._sql_manager.persistence_healthy = False
            raise
        except OwnershipLost as error:
            self._sql_manager.persistence_healthy = False
            raise owner._lose_ownership(str(error), error.reason_code) from None
        except BaseException:
            self._sql_manager.persistence_healthy = False
            raise

    def publish_recorded_order_event(self, market, tag, payload):
        previous = self._published_order_event
        self._published_order_event = payload
        try:
            market.trigger_event(tag, payload)
        finally:
            self._published_order_event = previous

    def _start_market_data_recording(self):
        self._market_data_collection_task = self._ev_loop.create_task(self._record_market_data())

    async def _record_market_data(self):
        while True:
            try:
                if all(ex.ready for ex in self._markets):
                    with self._sql_manager.get_new_session() as session:
                        with session.begin():
                            for market in self._markets:
                                exchange = market.display_name
                                for trading_pair in market.trading_pairs:
                                    mid_price = market.get_price_by_type(trading_pair, PriceType.MidPrice)
                                    best_bid = market.get_price_by_type(trading_pair, PriceType.BestBid)
                                    best_ask = market.get_price_by_type(trading_pair, PriceType.BestAsk)
                                    order_book = market.get_order_book(trading_pair)
                                    depth = self._market_data_collection_config.market_data_collection_depth + 1
                                    market_data = MarketData(
                                        timestamp=self.db_timestamp,
                                        exchange=exchange,
                                        trading_pair=trading_pair,
                                        mid_price=mid_price,
                                        best_bid=best_bid,
                                        best_ask=best_ask,
                                        order_book={
                                            "bid": list(islice(order_book.bid_entries(), depth)),
                                            "ask": list(islice(order_book.ask_entries(), depth))}
                                    )
                                    session.add(market_data)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.logger().error("Unexpected error while recording market data.", e)
            finally:
                await self._sleep(self._market_data_collection_config.market_data_collection_interval)

    @property
    def sql_manager(self) -> SQLConnectionManager:
        return self._sql_manager

    @property
    def config_file_path(self) -> str:
        return self._config_file_path

    @property
    def strategy_name(self) -> str:
        return self._strategy_name

    @property
    def db_timestamp(self) -> int:
        return int(time.time() * 1e3)

    def start(self):
        for market in self._markets:
            for event_pair in self._event_pairs:
                market.add_listener(event_pair[0], event_pair[1])
        if self._market_data_collection_config.market_data_collection_enabled:
            self._start_market_data_recording()

    def _uses_reconstruction_history(self, market):
        # OKX trackers use their in-flight trade identities. PostgreSQL owns
        # durable replay/deduplication; these legacy reconstruction sets are
        # unused on this path and otherwise grow once per order/fill forever.
        return not (self._sql_manager.engine.dialect.name == "postgresql"
                    and market.name in ("okx", "okx_perpetual"))

    def _restore_reconstruction_history(self, market):
        if not self._uses_reconstruction_history(market):
            return
        trade_fills = self.get_trades_for_config(self._config_file_path, 2000, market=market.display_name)
        market.add_trade_fills_from_market_recorder({TradeFillOrderDetails(tf.market, tf.exchange_trade_id, tf.symbol)
                                                    for tf in trade_fills})
        exchange_order_ids = self.get_orders_for_config_and_market(self._config_file_path, market, True, 2000)
        market.add_exchange_order_ids_from_market_recorder({o.exchange_order_id: o.id for o in exchange_order_ids})

    def add_market(self, market: ConnectorBase):
        """Add a new market/connector dynamically."""
        if market not in self._markets:
            self._markets.append(market)

            self._restore_reconstruction_history(market)

            # Add event listeners
            for event_pair in self._event_pairs:
                market.add_listener(event_pair[0], event_pair[1])

    def remove_market(self, market: ConnectorBase):
        """Remove a market/connector dynamically."""
        if market in self._markets:
            # Remove event listeners
            for event_pair in self._event_pairs:
                market.remove_listener(event_pair[0], event_pair[1])

            # Remove from markets list
            self._markets.remove(market)

    def stop(self):
        for market in self._markets:
            for event_pair in self._event_pairs:
                market.remove_listener(event_pair[0], event_pair[1])
        if self._market_data_collection_task is not None:
            self._market_data_collection_task.cancel()

    @trace_runtime('executor.archive')
    def store_or_update_executor(self, executor):
        self.store_or_update_executors([executor], defer_ownership_contention=self.durable_position_checkpoints)

    def store_or_update_executors(self, executors, *, defer_ownership_contention=False):
        """Persist a shutdown group with one lookup and fenced transaction."""
        from hummingbot.core.operational_warnings import ExecutorArchiveBusy
        from hummingbot.model.intent_admission import deferred_recorder_transaction

        values = [(executor.config.id, executor.executor_info.model_dump_json()) for executor in executors]
        if not values:
            return
        manager = self._sql_manager
        with manager.get_new_session() as session, (
            deferred_recorder_transaction(manager, session, ExecutorArchiveBusy, 'Executor archive waits for the ownership row.')
            if defer_ownership_contention else session.begin()
        ):
            self._write_executor_snapshots(session, values)

    @trace_runtime('executor.archive')
    async def archive_executors(self, snapshots, *, defer_contention):
        """Use the recorder worker and retain ownership until its transaction settles."""
        from hummingbot.core.operational_warnings import ExecutorArchiveBusy
        from hummingbot.core.order_submission_guard import active_guard

        owner = active_guard()
        task = asyncio.current_task()
        already_tracked = task in owner.terminal_tasks
        owner.terminal_tasks.add(task)
        try:
            busy = ExecutorArchiveBusy('Executor archive waits for the ownership row.') if defer_contention else None
            _, cancelled = await self._run_native_write(self._write_executor_snapshots, snapshots, defer_contention=busy)
            return cancelled
        finally:
            if not already_tracked:
                owner.terminal_tasks.discard(task)

    @staticmethod
    def _write_executor_snapshots(session, snapshots):
        existing = {row.id: row for row in session.query(Executors).filter(
            Executors.id.in_([identity for identity, _ in snapshots])).all()}
        for identity, snapshot in snapshots:
            data = json.loads(snapshot)
            row = existing.get(identity)
            if row is None:
                row = Executors(**data)
                session.add(row)
                existing[identity] = row
            else:
                for attr, value in data.items():
                    setattr(row, attr, value)

    def store_position(self, position: Position):
        with self._sql_manager.get_new_session() as session:
            session.add(position)
            session.commit()

    def update_or_store_position(self, position: Position):
        self.update_or_store_positions([position])

    def update_or_store_positions(self, positions: List[Position], *, session=None):
        """Commit a complete hold snapshot with one lookup and recorder fence.

        Preserve existing row identities and last-snapshot-wins behavior for
        repeated natural keys, including the legacy single-position entrypoint.
        The caller retains in-memory exposure until the entire commit succeeds.
        """
        if not positions:
            return
        if session is None:
            with self._sql_manager.get_new_session() as session, session.begin():
                self.update_or_store_positions(positions, session=session)
            return
        keys = ('controller_id', 'connector_name', 'trading_pair', 'side')

        def identity(position):
            # Reviewed OKX spot holds are a single net position. Direction is
            # mutable when a sell crosses the managed net through zero; it is
            # still part of identity for independent perpetual hedge legs.
            return tuple(getattr(position, key) for key in keys[:3]) + (
                None if position.connector_name == 'okx' else position.side,)

        snapshots = {}
        for position in positions:
            key = identity(position)
            if key in snapshots and snapshots[key].side != position.side:
                raise ValueError('Ambiguous spot position snapshots require reconciliation')
            snapshots[key] = position
        spot = [key[:3] for key in snapshots if key[1] == 'okx']
        other = [key for key in snapshots if key[1] != 'okx']
        predicates = []
        if spot:
            predicates.append(tuple_(*(getattr(Position, key) for key in keys[:3])).in_(spot))
        if other:
            predicates.append(tuple_(*(getattr(Position, key) for key in keys)).in_(other))
        existing = {}
        for row in session.query(Position).filter(or_(*predicates)).all():
            key = identity(row)
            if key in existing:
                kind = 'spot' if row.connector_name == 'okx' else 'perpetual'
                raise ValueError(f'Ambiguous {kind} position history requires reconciliation')
            existing.setdefault(key, row)
        fields = ('side', 'timestamp', 'volume_traded_quote', 'amount', 'breakeven_price',
                  'unrealized_pnl_quote', 'realized_pnl_quote', 'cum_fees_quote')
        for key, position in snapshots.items():
            row = existing.get(key)
            if row is None:
                session.add(position)
            else:
                for field in fields:
                    setattr(row, field, getattr(position, field))

    @property
    def durable_position_checkpoints(self):
        return self._sql_manager.engine.dialect.name == 'postgresql'

    def applied_position_executors(self, identities):
        from hummingbot.model.runtime_journal import applied_executors
        with self._sql_manager.get_new_session() as session:
            return applied_executors(session, identities)

    @staticmethod
    def _iter_held_order_fill_proofs(session, identities):
        """Bound both network waits and resident fill rows on this transaction.

        Each UNION member has its own overflow sentinel. A shared outer LIMIT
        could hide an oversized order behind another order's fills. Retain only
        one eight-order chunk, never cache proof across callbacks/transactions.
        """
        for offset in range(0, len(identities), 8):
            chunk = identities[offset:offset + 8]
            statements = [select(TradeFill).where(TradeFill.order_id == identity).limit(1001) for identity in chunk]
            # Nest each limited member so SQLite and PostgreSQL both apply
            # LIMIT to that order, rather than to the combined result.
            statement = union_all(*(select(*item.subquery().c) for item in statements)) if len(statements) > 1 else statements[0]
            rows = session.query(TradeFill).from_statement(statement).all()
            grouped = {identity: [] for identity in chunk}
            for row in rows:
                grouped[row.order_id].append(row)
            for identity in chunk:
                yield identity, grouped[identity]
            del rows, grouped

    def ensure_runtime_checkpoint(self):
        """Empty ledgers may establish a baseline without inventing old history.

        Existing ledgers require the reviewed recovery proof to bind their first
        baseline. A normal Start cannot certify legacy financial completeness.
        """
        from hummingbot.core.order_submission_guard import active_guard
        from hummingbot.model.runtime_journal import (
            RuntimeCheckpoint,
            binding_hash,
            bindings,
            credential_fingerprint,
            digest,
            environment_identity,
            financial_hash,
        )
        with self._sql_manager.get_new_session() as session, session.begin():
            existing = session.get(RuntimeCheckpoint, self._config_file_path)
            owner = active_guard()
            current_bindings = bindings(session, owner.store.bot_id)
            anchors = {name: credential_fingerprint(connector) for name, connector in owner.connectors.items()}
            if existing is not None:
                if existing.financial_hash != financial_hash(session) or existing.payload.get('environment') != environment_identity():
                    return False
                previous_bindings = existing.payload.get('bindings') or []

                def identity(rows):
                    return sorted((r['connector'], r['environment'], r['account_id']) for r in rows)
                if identity(previous_bindings) != identity(current_bindings):
                    return False
                old_anchors = existing.payload.get('credential_fingerprints') or {}
                same_version = sorted(previous_bindings, key=digest) == sorted(current_bindings, key=digest)
                if same_version and any(name in old_anchors and old_anchors[name] != value for name, value in anchors.items()):
                    return False
                existing.bindings_hash = binding_hash(session, owner.store.bot_id)
                existing.payload = {**existing.payload, 'bindings': current_bindings,
                                    'credential_fingerprints': {**old_anchors, **anchors}}
                return True
            if session.query(TradeFill.order_id).first() or session.query(Position.id).first():
                return False
            session.add(RuntimeCheckpoint(config_file_path=self._config_file_path,
                                          financial_hash=financial_hash(session), bindings_hash=binding_hash(session, owner.store.bot_id),
                                          watermark=0, payload={'baseline': 'empty_ledger', 'generation': owner.generation,
                                                                'environment': environment_identity(),
                                                                'bindings': current_bindings,
                                                                'credential_fingerprints': anchors}))
        return True

    def get_reduction_bases(self):
        """Startup-only read; no history scans or I/O on the quote/send path."""
        from hummingbot.model.runtime_journal import RuntimeCheckpoint, financial_hash
        from hummingbot.strategy_v2.utils.perpetual_reduction import restore_basis_checkpoint
        if not self.durable_position_checkpoints:
            return {}
        with self._sql_manager.get_new_session() as session:
            checkpoint = session.get(RuntimeCheckpoint, self._config_file_path)
            if checkpoint is None or 'reduction_basis' not in checkpoint.payload:
                return {}
            try:
                if checkpoint.financial_hash != financial_hash(session):
                    return {}
                return restore_basis_checkpoint(checkpoint.payload['reduction_basis'], checkpoint.financial_hash)
            except (ValueError, ArithmeticError, TypeError):
                return {}

    @trace_runtime('position.checkpoint')
    def checkpoint_held_positions(self, positions, infos, *, reduction_bases=None, defer_ownership_contention=False):
        """Native transfer order, financial rows and application receipt commit together.

        The caller only publishes the new in-memory hold after this transaction.
        A lost commit acknowledgement is resolved from the receipt after restart;
        it never means that a terminal executor can be applied a second time.
        """
        from hummingbot.core.operational_warnings import PositionCheckpointBusy
        from hummingbot.model.intent_admission import deferred_recorder_transaction
        manager = self._sql_manager
        manager.assert_persistence_healthy()
        with manager.get_new_session() as session, (
            deferred_recorder_transaction(manager, session, PositionCheckpointBusy, 'Position checkpoint waits for the ownership row.')
            if defer_ownership_contention else session.begin()
        ):
            if self._stage_held_positions(session, positions, infos, reduction_bases):
                session.flush()
                from hummingbot.core.order_submission_guard import fence_session
                fence_session(session)

    @trace_runtime('position.checkpoint')
    async def persist_held_positions(self, positions, infos, reduction_bases, *, defer_ownership_contention):
        from hummingbot.core.operational_warnings import PositionCheckpointBusy
        from hummingbot.core.order_submission_guard import active_guard

        owner = active_guard()
        task = asyncio.current_task()
        already_tracked = task in owner.terminal_tasks
        owner.terminal_tasks.add(task)
        try:
            busy = PositionCheckpointBusy('Position checkpoint waits for the ownership row.') if defer_ownership_contention else None
            _, cancelled = await self._run_native_write(
                self._stage_held_positions, positions, infos, reduction_bases, defer_contention=busy)
            return cancelled
        finally:
            if not already_tracked:
                owner.terminal_tasks.discard(task)

    def _stage_held_positions(self, session, positions, infos, reduction_bases):
        from hummingbot.model.runtime_journal import (
            RuntimeCheckpoint,
            RuntimeJournal,
            advance_checkpoints,
            applied_executors,
            financial_hash,
        )
        if applied_executors(session, [info.id for info in infos]):
            raise ValueError('Position application changed; reload its durable checkpoint')
        checkpoint = session.get(RuntimeCheckpoint, self._config_file_path)
        if checkpoint is not None and checkpoint.financial_hash != financial_hash(session):
            raise ValueError('Position checkpoint changed outside its application journal')
        previous_bases = {}
        if checkpoint is not None and reduction_bases is not None and 'reduction_basis' in checkpoint.payload:
            from hummingbot.strategy_v2.utils.perpetual_reduction import restore_basis_checkpoint
            try:
                previous_bases = restore_basis_checkpoint(checkpoint.payload['reduction_basis'], checkpoint.financial_hash)
            except (ValueError, ArithmeticError, TypeError):
                pass  # Unproven old costs cannot supply authority to a new session.
        # Prefetch rows by identity on this transaction, in bounded SQL
        # parameter groups. Fill reads also batch, with an independent
        # 1001-row sentinel per order; no proof survives this transaction.
        executor_ids = list(dict.fromkeys(info.id for info in infos))
        order_ids = list(dict.fromkeys(
            order['client_order_id'] for info in infos for order in info.custom_info.get('held_position_orders', [])))
        batch = len(infos) > 1 or len(order_ids) > 1
        existing_executors, recorded_orders = {}, {}
        lookups = ((Executors, executor_ids, existing_executors), (Order, order_ids, recorded_orders))
        if batch:
            for model, identities, target in lookups:
                for offset in range(0, len(identities), 100):
                    target.update((row.id, row) for row in session.query(model).filter(
                        model.id.in_(identities[offset:offset + 100])).all())
        self.update_or_store_positions(positions, session=session)
        # The reads below concern committed fills, not the pending hold,
        # executor or position writes. Validate every member before their
        # first guarded flush; retain shared ORM identity for duplicate-order
        # rejection within the group.
        # Single transfers need no prefetch, but their metadata/fill reads
        # must not flush the pending financial writes either. All proof
        # remains on this transaction before the first guarded write.
        with session.no_autoflush:
            fill_proofs = self._iter_held_order_fill_proofs(session, order_ids)
            for info in infos:
                data = json.loads(info.model_dump_json())
                existing = existing_executors.get(info.id) if batch else session.get(Executors, info.id)
                if existing is None:
                    existing = existing_executors[info.id] = Executors(**data)
                    session.add(existing)
                else:
                    for name, value in data.items():
                        setattr(existing, name, value)
                orders = info.custom_info.get('held_position_orders', [])
                session.add(RuntimeJournal(event_key='hold:' + info.id, kind='hold', applied=True,
                                           payload={'executor': data,
                                                    'positions': [{c.name: str(getattr(p, c.name))
                                                                   for c in Position.__table__.columns} for p in positions]}))
                for order in orders:
                    identity = order['client_order_id']
                    recorded_order = recorded_orders.get(identity) if batch else session.get(Order, identity)
                    managed = (getattr(info.config, 'mm_session_id', None) is not None
                               and getattr(info.config, 'mm_generation', None) is not None)
                    if managed and recorded_order is None:
                        raise ValueError('Durable order identity is missing from the managed held transfer')
                    if recorded_order is not None and recorded_order.position_applied:
                        raise ValueError('Fill application already belongs to a held executor')
                    proven_identity, fills = next(fill_proofs, (None, ()))
                    if proven_identity != identity:
                        raise ValueError('Fill coverage order identity mismatch')
                    if len(fills) > 1000:
                        raise ValueError('Fill coverage exceeds the bounded native transfer')
                    if fills or managed:
                        if (any(fill.trade_fee_in_quote is None for fill in fills)
                                or sum((fill.amount for fill in fills), Decimal(0)) != Decimal(str(order['executed_amount_base']))
                                or sum((fill.amount * fill.price for fill in fills), Decimal(0)) != Decimal(str(order['executed_amount_quote']))
                                or sum((fill.trade_fee_in_quote for fill in fills), Decimal(0)) != Decimal(str(order['cumulative_fee_paid_quote']))
                                or any(fill.symbol != info.trading_pair or fill.market != info.connector_name
                                       or fill.trade_type != order['trade_type'] for fill in fills)):
                            raise ValueError('Fill coverage does not match the native held transfer')
                        native_ids = order.get('order_fills')
                        if native_ids is not None and set(native_ids) != {fill.exchange_trade_id for fill in fills}:
                            raise ValueError('Fill coverage identity mismatch')
                    session.add(RuntimeJournal(event_key='applied-order:' + identity, kind='applied_order', order_id=identity,
                                               applied=True, payload={'executor_id': info.id, 'order': order}))
                    if recorded_order is not None:
                        recorded_order.position_applied = True
        for offset in range(0, len(order_ids), 100):
            session.query(RuntimeJournal).filter(RuntimeJournal.kind == 'fill',
                                                 RuntimeJournal.order_id.in_(order_ids[offset:offset + 100])).update(
                {RuntimeJournal.applied: True}, synchronize_session=False)
        if checkpoint is not None:
            advance_checkpoints(session, [checkpoint])
            if reduction_bases is not None:
                from hummingbot.strategy_v2.utils.perpetual_reduction import basis_checkpoint
                scope = {(row.controller_id, row.connector_name, row.trading_pair, row.side): row.amount
                         for row in positions}
                if any(key not in scope or basis.amount != scope[key] for key, basis in reduction_bases.items()):
                    raise ValueError('Remaining costs do not cover the exact owned position snapshot')
                # An inactive controller's unchanged position is outside this
                # batch. Carry its verified costs; drop costs for any updated
                # position whose new basis is unavailable.
                retained = {key: basis for key, basis in previous_bases.items() if key not in scope}
                retained.update(reduction_bases)
                checkpoint.payload = {**checkpoint.payload,
                                      'reduction_basis': basis_checkpoint(checkpoint.financial_hash, retained)}
        # Coalescing can leave no later dirty checkpoint (legacy baseline
        # absent). The synchronous caller then checks ownership explicitly.
        return batch or checkpoint is None

    def store_controller_config(self, controller_config: ControllerConfigBase):
        with self._sql_manager.get_new_session() as session:
            config = json.loads(controller_config.json())
            base_columns = ["id", "timestamp", "type"]
            controller = Controllers(id=config["id"],
                                     timestamp=time.time(),
                                     type=config["controller_type"],
                                     config={k: v for k, v in config.items() if k not in base_columns})
            session.add(controller)
            session.commit()

    def get_executors_by_ids(self, executor_ids: List[str]):
        with self._sql_manager.get_new_session() as session:
            executors = session.query(Executors).filter(Executors.id.in_(executor_ids)).all()
            return executors

    def get_executors_by_controller(self, controller_id: str = None) -> List[ExecutorInfo]:
        with self._sql_manager.get_new_session() as session:
            executors = session.query(Executors).filter(Executors.controller_id == controller_id).all()
            return [executor.to_executor_info() for executor in executors]

    def get_all_executors(self) -> List[ExecutorInfo]:
        with self._sql_manager.get_new_session() as session:
            executors = session.query(Executors).all()
            return [executor.to_executor_info() for executor in executors]

    def iter_executor_performance(self, controller_ids: List[str], *, compact: bool = False) -> Iterator[Row]:
        """Stream only historical PnL fields for the selected controllers.

        Keep aggregation in the existing orchestrator: SQL SUM on Float columns
        would change its Float -> Decimal arithmetic. Read the same column set
        and SQL order without allocating a mapped object for every history row.
        """
        if not controller_ids:
            return
        with self._sql_manager.get_new_session() as session:
            if compact:
                from hummingbot.model.db_migration.postgres.io_deadline import bootstrap_io_budget

                # Keep both branches in one snapshot. Count-only rows do not
                # need an ordinal. Keep non-finite held rows for validation.
                # OFFSET 0 keeps the contribution filter above the original
                # controller scan so it cannot change the Float-to-Decimal
                # addition order by changing that scan's access path.
                statement = text("""
                    WITH scoped AS NOT MATERIALIZED (
                        SELECT controller_id, close_type, net_pnl_quote, filled_amount_quote,
                               (net_pnl_quote = 0 AND filled_amount_quote = 0)
                               OR (close_type IS NOT DISTINCT FROM :held
                                   AND abs(net_pnl_quote) < 'Infinity'::double precision
                                   AND abs(filled_amount_quote) < 'Infinity'::double precision) AS count_only
                        FROM executors
                        WHERE controller_id IN :controller_ids
                    )
                    SELECT controller_id, close_type, net_pnl_quote, filled_amount_quote, history_count
                    FROM (
                        SELECT controller_id, close_type, 0::double precision AS net_pnl_quote,
                               0::double precision AS filled_amount_quote, count(*) AS history_count, 0 AS ordinal
                        FROM scoped
                        WHERE count_only
                        GROUP BY controller_id, close_type
                        UNION ALL
                        SELECT controller_id, close_type,
                               net_pnl_quote, filled_amount_quote, 1 AS history_count, row_number() OVER () AS ordinal
                        FROM (SELECT * FROM scoped OFFSET 0) AS source
                        WHERE NOT count_only
                    ) AS history
                    ORDER BY ordinal
                """).bindparams(bindparam('controller_ids', expanding=True))
                # Startup reads already run on a dedicated worker. Give this
                # history-only cursor a bounded budget independent of order I/O.
                connection = session.connection()
                with bootstrap_io_budget(connection, time.monotonic() + 20):
                    connection.exec_driver_sql("SET LOCAL statement_timeout = '12s'")
                    parameters = {'controller_ids': controller_ids, 'held': CloseType.POSITION_HOLD.value}
                    page_size = 4000
                    with connection.execute(statement, parameters, execution_options={'yield_per': page_size}) as rows:
                        while page := rows.fetchmany(page_size):
                            yield from page
                            # A short PostgreSQL cursor page already proves EOF.
                            if len(page) < page_size:
                                break
                return
            yield from (session.query(Executors.id, Executors.close_type, Executors.net_pnl_quote,
                                      Executors.filled_amount_quote, Executors.controller_id)
                        .filter(Executors.controller_id.in_(controller_ids)).yield_per(1000))

    def get_positions_by_ids(self, position_ids: List[str]) -> List[Position]:
        with self._sql_manager.get_new_session() as session:
            positions = session.query(Position).filter(Position.id.in_(position_ids)).all()
            return positions

    def get_positions_by_controller(self, controller_id: str = None) -> List[Position]:
        with self._sql_manager.get_new_session() as session:
            positions = session.query(Position).filter(Position.controller_id == controller_id).all()
            return positions

    def get_all_positions(self, controller_ids: Optional[List[str]] = None) -> List[Position]:
        if controller_ids == []:
            return []
        with self._sql_manager.get_new_session() as session:
            query = session.query(Position)
            if controller_ids is not None:
                query = query.filter(Position.controller_id.in_(controller_ids))
            positions = query.all()
            spot_scopes = set()
            for position in positions:
                if position.connector_name == 'okx':
                    scope = (position.controller_id, position.connector_name, position.trading_pair)
                    if scope in spot_scopes:
                        raise ValueError('Ambiguous spot position history requires reconciliation')
                    spot_scopes.add(scope)
            return positions

    def has_uncovered_positions(self, controller_ids: List[str]) -> bool:
        """One startup coverage check; retained foreign exposure must not vanish."""
        with self._sql_manager.get_new_session() as session:
            return session.query(Position.id).filter(Position.amount != 0,
                                                     ~Position.controller_id.in_(controller_ids)).first() is not None

    def get_orders_for_config_and_market(self, config_file_path: str, market: ConnectorBase,
                                         with_exchange_order_id_present: Optional[bool] = False,
                                         number_of_rows: Optional[int] = None) -> List[Order]:
        with self._sql_manager.get_new_session() as session:
            filters = [Order.config_file_path == config_file_path,
                       Order.market == market.display_name]
            if with_exchange_order_id_present:
                filters.append(Order.exchange_order_id.isnot(None))
            query: Query = (session
                            .query(Order)
                            .filter(*filters)
                            .order_by(Order.creation_timestamp))
            if number_of_rows is None:
                return query.all()
            else:
                # Keep ascending caller order, but warm reconciliation with the
                # most recent ACK mappings instead of the oldest historical ones.
                recent = query.order_by(None).order_by(Order.creation_timestamp.desc(), Order.id.desc())
                return list(reversed(recent.limit(number_of_rows).all()))

    def get_trades_for_config(self, config_file_path: str, number_of_rows: Optional[int] = None,
                              market: Optional[str] = None) -> List[TradeFill]:
        with self._sql_manager.get_new_session() as session:
            query: Query = (session
                            .query(TradeFill)
                            .filter(TradeFill.config_file_path == config_file_path)
                            .order_by(TradeFill.timestamp.desc()))
            if market is not None:
                query = query.filter(TradeFill.market == market)
            if number_of_rows is None:
                return query.all()
            else:
                return query.limit(number_of_rows).all()

    def save_market_states(self, config_file_path: str, market: ConnectorBase, session: Session,
                           *, new_order_id: Optional[str] = None, tracking_states=None, staged_orders=(),
                           tracking_basis=None):
        saved_state = dict(market.tracking_states if tracking_states is None else tracking_states)
        self._save_market_state_snapshot(session, config_file_path, market.display_name, saved_state, new_order_id,
                                         staged_orders=staged_orders, tracking_basis=tracking_basis)

    def _save_market_state_snapshot(self, session, config_file_path, market_name, saved_state, new_order_id=None,
                                    *, staged_orders=(), tracking_basis=None):
        # A native intent or create ACK already supplies an updated snapshot.
        # Decode the old JSON only when unresolved identities need merging.
        # Ordinary checkpoints retain full reads and unchanged-JSON dirty checking.
        postgres = self._sql_manager.engine.dialect.name == "postgresql"
        pending_ids = None
        if postgres and (saved_state or tracking_basis is not None):
            # Tracking begins before queued creates pass the durable send gate.
            # A sibling intent must not checkpoint those never-admitted creates:
            # a later switch would restore them as nonexistent exchange orders.
            if tracking_basis is None:
                tracking_basis = self._read_tracking_basis(
                    session, config_file_path, market_name, saved_state,
                    include_saved_state=new_order_id not in saved_state)
            market_states, admitted_ids, pending_ids = tracking_basis
            # Tracker identities staged in this transaction can commit with
            # their intents, even before they are visible to the SELECT.
            admitted_ids = set(admitted_ids or ()) | {order.id for order in staged_orders}
            saved_state = {order_id: state for order_id, state in saved_state.items() if order_id in admitted_ids}
            pending_ids = pending_ids or ()
        else:
            market_states = self._get_market_states(session, config_file_path, market_name)
        timestamp: int = self.db_timestamp
        if market_states is not None:
            # A connector may discard a pending create after a lost HTTP ack. Keep
            # its last durable tracking identity until reconciliation resolves it.
            if pending_ids is None:
                # Empty PostgreSQL snapshots and the legacy file backend retain
                # their existing single pending-identity lookup.
                pending_ids = [order_id for (order_id,) in session.query(Order.id).filter_by(
                    config_file_path=config_file_path, market=market_name, creation_pending=True).all()]
            resolved = {order.id for order in staged_orders if not order.creation_pending}
            for order_id in pending_ids:
                if order_id in resolved:
                    continue  # The current ACK already resolves this pending identity.
                if order_id not in saved_state and order_id in market_states.saved_state:
                    saved_state[order_id] = market_states.saved_state[order_id]

        if market_states is not None:
            market_states.saved_state = saved_state
            market_states.timestamp = timestamp
        else:
            market_states = MarketState(config_file_path=config_file_path,
                                        market=market_name,
                                        timestamp=timestamp,
                                        saved_state=saved_state)
            session.add(market_states)

    @staticmethod
    def _read_tracking_basis(session, config_file_path, market_name, tracking_states, *, include_saved_state):
        statement = MarketsRecorder._tracking_basis_query(config_file_path, market_name, tracking_states)
        if not include_saved_state:
            statement = statement.options(defer(MarketState.saved_state))
        return session.execute(statement).one()

    @staticmethod
    def _tracking_basis_query(config_file_path, market_name, tracking_states):
        orders = MarketsRecorder._tracking_order_ids(config_file_path, market_name, tracking_states)
        return select(MarketState, orders.c.admitted_ids, orders.c.pending_ids).select_from(orders).outerjoin(
            MarketState, and_(MarketState.config_file_path == config_file_path, MarketState.market == market_name))

    def _read_intent_basis(self, session, bot_id, record, tracking_states):
        from hummingbot.model.runtime_journal import active_bindings_query

        # Project only binding metadata in the same snapshot as the tracker.
        bindings = active_bindings_query(bot_id).subquery()
        current = select(func.jsonb_agg(func.to_jsonb(bindings.table_valued()))).scalar_subquery()
        statement = self._tracking_basis_query(record.config_file_path, record.market, tracking_states)
        row = session.execute(statement.add_columns(current).options(defer(MarketState.saved_state))).one()
        return row[:3], row[3] or []

    @staticmethod
    def _tracking_order_ids(config_file_path, market_name, tracking_states):
        # The aggregate yields one row even for a new market with no orders.
        # Read the checkpoint and its scoped order proof from one DB snapshot.
        pending = Order.creation_pending.is_(True)
        return select(func.array_agg(Order.id).label('admitted_ids'),
                      func.array_agg(Order.id).filter(pending).label('pending_ids')).where(
            Order.config_file_path == config_file_path, Order.market == market_name,
            or_(Order.id.in_(tracking_states), pending)).subquery()

    @trace_runtime('order.intent', order_parameter='order')
    def record_order_intent(self, market: ConnectorBase, order) -> None:
        """Persist existing order identity/tracking state before an external submission."""
        from hummingbot.model.runtime_journal import mark_prepared_order

        with self._sql_manager.order_intent_transaction(order.client_order_id) as session:
            record, cleanup, attempt = self._capture_order_intent(market, order)
            self._stage_order_intent(session, record, cleanup, attempt, dict(market.tracking_states))
            if attempt is not None:
                mark_prepared_order(order, attempt)

    @trace_runtime('order.intent', order_parameter='order')
    async def persist_order_intent(self, market, order):
        from hummingbot.core.bot_ownership import OwnershipLost
        from hummingbot.core.order_submission_guard import IntentAdmissionBusy, active_guard
        from hummingbot.model.runtime_journal import mark_prepared_order

        owner = active_guard()
        owner.require_owner_connection()
        try:
            record, cleanup, attempt = self._capture_order_intent(market, order)
            tracking_states = dict(market.tracking_states)
        except Exception:
            self._sql_manager.persistence_healthy = False
            raise

        def write():
            try:
                # The mutable connector/tracker stay on the engine. The worker
                # opens and closes its own session for the entire transaction.
                with self._sql_manager.order_intent_transaction(record.id, worker_owner=owner) as session:
                    self._stage_order_intent(session, record, cleanup, attempt, tracking_states, coalesce_snapshot=True)
                    session.flush()
                    self._sql_manager.assert_persistence_healthy()
                    owner.fence_recorder_session(session)
            except IntentAdmissionBusy as error:
                return error
            return None

        try:
            busy, cancelled = await self._drain_order_write(write)
        except OwnershipLost as error:
            raise owner._lose_ownership(str(error), error.reason_code) from None
        if busy is not None:
            raise IntentAdmissionBusy(str(busy), cancellation=cancelled, ownership_evidence=busy.ownership_evidence) from None
        if attempt is not None:
            mark_prepared_order(order, attempt)
        return cancelled

    @trace_runtime('order.intent_batch')
    async def persist_order_intents(self, intents):
        """Commit a bounded set of already-authorized native intents together."""
        from hummingbot.core.bot_ownership import OwnershipLost
        from hummingbot.core.order_submission_guard import IntentAdmissionBusy, active_guard
        from hummingbot.model.runtime_journal import mark_prepared_order, prepare_attempt

        owner = active_guard()
        owner.require_owner_connection()
        records, by_market, tracking = [], {}, {}
        try:
            for intent in intents:
                record, cleanup, attempt = intent.context.run(self._capture_order_intent, intent.connector, intent.order)
                if cleanup is not None or attempt is None:
                    raise ValueError('An intent batch requires ordinary managed order identities.')
                records.append((intent.order, record, attempt))
                if record.market not in by_market:
                    by_market[record.market] = []
                    tracking[record.market] = dict(intent.connector.tracking_states)
                by_market[record.market].append(record)
        except Exception:
            self._sql_manager.persistence_healthy = False
            raise

        def write():
            try:
                identities = tuple(record.id for _, record, _ in records)
                with self._sql_manager.order_intent_transaction(identities, worker_owner=owner) as session:
                    with session.no_autoflush:
                        first = records[0][1]
                        basis, current_bindings = self._read_intent_basis(session, owner.store.bot_id, first, tracking[first.market])
                        for _, record, attempt in records:
                            session.add(record)
                            prepare_attempt(session, record.id, attempt, current_bindings=current_bindings)
                            session.add(OrderStatus(order=record, timestamp=record.creation_timestamp, status=record.last_status))
                        for market, orders in by_market.items():
                            self._save_market_state_snapshot(session, self._config_file_path, market, tracking[market],
                                                             orders[0].id, staged_orders=orders,
                                                             tracking_basis=basis if market == first.market else None)
                    session.flush()
                    self._sql_manager.assert_persistence_healthy()
                    owner.fence_recorder_session(session)
            except IntentAdmissionBusy as error:
                return error
            return None

        try:
            busy, cancelled = await self._drain_order_write(write)
        except OwnershipLost as error:
            raise owner._lose_ownership(str(error), error.reason_code) from None
        if busy is not None:
            raise IntentAdmissionBusy(str(busy), cancellation=cancelled, ownership_evidence=busy.ownership_evidence) from None
        for order, _, attempt in records:
            mark_prepared_order(order, attempt)
        return cancelled

    def _capture_order_intent(self, market, order):
        from hummingbot.core.cleanup_authority import current_cleanup_scope
        from hummingbot.core.order_submission_guard import active_guard
        from hummingbot.model.runtime_journal import capture_attempt_context

        cleanup = current_cleanup_scope()
        base, quote = order.trading_pair.split("-")
        timestamp = int(order.creation_timestamp * 1000)
        price = order.price if order.price is not None and order.price.is_finite() else Decimal("0")
        record = Order(id=order.client_order_id, config_file_path=self._config_file_path,
                       strategy=self._strategy_name, market=market.display_name, symbol=order.trading_pair,
                       base_asset=base, quote_asset=quote, creation_timestamp=timestamp,
                       order_type=order.order_type.name, amount=order.amount, price=price,
                       leverage=order.leverage or 1, position=order.position.value,
                       last_status="Pending" + order.trade_type.name.title(),
                       last_update_timestamp=timestamp, creation_pending=True,
                       cleanup_operation_id=cleanup.operation_id if cleanup is not None else None,
                       cleanup_scope=cleanup.json() if cleanup is not None else None)
        attempt = capture_attempt_context(order, active_guard(), market) if self.durable_position_checkpoints else None
        return record, cleanup, attempt

    def _stage_order_intent(self, session, record, cleanup, attempt, tracking_states, *, coalesce_snapshot=False):
        from hummingbot.model.runtime_journal import prepare_attempt

        session.add(record)
        if coalesce_snapshot and cleanup is None and attempt is not None:
            with session.no_autoflush:
                basis, current_bindings = self._read_intent_basis(session, attempt['bot_id'], record, tracking_states)
                prepare_attempt(session, record.id, attempt, current_bindings=current_bindings)
                # An intent transaction already proved this identity is new.
                session.add(OrderStatus(order=record, timestamp=record.creation_timestamp, status=record.last_status))
                self._save_market_state_snapshot(session, record.config_file_path, record.market, tracking_states, record.id,
                                                 staged_orders=(record,), tracking_basis=basis)
            return
        if attempt is not None:
            # Binding metadata does not depend on the pending Order INSERT.
            # Persist the complete identity/status together at the existing
            # checkpoint read, preserving its write fence and final commit.
            with session.no_autoflush:
                prepare_attempt(session, record.id, attempt)
        if cleanup is not None:
            from hummingbot.model.cleanup_positions import record_cleanup_intent
            record_cleanup_intent(session, cleanup, record)
        # Keep the original side even when a terminal update precedes any
        # Created callback. This records intent, not exchange admission.
        with session.no_autoflush:
            self._add_order_status(session, record, record.creation_timestamp, record.last_status)
        self._save_market_state_snapshot(session, record.config_file_path, record.market, tracking_states, record.id)

    def _event_configuration(self, session, market, order):
        if order is None or order.config_file_path == self._config_file_path:
            return self._config_file_path, self._strategy_name
        if self.cleanup_operation_id is not None and order.market == market.display_name:
            from hummingbot.model.bot_state import BotState
            active = session.query(BotState.bot_id).filter(
                BotState.recovery_state["credential_operation"]["id"].astext == self.cleanup_operation_id,
                BotState.recovery_state["credential_operation"]["action"].astext == "credentials_disconnect").first()
            if active:
                return order.config_file_path, order.strategy
        raise ValueError("Order identity conflicts with its recorded configuration")

    def reject_unsent_order(self, market: ConnectorBase, order_id: str, *, require_existing: bool = False,
                            rejection_receipt=None, rejection_reason=None) -> None:
        from hummingbot.connector.okx_order_status import order_rejection_reason
        from hummingbot.model.runtime_journal import SubmissionAttempt
        with self._sql_manager.get_new_session() as session, session.begin():
            order = session.get(Order, order_id, with_for_update=True)
            attempt = None
            if rejection_receipt is not None or rejection_reason is not None:
                from hummingbot.core.order_submission_guard import active_guard
                guard = active_guard()
                if guard is None or guard.engine is not self.sql_manager.engine:
                    raise ValueError("Exchange rejection requires the current trading owner.")
                guard.check_owner()
                attempt = session.get(SubmissionAttempt, order_id, with_for_update=True)
                reason = order_rejection_reason(attempt.receipt, order_id) if attempt is not None else None
                if (not reason or (rejection_receipt is not None and attempt.receipt != rejection_receipt)
                        or (rejection_reason is not None and reason != rejection_reason)
                        or attempt.state not in ('possibly_sent', 'uncertain', 'rejected')
                        or session.query(TradeFill.order_id).filter_by(order_id=order_id).first() is not None):
                    raise ValueError("Exchange rejection conflicts with durable submission evidence.")
            if order is None and (require_existing or attempt is not None):
                raise ValueError("Rejected order has no durable submission intent.")
            if order is not None:
                already_rejected = (attempt is not None and attempt.state == 'rejected'
                                    and order.last_status == MarketEvent.OrderFailure.name and not order.creation_pending)
                if ((not order.creation_pending and not already_rejected) or order.exchange_order_id
                        or order.market != market.display_name or order.config_file_path != self._config_file_path):
                    raise ValueError("Unsent rejection conflicts with acknowledged order history.")
                order.creation_pending = False
                order.last_status = MarketEvent.OrderFailure.name
                if attempt is not None:
                    if not already_rejected:
                        from hummingbot.core.admin_identity import Principal
                        from hummingbot.model.admin_access import AdminAccessStore
                        AdminAccessStore(guard.engine, guard.store.bot_id).audit(
                            session.connection(), actor=Principal('owner:' + guard.owner_uuid, 'runtime', 'write'),
                            action='automatic_recovery_order_rejected', outcome='succeeded',
                            request_id='automatic:' + order_id, target_id=order_id,
                            reason_code=reason, details={'order_id': order_id})
                    attempt.state = 'rejected'
                else:
                    self._attempt_observation(session, order_id, 'rejected')
            saved = self.get_market_states(self._config_file_path, market, session)
            if saved is not None:
                state = dict(saved.saved_state)
                state.pop(order_id, None)
                saved.saved_state = state

    def restore_market_states(self, config_file_path: str, market: ConnectorBase):
        with self._sql_manager.get_new_session() as session:
            market_states: Optional[MarketState] = self.get_market_states(config_file_path, market, session=session)

            if market_states is not None:
                market.restore_tracking_states(market_states.saved_state)

    def get_market_states(self,
                          config_file_path: str,
                          market: ConnectorBase,
                          session: Session, *, include_saved_state: bool = True) -> Optional[MarketState]:
        return self._get_market_states(session, config_file_path, market.display_name, include_saved_state=include_saved_state)

    @staticmethod
    def _get_market_states(session, config_file_path, market_name, *, include_saved_state=True):
        query: Query = (session
                        .query(MarketState)
                        .filter(MarketState.config_file_path == config_file_path,
                                MarketState.market == market_name))
        if not include_saved_state:
            query = query.options(defer(MarketState.saved_state))
        market_states: Optional[MarketState] = query.one_or_none()
        return market_states

    def _did_create_order(self,
                          event_tag: int,
                          market: ConnectorBase,
                          evt: Union[BuyOrderCreatedEvent, SellOrderCreatedEvent]):
        if threading.current_thread() != threading.main_thread():
            self._ev_loop.call_soon_threadsafe(self._did_create_order, event_tag, market, evt)
            return

        if evt is self._published_order_event:
            return
        with self._sql_manager.get_new_session() as session, session.begin():
            self._stage_order_creation(session, event_tag, market, evt)
        self.finish_recorded_order_creation(market, evt)

    @trace_runtime('order.created', order_parameter='evt')
    def _stage_order_creation(self, session, event_tag, market, evt, *, tracking_states=None, coalesce_snapshot=False,
                              defer_snapshot=False, evidence=None):
        base_asset, quote_asset = evt.trading_pair.split("-")
        timestamp = int(evt.creation_timestamp * 1e3)
        event_type: MarketEvent = self.market_event_tag_map[event_tag]

        if coalesce_snapshot and evidence is None:
            evidence = self._read_order_event_evidence(session, evt.order_id, timestamp, event_type.name)
        order_record = evidence[0] if evidence is not None else session.get(Order, evt.order_id)
        config_file_path, strategy_name = self._event_configuration(session, market, order_record)
        if order_record is not None and (
            order_record.market != market.display_name or order_record.symbol != evt.trading_pair
            or order_record.config_file_path != config_file_path
        ):
            raise ValueError("Order identity conflicts with an existing recorded order.")
        values = dict(
            id=evt.order_id,
            config_file_path=config_file_path,
            strategy=strategy_name,
            market=market.display_name,
            symbol=evt.trading_pair,
            base_asset=base_asset,
            quote_asset=quote_asset,
            creation_timestamp=timestamp,
            order_type=evt.type.name,
            amount=Decimal(evt.amount),
            leverage=evt.leverage if evt.leverage else 1,
            price=Decimal(evt.price) if evt.price == evt.price else Decimal(0),
            position=evt.position if evt.position else PositionAction.NIL.value,
            last_status=event_type.name,
            last_update_timestamp=timestamp,
            exchange_order_id=evt.exchange_order_id,
            creation_pending=False,
        )
        if order_record is None:
            order_record = Order(**values)
            session.add(order_record)
        else:
            # A create ack completes a provisional record without erasing fills
            # or regressing a newer/terminal status. Duplicate create is a no-op.
            was_pending = order_record.creation_pending
            for key, value in values.items():
                if key in ("last_status", "last_update_timestamp"):
                    continue
                if was_pending or key in ("exchange_order_id", "creation_pending"):
                    setattr(order_record, key, value)
            if was_pending and order_record.last_status in ("PendingBuy", "PendingSell"):
                order_record.last_status = event_type.name
            if order_record.last_status in (MarketEvent.BuyOrderCreated.name, MarketEvent.SellOrderCreated.name):
                order_record.last_update_timestamp = max(order_record.last_update_timestamp, timestamp)
        # These lookups need only the persisted attempt and history. An
        # isolated worker ACK can overlay its own change on the fresh snapshot.
        with session.no_autoflush:
            if evidence is None:
                self._attempt_observation(session, evt.order_id, 'acknowledged')
                self._add_order_status(session, order_record, timestamp, event_type.name)
            else:
                _, attempt, history_recorded = evidence
                self._set_attempt_observation(attempt, 'acknowledged')
                if not history_recorded:
                    session.add(OrderStatus(order=order_record, timestamp=timestamp, status=event_type.name))
            if coalesce_snapshot:
                if not defer_snapshot:
                    self.save_market_states(config_file_path, market, session=session, tracking_states=tracking_states,
                                            new_order_id=evt.order_id, staged_orders=(order_record,))
                return order_record
        self.save_market_states(config_file_path, market, session=session, tracking_states=tracking_states)

    def _read_order_event_evidence(self, session, identity, timestamp, status, *, include_pending_intent=False,
                                   tracking=None, market_name=None):
        from hummingbot.model.runtime_journal import SubmissionAttempt

        history_recorded = select(OrderStatus.id).filter_by(order_id=identity, timestamp=timestamp, status=status).exists()
        statement = select(Order, SubmissionAttempt, history_recorded).options(
            load_only(SubmissionAttempt.state)).outerjoin(
            SubmissionAttempt, SubmissionAttempt.order_id == Order.id
        ).where(Order.id == identity)
        if include_pending_intent:
            intent_recorded = select(OrderStatus.id).where(
                OrderStatus.order_id == identity, OrderStatus.status.in_(("PendingBuy", "PendingSell"))).exists()
            statement = statement.add_columns(case((Order.creation_pending, intent_recorded), else_=False))
        if tracking is not None:
            statement = self._with_tracking_basis(statement, tracking, market_name)
        return session.execute(statement).one_or_none()

    def _with_tracking_basis(self, statement, tracking, market_name):
        orders = self._tracking_order_ids(self._config_file_path, market_name, tracking)
        return (statement.add_columns(MarketState, orders.c.admitted_ids, orders.c.pending_ids)
                .join(orders, true()).outerjoin(MarketState, and_(
                    MarketState.config_file_path == self._config_file_path, MarketState.market == market_name))
                .options(defer(MarketState.saved_state)))

    def _read_creation_evidence_batch(self, session, acknowledgements, *, tracking, market_name):
        from hummingbot.model.runtime_journal import SubmissionAttempt

        requested = values(column('order_id', Order.id.type), column('timestamp', OrderStatus.timestamp.type),
                           column('status', OrderStatus.status.type), name='requested_acks').data(acknowledgements)
        history_recorded = select(OrderStatus.id).where(
            OrderStatus.order_id == Order.id, OrderStatus.timestamp == requested.c.timestamp,
            OrderStatus.status == requested.c.status).exists()
        statement = (select(Order, SubmissionAttempt, history_recorded).options(load_only(SubmissionAttempt.state))
                     .select_from(requested).outerjoin(Order, Order.id == requested.c.order_id)
                     .outerjoin(SubmissionAttempt, SubmissionAttempt.order_id == Order.id))
        if tracking is not None:
            # Every ACK shares one checkpoint basis. Defer the previous JSON
            # so only a missing unresolved order needs to load it.
            statement = self._with_tracking_basis(statement, tracking, market_name)
        rows = session.execute(statement).all()
        tracking_basis = rows[0][3:] if tracking is not None else None
        return {row[0].id: row[:3] for row in rows if row[0] is not None}, tracking_basis

    def finish_recorded_order_creation(self, market, evt):
        if evt.exchange_order_id and self._uses_reconstruction_history(market):
            market.add_exchange_order_ids_from_market_recorder({evt.exchange_order_id: evt.order_id})

    @staticmethod
    def _add_order_status(session: Session, order: Order, timestamp: int, status: str) -> None:
        exists = session.query(OrderStatus.id).filter_by(order_id=order.id, timestamp=timestamp, status=status).first()
        if exists is None:
            session.add(OrderStatus(order=order, timestamp=timestamp, status=status))

    def _attempt_observation(self, session, identity, state):
        if self.durable_position_checkpoints:
            from hummingbot.model.runtime_journal import SubmissionAttempt
            self._set_attempt_observation(session.get(SubmissionAttempt, identity), state)

    @staticmethod
    def _set_attempt_observation(attempt, state):
        if (attempt is not None and attempt.state != 'uncertain'
                and not (state == 'acknowledged' and attempt.state in ('terminal', 'rejected'))):
            attempt.state = state

    @staticmethod
    def _record_latest_status(order: Order, status: str, timestamp: int) -> None:
        terminal = {MarketEvent.OrderCancelled.name, MarketEvent.OrderFailure.name,
                    MarketEvent.OrderExpired.name, MarketEvent.BuyOrderCompleted.name, MarketEvent.SellOrderCompleted.name}
        if timestamp >= order.last_update_timestamp and not (order.last_status in terminal and status not in terminal):
            order.last_status = status
            order.last_update_timestamp = timestamp

    @staticmethod
    def _same_trade_fee(recorded, incoming):
        if recorded == incoming:
            return True
        # Keep the raw payload on disk. Reuse fee models for exact Decimal
        # comparison; unknown payload shapes retain the original strict policy.
        fields = {'fee_type', 'percent', 'percent_token', 'flat_fees'}
        try:
            if set(recorded) != fields or set(incoming) != fields:
                return False
            if any(set(token) != {'token', 'amount'}
                   for fee in (recorded, incoming) for token in fee['flat_fees']):
                return False
            fees = [TradeFeeBase.from_json(fee) for fee in (recorded, incoming)]
            return (all(fee.percent.is_finite() and all(token.amount.is_finite() for token in fee.flat_fees)
                        for fee in fees) and fees[0] == fees[1])
        except (KeyError, TypeError, ValueError, ArithmeticError):
            return False

    def _did_fill_order(self,
                        event_tag: int,
                        market: ConnectorBase,
                        evt: OrderFilledEvent):
        if threading.current_thread() != threading.main_thread():
            self._ev_loop.call_soon_threadsafe(self._did_fill_order, event_tag, market, evt)
            return

        if evt is self._published_order_event:
            return
        with self._sql_manager.get_new_session() as session, session.begin():
            committed = self._stage_fill(session, event_tag, market, evt)
        self.finish_recorded_fill(market, evt, committed)

    async def persist_fill(self, market, evt, tracking_states):
        return await self._run_native_write(
            self._stage_fill, MarketEvent.OrderFilled.value, market, evt, self._fill_quote_fee(evt), tracking_states,
        )

    def _stage_fill(self, session, event_tag, market, evt, fee_in_quote=_QUOTE_FEE_NOT_CALCULATED, tracking_states=None):
        base_asset, quote_asset = evt.trading_pair.split("-")
        timestamp: int = int(evt.timestamp * 1e3) if evt.timestamp is not None else self.db_timestamp
        event_type: MarketEvent = self.market_event_tag_map[event_tag]
        order_id: str = evt.order_id

        # Try to find the order record, and update it if necessary.
        order_record: Optional[Order] = session.query(Order).filter(Order.id == order_id).one_or_none()
        config_file_path, _ = self._event_configuration(session, market, order_record)
        existing_fill = session.get(TradeFill, (market.display_name, order_id, evt.exchange_trade_id))
        if existing_fill is not None:
            if (existing_fill.symbol != evt.trading_pair or existing_fill.price != evt.price
                    or existing_fill.amount != evt.amount or existing_fill.trade_type != evt.trade_type.name
                    or not self._same_trade_fee(existing_fill.trade_fee, evt.trade_fee.to_json())):
                raise ValueError("Duplicate fill identity has conflicting economic values.")
            self.save_market_states(config_file_path, market, session=session, tracking_states=tracking_states)
        else:
            if fee_in_quote is _QUOTE_FEE_NOT_CALCULATED:
                fee_in_quote = self._fill_quote_fee(evt)
            existing_fill = self._store_fill(session, market, evt, order_record, timestamp, event_type.name,
                                             base_asset, quote_asset, fee_in_quote, tracking_states=tracking_states)
        committed = {key: getattr(existing_fill, key) for key in (
            "market", "symbol", "timestamp", "order_id", "exchange_trade_id", "trade_type", "price", "amount", "trade_fee")}
        committed['late_held_fill'] = getattr(existing_fill, 'runtime_late_application', False)
        return committed

    def finish_recorded_fill(self, market, evt, committed):
        # A failed commit must never acknowledge persistence to connector reconciliation.
        from hummingbot.core.order_submission_guard import fill_committed
        fill_committed(committed)
        if self._uses_reconstruction_history(market):
            market.add_trade_fills_from_market_recorder({
                TradeFillOrderDetails(market.display_name, evt.exchange_trade_id, evt.trading_pair)
            })

    def _fill_quote_fee(self, evt):
        try:
            fee = evt.trade_fee.fee_amount_in_token(
                trading_pair=evt.trading_pair, price=evt.price, order_amount=evt.amount,
                token=evt.trading_pair.split("-")[1],
            )
            fee = Decimal(str(fee))
            if not fee.is_finite():
                raise ValueError("Non-finite fee conversion")
            return fee
        except Exception as error:
            self.logger().warning("Quote-fee conversion unavailable; original fees are preserved type=%s", type(error).__name__)
            return None

    def _store_fill(self, session: Session, market: ConnectorBase, evt: OrderFilledEvent,
                    order_record: Optional[Order], timestamp: int, status: str, base_asset: str, quote_asset: str,
                    fee_in_quote, *, tracking_states=None):
        config_file_path, strategy_name = self._event_configuration(session, market, order_record)
        if order_record is None:
            # Preserve FKs even when exchange fill updates precede the create ack.
            # Quantity/price here are provisional until the original create arrives.
            order_record = Order(id=evt.order_id, config_file_path=self._config_file_path,
                                 strategy=self._strategy_name, market=market.display_name,
                                 symbol=evt.trading_pair, base_asset=base_asset, quote_asset=quote_asset,
                                 creation_timestamp=timestamp, order_type=evt.order_type.name,
                                 amount=evt.amount, price=evt.price, leverage=evt.leverage or 1,
                                 position=evt.position or PositionAction.NIL.value,
                                 last_status=status, last_update_timestamp=timestamp, creation_pending=True)
            session.add(order_record)
        elif (order_record.market != market.display_name or order_record.symbol != evt.trading_pair
              or order_record.config_file_path != config_file_path):
            raise ValueError("Fill identity conflicts with its recorded order.")
        self._record_latest_status(order_record, status, timestamp)
        # The status lookup does not depend on the pending Order update. Let
        # the checkpoint query flush Order/status/fill together, with the usual
        # ownership fence, rather than first flushing Order alone for this read.
        with session.no_autoflush:
            self._add_order_status(session, order_record, timestamp, status)
        trade_fill_record: TradeFill = TradeFill(
            config_file_path=config_file_path,
            strategy=strategy_name,
            market=market.display_name,
            symbol=evt.trading_pair,
            base_asset=base_asset,
            quote_asset=quote_asset,
            timestamp=timestamp,
            order=order_record,
            trade_type=evt.trade_type.name,
            order_type=evt.order_type.name,
            price=evt.price,
            amount=evt.amount,
            leverage=evt.leverage if evt.leverage else 1,
            trade_fee=evt.trade_fee.to_json(),
            trade_fee_in_quote=fee_in_quote,
            exchange_trade_id=evt.exchange_trade_id,
            position=evt.position if evt.position else PositionAction.NIL.value,
        )
        session.add(trade_fill_record)
        if self.durable_position_checkpoints:
            from hummingbot.model.runtime_journal import journal_fill
            trade_fill_record.runtime_late_application = journal_fill(session, trade_fill_record)
        if order_record.cleanup_operation_id is not None:
            from hummingbot.model.cleanup_positions import apply_cleanup_fill
            apply_cleanup_fill(session, order_record, trade_fill_record)
        self.save_market_states(config_file_path, market, session=session, tracking_states=tracking_states)
        return trade_fill_record

    def _did_complete_funding_payment(self,
                                      event_tag: int,
                                      market: ConnectorBase,
                                      evt: FundingPaymentCompletedEvent):
        if threading.current_thread() != threading.main_thread():
            self._ev_loop.call_soon_threadsafe(self._did_complete_funding_payment, event_tag, market, evt)
            return

        if getattr(market, "funding_bills_managed", False) is True:
            # The owner service persists identified bills, including idle/backfill
            # periods. A legacy event must not count that same payment twice.
            return

        timestamp: float = evt.timestamp

        with self._sql_manager.get_new_session() as session:
            with session.begin():
                # Try to find the funding payment has been recorded already.
                payment_record: Optional[FundingPayment] = session.query(FundingPayment).filter(
                    FundingPayment.timestamp == timestamp,
                    FundingPayment.market == market.display_name,
                    FundingPayment.symbol == evt.trading_pair).one_or_none()
                if payment_record is None:
                    funding_payment_record: FundingPayment = FundingPayment(timestamp=timestamp,
                                                                            config_file_path=self.config_file_path,
                                                                            market=market.display_name,
                                                                            rate=evt.funding_rate,
                                                                            symbol=evt.trading_pair,
                                                                            amount=float(evt.amount))
                    session.add(funding_payment_record)

    @staticmethod
    def _csv_matches_header(file_path: str, header: tuple) -> bool:
        df = pd.read_csv(file_path, header=None)
        return tuple(df.iloc[0].values) == header

    def append_to_csv(self, trade: TradeFill):
        if self._sql_manager.engine.dialect.name == "postgresql":
            raise RuntimeError("PostgreSQL runtime does not export recorder files; use an explicit operator export.")
        csv_filename = "trades_" + trade.config_file_path[:-4] + ".csv"
        csv_path = os.path.join(data_path(), csv_filename)

        field_names = tuple(trade.attribute_names_for_file_export())
        field_data = tuple(getattr(trade, attr) for attr in field_names)

        # adding extra field "age"
        # // indicates order is a paper order so 'n/a'. For real orders, calculate age.
        age = pd.Timestamp(int((trade.timestamp * 1e-3) - (trade.order.creation_timestamp * 1e-3)), unit='s').strftime(
            '%H:%M:%S') if (trade.order is not None and "//" not in trade.order_id) else "n/a"
        field_names += ("age",)
        field_data += (age,)

        if (os.path.exists(csv_path) and (not self._csv_matches_header(csv_path, field_names))):
            move(csv_path, csv_path[:-4] + '_old_' + pd.Timestamp.utcnow().strftime("%Y%m%d-%H%M%S") + ".csv")

        if not os.path.exists(csv_path):
            df_header = pd.DataFrame([field_names])
            df_header.to_csv(csv_path, mode='a', header=False, index=False)
        df = pd.DataFrame([field_data])
        df.to_csv(csv_path, mode='a', header=False, index=False)

    def _update_order_status(self,
                             event_tag: int,
                             market: ConnectorBase,
                             evt: Union[OrderCancelledEvent,
                                        MarketOrderFailureEvent,
                                        BuyOrderCompletedEvent,
                                        SellOrderCompletedEvent,
                                        OrderExpiredEvent]):
        if threading.current_thread() != threading.main_thread():
            self._ev_loop.call_soon_threadsafe(self._update_order_status, event_tag, market, evt)
            return

        if evt is self._published_order_event:
            return
        with self._sql_manager.get_new_session() as session, session.begin():
            self._stage_order_status(session, event_tag, market, evt)

    async def persist_order_update(self, market, events, tracking_states, *, defer_ack_contention=False):
        def stage(session):
            for tag, payload in events:
                if tag in (MarketEvent.BuyOrderCreated, MarketEvent.SellOrderCreated):
                    self._stage_order_creation(session, tag.value, market, payload, tracking_states=tracking_states,
                                               coalesce_snapshot=len(events) == 1)
                else:
                    self._stage_order_status(session, tag.value, market, payload, tracking_states=tracking_states,
                                             coalesce_snapshot=len(events) == 1)

        _, cancelled = await self._run_native_write(
            stage, defer_contention=OrderAcknowledgementBusy if defer_ack_contention else None)
        return cancelled

    @trace_runtime('order.terminal', order_parameter='evt')
    def _stage_order_status(self, session, event_tag, market, evt, *, tracking_states=None, coalesce_snapshot=False):
        timestamp: int = self.db_timestamp
        event_type: MarketEvent = self.market_event_tag_map[event_tag]
        order_id: str = evt.order_id
        exchange_id = getattr(evt, "exchange_order_id", None)
        has_cancel_identity = event_type == MarketEvent.OrderCancelled and isinstance(exchange_id, str) and bool(exchange_id)

        evidence = None
        tracking_basis = None
        if self.durable_position_checkpoints:
            coalesce_snapshot = coalesce_snapshot and self.cleanup_operation_id is None
            if coalesce_snapshot and tracking_states is None:
                tracking_states = market.tracking_states
            evidence = self._read_order_event_evidence(session, order_id, timestamp, event_type.name,
                                                       include_pending_intent=has_cancel_identity,
                                                       tracking=tracking_states if coalesce_snapshot else None,
                                                       market_name=market.display_name)
            order_record = evidence[0] if evidence is not None else None
            if evidence is not None and coalesce_snapshot:
                tracking_basis = evidence[-3:]
        else:
            order_record = session.query(Order).filter(Order.id == order_id).one_or_none()

        if order_record is not None:
            config_file_path, _ = self._event_configuration(session, market, order_record)
            # A private cancel can arrive before the create ACK. Its
            # exchange ID proves admission; a not-found cancel without
            # that identity must remain unresolved.
            if has_cancel_identity:
                if (order_record.market != market.display_name or order_record.config_file_path != config_file_path
                        or (order_record.exchange_order_id and order_record.exchange_order_id != exchange_id)):
                    raise ValueError("Cancel acknowledgment conflicts with the recorded order identity.")
                # A fill-only placeholder does not yet contain the
                # original order quantity. Keep it provisional until
                # Created arrives; managed intents already have it.
                original_parameters_known = not order_record.creation_pending
                if not original_parameters_known:
                    original_parameters_known = evidence[3] if evidence is not None else session.query(OrderStatus.id).filter(
                        OrderStatus.order_id == order_id, OrderStatus.status.in_(("PendingBuy", "PendingSell"))).first() is not None
                if original_parameters_known:
                    order_record.exchange_order_id = exchange_id
                    order_record.creation_pending = False
            self._record_latest_status(order_record, event_type.name, timestamp)
            with session.no_autoflush:
                if evidence is None:
                    self._add_order_status(session, order_record, timestamp, event_type.name)
                else:
                    _, attempt, history_recorded = evidence[:3]
                    self._set_attempt_observation(attempt, 'terminal')
                    if not history_recorded:
                        session.add(OrderStatus(order=order_record, timestamp=timestamp, status=event_type.name))
                if tracking_basis is not None:
                    # The read saw the order before this update. Exclude its
                    # old pending snapshot only if the update resolved it.
                    self.save_market_states(config_file_path, market, session=session, tracking_states=tracking_states,
                                            staged_orders=(order_record,), tracking_basis=tracking_basis)
            if tracking_basis is None:
                self.save_market_states(config_file_path, market, session=session, tracking_states=tracking_states)
        elif self._sql_manager.engine.dialect.name == "postgresql" and event_type != MarketEvent.OrderFailure:
            raise RuntimeError("Order status arrived without a persisted order identity; reconciliation is required.")

    def _did_cancel_order(self,
                          event_tag: int,
                          market: ConnectorBase,
                          evt: OrderCancelledEvent):
        self._update_order_status(event_tag, market, evt)

    def _did_fail_order(self,
                        event_tag: int,
                        market: ConnectorBase,
                        evt: MarketOrderFailureEvent):
        self._update_order_status(event_tag, market, evt)

    def _did_complete_order(self,
                            event_tag: int,
                            market: ConnectorBase,
                            evt: Union[BuyOrderCompletedEvent, SellOrderCompletedEvent]):
        self._update_order_status(event_tag, market, evt)

    def _did_expire_order(self,
                          event_tag: int,
                          market: ConnectorBase,
                          evt: OrderExpiredEvent):
        self._update_order_status(event_tag, market, evt)

    def _did_update_range_position(self,
                                   event_tag: int,
                                   connector: ConnectorBase,
                                   evt: Union[RangePositionLiquidityAddedEvent, RangePositionLiquidityRemovedEvent]):
        if threading.current_thread() != threading.main_thread():
            self._ev_loop.call_soon_threadsafe(self._did_update_range_position, event_tag, connector, evt)
            return

        timestamp: int = self.db_timestamp
        event_type: MarketEvent = self.market_event_tag_map[event_tag]

        # Determine order_action based on event type
        order_action = None
        if event_type == MarketEvent.RangePositionLiquidityAdded:
            order_action = "ADD"
        elif event_type == MarketEvent.RangePositionLiquidityRemoved:
            order_action = "REMOVE"

        # Calculate trade_fee_in_quote similar to _did_fill_order
        trading_pair = getattr(evt, 'trading_pair', None)
        mid_price = Decimal(str(getattr(evt, 'mid_price', 0) or 0))
        base_amount = Decimal(str(getattr(evt, 'base_amount', 0) or 0))
        fee_in_quote = Decimal("0")
        if trading_pair:
            _, quote_asset = trading_pair.split("-")
            try:
                fee_in_quote = evt.trade_fee.fee_amount_in_token(
                    trading_pair=trading_pair,
                    price=mid_price,
                    order_amount=base_amount,
                    token=quote_asset,
                )
            except Exception as e:
                self.logger().error(f"Error calculating fee in quote for LP position: {e}, will be stored as 0.")
                fee_in_quote = Decimal("0")

        with self._sql_manager.get_new_session() as session:
            with session.begin():
                rp_update: RangePositionUpdate = RangePositionUpdate(
                    hb_id=evt.order_id,
                    timestamp=timestamp,
                    tx_hash=evt.exchange_order_id,
                    token_id=getattr(evt, 'token_id', 0) or 0,
                    trade_fee=evt.trade_fee.to_json(),
                    trade_fee_in_quote=float(fee_in_quote),
                    # P&L tracking fields
                    config_file_path=self._config_file_path,
                    market=connector.display_name,
                    order_action=order_action,
                    trading_pair=trading_pair,
                    position_address=getattr(evt, 'position_address', None),
                    lower_price=float(getattr(evt, 'lower_price', 0) or 0),
                    upper_price=float(getattr(evt, 'upper_price', 0) or 0),
                    mid_price=float(mid_price),
                    base_amount=float(base_amount),
                    quote_amount=float(getattr(evt, 'quote_amount', 0) or 0),
                    base_fee=float(getattr(evt, 'base_fee', 0) or 0),
                    quote_fee=float(getattr(evt, 'quote_fee', 0) or 0),
                    # Rent tracking: position_rent on ADD, position_rent_refunded on REMOVE
                    position_rent=float(getattr(evt, 'position_rent', 0) or 0),
                    position_rent_refunded=float(getattr(evt, 'position_rent_refunded', 0) or 0),
                )
                session.add(rp_update)
                self.save_market_states(self._config_file_path, connector, session=session)

    @staticmethod
    async def _sleep(delay):
        """
        A wrapper function that facilitates patching the sleep in unit tests without affecting the asyncio module
        """
        await asyncio.sleep(delay)
