"""Explicit SPOT connector listeners composed with signed financial collection.

SDK callbacks are untrusted hints/reporting. Only the pinned signed GET observer
can account money, and only native financially qualified reporting can update
SDK dedup IDs. No tracking JSON, order tracker, readiness or send grant restores.
"""
import asyncio

from hummingbot.core.event.event_forwarder import SourceInfoEventForwarder
from hummingbot.core.event.events import MarketEvent
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
from hummingbot.strategy_v2.life_liquidity.storage.postgres.financial_observer import PgSpotFinancialObserver
from hummingbot.strategy_v2.life_liquidity.storage.recorder_events import EventProjection


class PgSpotEventApplication:
    TAGS = (MarketEvent.BuyOrderCreated, MarketEvent.SellOrderCreated, MarketEvent.OrderFilled,
            MarketEvent.OrderCancelled, MarketEvent.OrderFailure, MarketEvent.BuyOrderCompleted,
            MarketEvent.SellOrderCompleted, MarketEvent.OrderExpired)

    def __init__(self, reporting, observer):
        from hummingbot.strategy_v2.life_liquidity.storage.postgres.recorder_application import PgRecorderApplication
        if (type(reporting) is not PgRecorderApplication or type(observer) is not PgSpotFinancialObserver
                or reporting.financial is not observer.repository or reporting.owner != observer.owner
                or reporting._event_application is not None or not reporting.ready or reporting._revoked
                or observer.reader.source.journal.controller_id != reporting.controller.config.id
                or len(reporting.events) != 1 or reporting.events[0].source.market != 'okx'):
            raise StorageError('PG_EVENT_APPLICATION_CONTEXT_INVALID')
        self.reporting, self.observer = reporting, observer
        self.runtime, self.reader = observer.runtime, observer.reader
        self.financial, self.owner, self.account_source = reporting.financial, reporting.owner, self.reader.source
        self._controller_json = reporting.controller.config.model_dump_json()
        self.connector, self.source = self.reader.source._connector, reporting.events[0].source
        self._event_repository = reporting.events[0]
        self._closed, self._started, self._failed = False, False, False
        self._epoch, self._callbacks = 0, 0
        self._task = None
        self._forwarder = SourceInfoEventForwarder(self.capture)
        self._registered = []

    @classmethod
    async def install(cls, reporting, observer):
        app = cls(reporting, observer)
        try:
            app._exclusive_listeners()
            reporting._event_application = app
            # Listen before the first awaited source/history qualification so
            # startup callbacks cannot slip between recovery and registration.
            for tag in app.TAGS:
                app._registered.append(tag)
                app.connector.add_listener(tag, app._forwarder)
            app._start()
            await app.drain()
            if app._closed or app._failed or reporting._closed or not reporting.ready:
                raise StorageError('PG_EVENT_APPLICATION_INSTALL_REVOKED')
            return app
        except BaseException:
            app.stop()
            reporting.invalidate()
            raise

    def _exclusive_listeners(self):
        from hummingbot.connector.markets_recorder import MarketsRecorder

        # A legacy MarketsRecorder may synchronously write SQL/populate SDK IDs
        # from the same callback. This explicit PG assembly cannot coexist with
        # that known second writer, or another copy of this event application.
        for tag in self.TAGS:
            for listener in self.connector.get_listeners(tag):
                target = getattr(getattr(listener, '_to_function', None), '__self__', None)
                if target is not self and isinstance(target, (MarketsRecorder, PgSpotEventApplication)):
                    raise StorageError('PG_EVENT_APPLICATION_EXCLUSIVE_LISTENERS_REQUIRED')

    def _context(self):
        self.reader.source._credentials()
        self._exclusive_listeners()
        if (self._failed or self.reporting._closed or self.reporting._revoked
                or self.reporting._event_application is not self
                or self.reporting.financial is not self.observer.repository
                or self.reporting.financial is not self.financial
                or self.financial._runtime is not self.runtime
                or self.reader.repository is not self.reporting.financial
                or self.reader.runtime is not self.runtime or self.observer.runtime is not self.runtime
                or self.observer.reader is not self.reader
                or self.reader.source is not self.account_source or self.reader.owner != self.owner
                or self.observer.owner != self.owner
                or self.reporting.owner != self.observer.owner
                or self.reader.source.journal.owner != self.observer.owner
                or self.reader.source.journal.repository is not self.financial
                or self.reader.source.journal.binding != self.reader.binding
                or self.reader.source.journal.controller_id != self.source.controller_id
                or self.reporting.controller.config.storage_binding != self.reader.binding
                or self.reporting.controller.config.model_dump_json() != self._controller_json
                or self.reporting.events != (self._event_repository,) or self._event_repository.source != self.source
                or self.reader.source._connector is not self.connector):
            raise StorageError('PG_EVENT_APPLICATION_CONTEXT_CHANGED')

    def runner_sources(self):
        """Synchronous startup qualification, never a restored send grant."""
        self._context()
        if (self._closed or not self._started or self._failed or self._callbacks
                or self._task is None or not self._task.done() or not self.runtime.healthy
                or not self.account_source._uid_fresh()
                or tuple(self._registered) != self.TAGS
                or any(self.connector.get_listeners(tag).count(self._forwarder) != 1 for tag in self.TAGS)):
            raise StorageError('PG_EVENT_RUNNER_STARTUP_UNQUALIFIED')
        return ((self.connector, self.source),)

    def capture(self, tag, connector, event):
        if self._closed:
            return
        # Every callback revokes old captures before any event parsing/await.
        self.runtime._ingress_epoch += 1
        try:
            self._context()
            if connector is not self.connector or MarketEvent(tag) not in self.TAGS:
                raise StorageError('PG_EVENT_APPLICATION_SOURCE_INVALID')
            self._callbacks += 1
            if self._callbacks > self.reporting.max_callbacks:
                raise StorageError('PG_EVENT_APPLICATION_BACKPRESSURE')
            command = EventProjection.capture(self.reader.binding, self.source, tag, event, connector.tracking_states)
            self.reporting.enqueue_event(command)
            self._epoch += 1
            self._start()
        except BaseException:
            self._failed = True
            self.runtime._healthy = False
            self.reporting.invalidate()
            raise

    def _start(self):
        if self._task is not None and not self._task.done():
            return
        self.runtime._observation_inflight += 1
        self.runtime._ingress_epoch += 1
        self._task = asyncio.create_task(self._pump())

        def finished(task):
            if not task.cancelled():
                task.exception()
        self._task.add_done_callback(finished)

    async def _pump(self):
        try:
            while True:
                self._context()
                epoch = self._epoch
                await self.reporting.drain()
                self._context()
                # New reporting callbacks queue while signed account collection
                # runs. They cannot change its native cut halfway through it.
                async with self.reporting._mutex:
                    await self.observer.observe()
                self._context()
                if self._closed:
                    return  # Finish owned accounting; no SDK publication after stop.
                if epoch != self._epoch:
                    continue
                # Mandatory original native financial/report qualification;
                # diagnostic events alone never populate the SDK caches.
                await self.reporting.restore_spot_dedup(self.reader)
                self._context()
                if epoch == self._epoch:
                    self._callbacks = 0
                    self._started = True
                    return
        except BaseException:
            self._failed = True
            self.runtime._healthy = False
            self.reporting.invalidate()
            raise
        finally:
            self.runtime._observation_inflight -= 1

    async def drain(self):
        task = self._task
        if task is not None:
            await asyncio.shield(task)
        if self._failed:
            raise StorageError('PG_EVENT_APPLICATION_REPAIR_REQUIRED')

    def stop(self):
        if self._closed:
            return
        self._closed = True
        self.runtime._healthy = False
        for tag in self._registered:
            self.connector.remove_listener(tag, self._forwarder)
        self._registered.clear()

    async def close(self):
        self.stop()
        # Shield the physical work; timeout/caller cancellation cannot turn an
        # in-flight financial COMMIT into a completed/absent observation.
        try:
            async with asyncio.timeout(self.reporting.shutdown_timeout_s):
                if self._task is not None:
                    await asyncio.shield(self._task)
        except TimeoutError:
            raise StorageError('PG_EVENT_SHUTDOWN_READBACK_REQUIRED') from None
