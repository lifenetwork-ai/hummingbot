"""Explicit native quote execution through the actual OrderExecutor/OKX SDK.

Only this process's original runner claims can submit once. Prepared/armed SQL
readback never reconstructs an HTTP capability. Configured market provenance,
joint scope and service installation require their separate source assembly.
"""
import asyncio

from hummingbot.core.data_type.common import OrderType, TradeType
from hummingbot.core.event.event_forwarder import EventForwarder
from hummingbot.core.event.events import MarketEvent, MarketOrderFailureEvent
from hummingbot.strategy_v2.life_liquidity.storage.action_preparation import PrepareQuoteAction
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError, fingerprint
from hummingbot.strategy_v2.life_liquidity.storage.postgres.quote_account_source import PgQuoteAccountSource
from hummingbot.strategy_v2.models.executor_actions import CreateExecutorAction


class PgSpotQuoteApplication:
    def __init__(self, source, *, preparation_source, max_pending, operation_timeout_ms, shutdown_timeout_ms):
        if (type(source) is not PgQuoteAccountSource or not callable(preparation_source)
                or type(max_pending) is not int or not 0 < max_pending <= 64
                or any(type(v) is not int or not 0 < v <= 60000 for v in (operation_timeout_ms, shutdown_timeout_ms))):
            raise StorageError('PG_SPOT_APPLICATION_CONFIG_INVALID')
        journal = source.journal
        if (journal._entries or journal._transport is not None or not journal._ready()
                or not journal.repository._runtime.healthy or max_pending > source.max_pending
                or 'LIFE-USDT' not in source._connector._protected_trading_pairs):
            raise StorageError('PG_SPOT_APPLICATION_BINDING_INVALID')
        source._credentials()
        self.source, self.journal, self.connector = source, journal, source._connector
        self.repository, self.runtime = journal.repository, journal.repository._runtime
        self._prepare = preparation_source
        self._context = (source, journal, self.connector, self.repository, self.runtime, journal.binding,
                         journal.owner, preparation_source, source._admission_source,
                         journal.runner_facts, journal.local_ready, journal.clock, journal.controller_id, journal.config_version,
                         source._market_source)
        self.max_pending, self.operation_timeout_ms, self.shutdown_timeout_ms = max_pending, operation_timeout_ms, shutdown_timeout_ms
        self._started, self._pending, self._waiting = set(), {}, {}
        self._physical = {}
        self._guards = {}
        self._preparing = {}
        self._stopped, self._failed = False, False
        journal._transport = self

    @property
    def pending_count(self):
        return len(self._pending)

    def _original(self):
        source, journal, connector, repository, runtime, binding, owner, prepare, admission, facts, ready, clock, controller, version, market = self._context
        if (self.source is not source or self.journal is not journal or self.connector is not connector
                or self.repository is not repository or self.runtime is not runtime
                or source.journal is not journal or source._connector is not connector
                or journal.repository is not repository or repository._runtime is not runtime
                or journal.binding != binding or journal.owner != owner or journal._transport is not self
                or self._prepare is not prepare or source._admission_source is not admission
                or journal.runner_facts is not facts or journal.local_ready is not ready or journal.clock is not clock
                or journal.controller_id != controller or journal.config_version != version or source._market_source is not market):
            self.stop()
            raise StorageError('PG_SPOT_APPLICATION_CONTEXT_CHANGED')
        source._credentials()

    def _ready(self, action):
        self._original()
        if self._stopped or not self.runtime.healthy or not self.journal.local_runner_create_fence(action):
            raise StorageError('PG_SPOT_APPLICATION_STOPPED')

    def dispatch_finished(self, action):
        event = self._waiting.get(action.executor_config.id)
        if event is not None:
            event.set()

    def submit(self, config, *, amount, price, order_type):
        # Detach mutable executor configuration before scheduling any work.
        action = CreateExecutorAction(controller_id=self.journal.controller_id, executor_config=config.model_copy(deep=True))
        entry = self.journal._entry(action)
        if entry.row.intent_id in self._started:
            raise StorageError('PG_SPOT_SUBMISSION_ALREADY_STARTED')
        self._ready(action)
        if (order_type is not OrderType.LIMIT_MAKER or amount != action.executor_config.amount
                or price != action.executor_config.price or entry.prepare_started
                or self.pending_count >= self.max_pending):
            raise StorageError('PG_SPOT_SUBMISSION_SCOPE_OR_CAPACITY')
        wire = (self.source.wire_for_action(action) if self.source._market_source is not None
                else 'L' + fingerprint(entry.row.json())[:31])
        if wire in self.connector._order_tracker.all_orders:
            raise StorageError('PG_SPOT_SDK_WIRE_ALREADY_PRESENT')
        # Permanent process-local identity consumption precedes the first await.
        # A later failure cannot authorize a retry under the same wire/claim.
        self._started.add(entry.row.intent_id)
        event = self._waiting[entry.row.intent_id] = asyncio.Event()
        if entry.accepted:
            event.set()
        task = asyncio.create_task(self._run(action, wire, event))
        self._pending[entry.row.intent_id] = task

        def done(result):
            self._pending.pop(entry.row.intent_id, None)
            self._waiting.pop(entry.row.intent_id, None)
            if result.cancelled() or result.exception() is not None:
                self._failed = True
                self.stop()
        task.add_done_callback(done)
        return wire

    def _sdk_check(self, action, wire, body, *, observation_guard=None):
        if observation_guard is None:
            self._ready(action)
        else:
            self._original()
            market = self.source._market_source
            if (self._stopped or not self.journal.local_runner_create_fence(action) or market is None
                    or self._guards.get(action.executor_config.id) is not observation_guard
                    or not market.owns_pending_observation(observation_guard)):
                raise StorageError('PG_SPOT_SDK_OBSERVATION_ORIGIN_REQUIRED')
        cfg = action.executor_config
        tracked = self.connector._order_tracker.active_orders.get(wire)
        if (tracked is None or tracked.client_order_id != wire or tracked.exchange_order_id is not None
                or tracked.trading_pair != cfg.trading_pair or tracked.trade_type is not cfg.side
                or tracked.order_type is not OrderType.LIMIT_MAKER or tracked.amount != cfg.amount
                or tracked.price != cfg.price or body.get('clOrdId') != wire):
            raise StorageError('PG_SPOT_SDK_SCOPE_CHANGED')

    def preparing_action(self):
        """The original accepted runner, during its owned preparation await."""
        task = asyncio.current_task()
        action = self._preparing.get(task)
        if action is None or self._pending.get(action.executor_config.id) is not task:
            raise StorageError('PG_SPOT_PREPARING_ORIGIN_REQUIRED')
        self._original()
        market = self.source._market_source
        if (self._stopped or not self.journal.local_runner_create_fence(action)
                or not self.runtime.healthy and (market is None or not market.owns_preparation_observation(self))):
            raise StorageError('PG_SPOT_PREPARING_ORIGIN_REQUIRED')
        entry = self.journal._entry(action)
        if not entry.accepted or entry.prepare_started or entry.prepared is not None:
            raise StorageError('PG_SPOT_PREPARING_ORIGIN_REQUIRED')
        return action.model_copy(deep=True)

    async def _run(self, action, wire, accepted):
        sdk_started = False
        try:
            async with asyncio.timeout(self.operation_timeout_ms / 1000):
                await accepted.wait()
                self._ready(action)
                entry = self.journal._entry(action)
                if not entry.accepted:
                    raise StorageError('RUNNER_ACCEPTANCE_REQUIRED')
                self._preparing[asyncio.current_task()] = action
                try:
                    preparation = await self._prepare(action.model_copy(deep=True), wire)
                finally:
                    self._preparing.pop(asyncio.current_task(), None)
                self._ready(action)
                if (type(preparation) is not PrepareQuoteAction or preparation.action != entry.row
                        or preparation.prepare.client_order_id != wire):
                    raise StorageError('PG_SPOT_PREPARATION_SCOPE_CHANGED')
                market = self.source._market_source
                source_cut = None if market is None else market.take_preparation_cut(self, preparation)
                source_epoch = self.runtime._ingress_epoch
                receipt = await self.journal.prepare_quote_action(action, preparation, _source_cut=source_cut)
                self._ready(action)
                if market is not None and self.runtime._ingress_epoch != source_epoch:
                    raise StorageError('PG_SPOT_PREPARATION_SOURCE_CHANGED')
                if receipt.replayed or receipt.state != 'PREPARED':
                    raise StorageError('PG_SPOT_PREPARATION_RECOVERY_REQUIRED')
                if wire in self.connector._order_tracker.all_orders:
                    raise StorageError('PG_SPOT_SDK_WIRE_ALREADY_PRESENT')
                guard = self.source.guard(action=action, preparation=preparation)
                self._guards[action.executor_config.id] = guard
                cfg = action.executor_config
                completed = asyncio.get_running_loop().create_future()
                completed.add_done_callback(lambda f: None if f.cancelled() else f.exception())
                physical = asyncio.get_running_loop().create_future()
                self._physical[action.executor_config.id] = physical
                acknowledged = None

                def sdk_done(task):
                    if not physical.done():
                        physical.set_result(None)
                    self._physical.pop(action.executor_config.id, None)
                    self._guards.pop(action.executor_config.id, None)
                    if not completed.done():
                        completed.set_exception(StorageError('PG_SPOT_SDK_SUBMISSION_UNKNOWN'))

                async def ack(exchange_id):
                    nonlocal acknowledged
                    # Late truth survives local stop. The original guard owns
                    # the consumed bytes and native ingress, never a new source.
                    await guard.acknowledge(exchange_id)
                    acknowledged = exchange_id

                def created(event):
                    if event.order_id == wire and not completed.done():
                        if guard._consumed and acknowledged is not None and event.exchange_order_id == acknowledged:
                            completed.set_result(acknowledged)
                        else:
                            completed.set_exception(StorageError('PG_SPOT_SDK_ACK_REQUIRED'))

                def failed(event):
                    if event.order_id == wire and not completed.done():
                        completed.set_exception(StorageError('PG_SPOT_SDK_SUBMISSION_UNKNOWN'))

                tag = MarketEvent.BuyOrderCreated if cfg.side is TradeType.BUY else MarketEvent.SellOrderCreated
                on_created, on_failure = EventForwarder(created), EventForwarder(failed)
                self.connector.add_listener(tag, on_created)
                self.connector.add_listener(MarketEvent.OrderFailure, on_failure)
                try:
                    self._ready(action)
                    returned = self.connector.submit_protected_order(order_id=wire, trading_pair=cfg.trading_pair,
                                                                     amount=cfg.amount, trade_type=cfg.side, order_type=OrderType.LIMIT_MAKER, price=cfg.price,
                                                                     pre_send_check=lambda body: self._sdk_check(action, wire, body),
                                                                     request_send_guard=guard, async_on_ack=ack, on_submission_done=sdk_done)
                    sdk_started = True
                    if returned != wire:
                        raise StorageError('PG_SPOT_SDK_WIRE_CHANGED')
                    await asyncio.shield(completed)
                finally:
                    self.connector.remove_listener(tag, on_created)
                    self.connector.remove_listener(MarketEvent.OrderFailure, on_failure)
                    if not sdk_started:
                        physical.set_result(None)
                        self._physical.pop(action.executor_config.id, None)
                        self._guards.pop(action.executor_config.id, None)
        except BaseException:
            self._failed = True
            self.stop()
            if not sdk_started:
                # No SDK task exists to notify the executor. This hint grants
                # no monetary release or native absence proof.
                self.connector.trigger_event(MarketEvent.OrderFailure,
                                             MarketOrderFailureEvent(self.connector.current_timestamp, wire, OrderType.LIMIT_MAKER))
            raise

    async def drain(self):
        tasks = tuple(self._pending.values())
        if tasks:
            # Cancelling the caller must not cancel owned physical work/ACK.
            results = await asyncio.shield(asyncio.gather(*tasks, return_exceptions=True))
            if any(isinstance(result, BaseException) for result in results):
                self._failed = True
        if self._failed:
            raise StorageError('PG_SPOT_SUBMISSION_UNKNOWN')

    def stop(self):
        if self._stopped:
            return
        self._stopped = True
        self.runtime._healthy = False
        self.runtime._ingress_epoch += 1
        self.source.revoke()
        self.journal.revoke()
        for event in self._waiting.values():
            event.set()

    async def close(self):
        self.stop()
        try:
            async with asyncio.timeout(self.shutdown_timeout_ms / 1000):
                tasks = (*self._pending.values(), *self._physical.values())
                if tasks:
                    await asyncio.shield(asyncio.gather(*tasks, return_exceptions=True))
        except TimeoutError:
            raise StorageError('PG_SPOT_SHUTDOWN_READBACK_REQUIRED') from None
