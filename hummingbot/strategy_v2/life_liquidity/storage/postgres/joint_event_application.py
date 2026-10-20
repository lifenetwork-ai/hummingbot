"""Both owned SDK listeners -> signed joint money -> qualified SDK duplicate IDs.

Callbacks remain untrusted hints. No tracking state, sender or new-risk grant is
restored. A stopped application joins original accounting without publishing IDs.
"""
from uuid import uuid4

from hummingbot.connector.markets_recorder import MarketsRecorder
from hummingbot.core.event.event_forwarder import SourceInfoEventForwarder
from hummingbot.core.event.events import MarketEvent
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
from hummingbot.strategy_v2.life_liquidity.storage.postgres.event_application import PgSpotEventApplication
from hummingbot.strategy_v2.life_liquidity.storage.postgres.joint_dedup import PgJointDedupRecovery
from hummingbot.strategy_v2.life_liquidity.storage.postgres.joint_observer import PgJointFinancialObserver
from hummingbot.strategy_v2.life_liquidity.storage.recorder_events import EventProjection


class PgJointEventApplication(PgSpotEventApplication):
    def __init__(self, reporting, observer):
        from hummingbot.strategy_v2.life_liquidity.storage.postgres.recorder_application import PgRecorderApplication
        if (type(reporting) is not PgRecorderApplication or type(observer) is not PgJointFinancialObserver
                or reporting.financial is not observer.financial or reporting.owner != observer.owner
                or reporting._event_application is not None or not reporting.ready or reporting._revoked
                or not reporting.controller.config.strategy.perpetual.enabled
                or tuple(r.source.market for r in reporting.events) != ('okx', 'okx_perpetual')
                or observer.reader.source.journal.controller_id != reporting.controller.config.id):
            raise StorageError('PG_JOINT_EVENT_CONTEXT_INVALID')
        self.reporting, self.observer = reporting, observer
        self.runtime, self.reader = observer.runtime, observer.reader
        self.financial, self.owner, self.account_source = observer.financial, observer.owner, self.reader.source
        self._controller_json = reporting.controller.config.model_dump_json()
        self._repositories = reporting.events
        self._sources = tuple(r.source for r in reporting.events)
        self.connectors = (self.reader.source._connector, observer.source.swap_source._connector)
        self._history = PgJointDedupRecovery(observer, reporting.events, local_idle=self._idle)
        self._pin = (reporting, observer, self.runtime, self.reader, self.financial, self.owner, self.account_source, self.connectors, self._history)
        self._closed, self._started, self._failed = False, False, False
        self._epoch, self._callbacks = 0, 0
        self._task, self._operation_id = None, None
        self._forwarder = SourceInfoEventForwarder(self.capture)
        self._registered = []

    def _tags(self, connector):
        return self.TAGS + ((MarketEvent.FundingPaymentCompleted,) if connector is self.connectors[1] else ())

    @classmethod
    async def install(cls, reporting, observer):
        app = cls(reporting, observer)
        try:
            app._exclusive_listeners()
            reporting._event_application = app
            # Register both before any awaited initial account/history read.
            for connector in app.connectors:
                for tag in app._tags(connector):
                    app._registered.append((connector, tag))
                    connector.add_listener(tag, app._forwarder)
            app._start()
            await app.drain()
            if app._closed or app._failed or reporting._closed or not reporting.ready:
                raise StorageError('PG_JOINT_EVENT_INSTALL_REVOKED')
            return app
        except BaseException:
            app.stop()
            reporting.invalidate()
            raise

    def _exclusive_listeners(self):
        for connector in self.connectors:
            for tag in self._tags(connector):
                for listener in connector.get_listeners(tag):
                    target = getattr(getattr(listener, '_to_function', None), '__self__', None)
                    if target is not self and isinstance(target, (MarketsRecorder, PgSpotEventApplication)):
                        raise StorageError('PG_EVENT_APPLICATION_EXCLUSIVE_LISTENERS_REQUIRED')

    def _context(self):
        if (self._pin != (self.reporting, self.observer, self.runtime, self.reader, self.financial,
                          self.owner, self.account_source, self.connectors, self._history)
                or self.reporting._closed or self.reporting._revoked or self._failed
                or self.reporting._event_application is not self
                or self.reporting.financial is not self.financial or self.financial._runtime is not self.runtime
                or self.reporting.owner != self.owner or self.observer.owner != self.owner
                or self.observer.reader is not self.reader or self.reader.source is not self.account_source
                or self.reporting.events != self._repositories
                or tuple(r.source for r in self.reporting.events) != self._sources
                or self.reporting.controller.config.model_dump_json() != self._controller_json
                or self.connectors != (self.reader.source._connector, self.observer.source.swap_source._connector)):
            raise StorageError('PG_JOINT_EVENT_CONTEXT_CHANGED')
        self._history._original()
        self._exclusive_listeners()

    def _idle(self):
        return (not self._closed and not self._failed and self.reporting._healthy
                and not self.reporting._closed and not self.reporting._revoked
                and not self.reporting._pending and not self.reporting._busy)

    def runner_sources(self):
        self._context()
        expected = tuple((c, t) for c in self.connectors for t in self._tags(c))
        if (self._closed or not self._started or self._callbacks or not self.runtime.healthy
                or self._task is None or not self._task.done() or not self.account_source._uid_fresh()
                or tuple(self._registered) != expected
                or any(c.get_listeners(t).count(self._forwarder) != 1 for c, t in expected)):
            raise StorageError('PG_JOINT_EVENT_RUNNER_UNQUALIFIED')
        return tuple(zip(self.connectors, self._sources))

    def capture(self, tag, connector, event):
        if self._closed:
            return
        self.runtime._ingress_epoch += 1
        try:
            self._context()
            matches = [i for i, c in enumerate(self.connectors) if c is connector]
            if len(matches) != 1 or MarketEvent(tag) not in self._tags(connector):
                raise StorageError('PG_JOINT_EVENT_SOURCE_INVALID')
            self._callbacks += 1
            if self._callbacks > self.reporting.max_callbacks:
                raise StorageError('PG_EVENT_APPLICATION_BACKPRESSURE')
            command = EventProjection.capture(self.observer.binding, self._sources[matches[0]], tag, event, connector.tracking_states)
            self.reporting.enqueue_event(command)
            self._epoch += 1
            self._start()
        except BaseException:
            self._failed = True
            self._pin[2]._healthy = False
            self.reporting.invalidate()
            raise

    async def _pump(self):
        try:
            while True:
                self._context()
                epoch = self._epoch
                await self.reporting.drain()
                self._context()
                # Persist only signed original monetary truth. The original ID
                # remains available for diagnostics if its COMMIT is unknown.
                self._operation_id = 'sdk-joint:' + uuid4().hex
                async with self.reporting._mutex:
                    await self.observer.observe(self._operation_id)
                self._context()
                if self._closed:
                    return
                if epoch != self._epoch:
                    continue
                async with self.reporting._mutex:
                    await self._history.restore()
                self._context()
                if epoch == self._epoch:
                    self._callbacks = 0
                    self._started = True
                    return
        except BaseException:
            self._failed = True
            self._pin[2]._healthy = False
            self.reporting.invalidate()
            raise
        finally:
            self.runtime._observation_inflight -= 1

    def stop(self):
        if self._closed:
            return
        self._closed = True
        self._pin[2]._healthy = False
        for connector, tag in self._registered:
            connector.remove_listener(tag, self._forwarder)
        self._registered.clear()
