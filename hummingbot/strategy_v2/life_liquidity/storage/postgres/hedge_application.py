"""One-shot native hedge orchestration. Configured source installation is separate.

The runner must synchronously pause SPOT proposals before cancellation. A cancel
response is not terminal proof: native hedge admission independently requires
reconciled orders, account/carry observations and no unresolved SPOT attempts.
The collector owns provider qualification; a source label is not authentication.
No retry, cold lookup or settlement grants another HTTP submission.
"""
import asyncio

from hummingbot.core.data_type.common import OrderType, TradeType
from hummingbot.core.event.event_forwarder import EventForwarder
from hummingbot.core.event.events import MarketEvent
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError, identifier
from hummingbot.strategy_v2.life_liquidity.storage.postgres.hedge import PgHedgeRepository, PlanHedge, SettleHedge
from hummingbot.strategy_v2.life_liquidity.storage.postgres.swap_fills import FillSwap, PgSwapFillRepository
from hummingbot.strategy_v2.life_liquidity.storage.postgres.swap_send import PgSwapBoundary
from hummingbot.strategy_v2.life_liquidity.storage.postgres.swap_terminal import (
    ConfirmSwapTerminal,
    PgSwapTerminalRepository,
)
from hummingbot.strategy_v2.life_liquidity.storage.postgres.swap_transport import PgSwapAccountSource


class PgHedgeApplication:
    @classmethod
    async def create(cls, repository, *, connector, pause_makers, cancel_makers, collect, local_fence,
                     clock, max_uid_age_ms, max_auth_age_ms, max_clock_skew_ms, collection_timeout_ms,
                     cycle_timeout_ms):
        if (type(repository) is not PgHedgeRepository
                or any(not callable(v) for v in (pause_makers, cancel_makers, collect, local_fence))
                or type(cycle_timeout_ms) is not int or not 0 < cycle_timeout_ms <= 60000):
            raise StorageError('PG_HEDGE_APPLICATION_CONFIG_INVALID')
        repository._original()
        app = cls()
        app.repository, app.connector = repository, connector
        app._pause, app._cancel, app._collect, app._fence = pause_makers, cancel_makers, collect, local_fence
        app._runtime, app._binding, app._owner = repository.runtime, repository.binding, repository.owner
        app._cycle_timeout_ms = cycle_timeout_ms
        app._busy = app._stopped = app._paused = False
        app.source = await PgSwapAccountSource.create(PgSwapBoundary(repository.swap), connector=connector,
                                                      clock=clock, local_fence=app._ready, max_uid_age_ms=max_uid_age_ms, max_auth_age_ms=max_auth_age_ms,
                                                      max_clock_skew_ms=max_clock_skew_ms, collection_timeout_ms=collection_timeout_ms, max_pending=1)
        app._context = (repository, connector, app.source, repository.swap, repository.financial)
        return app

    def _original(self):
        repository, connector, source, swap, financial = self._context
        repository._original()
        if (self.repository is not repository or self.connector is not connector or self.source is not source
                or repository.swap is not swap or repository.financial is not financial
                or repository.runtime is not self._runtime or repository.binding != self._binding
                or repository.owner != self._owner or source.boundary.swap is not swap):
            self.stop()
            raise StorageError('PG_HEDGE_APPLICATION_CONTEXT_CHANGED')

    def _ready(self):
        if self._stopped or not self._paused or not self._runtime.healthy:
            return False
        try:
            self._original()
            return self._fence() is True
        except Exception:
            self.stop()
            return False

    def _require_ready(self):
        self._original()
        if not self._ready():
            raise StorageError('PG_HEDGE_APPLICATION_STOPPED')

    def stop(self):
        # Never cancel an owned physical COMMIT or release financial holds.
        if self._stopped:
            return
        self._stopped = True
        self._runtime._healthy = False
        self._runtime._ingress_epoch += 1
        if hasattr(self, 'source'):
            self.source.revoke()

    async def execute(self, operation_id):
        identifier(operation_id)
        self._original()
        if self._busy:
            raise StorageError('PG_HEDGE_APPLICATION_BACKPRESSURE')
        self._busy = True
        try:
            async with asyncio.timeout(self._cycle_timeout_ms / 1000):
                # Existing records are diagnostic, including PREPARED children
                # whose process died before HTTP. Reconciliation is explicit.
                previous = await self.repository.lookup(operation_id, self._binding)
                if previous is not None:
                    return previous
                if self._stopped or self._pause() is not True:
                    raise StorageError('PG_HEDGE_MAKER_PAUSE_REQUIRED')
                self._paused = True
                self._require_ready()
                if await self._cancel() is not True:
                    raise StorageError('PG_HEDGE_CANCELLATION_UNQUALIFIED')
                self._require_ready()
                command = await self._collect(operation_id)
                self._require_ready()
                if type(command) is not PlanHedge or command.operation_id != operation_id:
                    raise StorageError('PG_HEDGE_SOURCE_COMMAND_MISMATCH')
                cut = (await self.repository.financial.snapshot(self._binding)).cut
                self._require_ready()
                result = await self.repository.plan(command, cut=cut, owner=self._owner)
                self._require_ready()
                if result.replayed or result.child_state != 'PREPARED':
                    return result
                child = command.child.prepare
                if result.child_operation_id != child.operation_id:
                    raise StorageError('PG_HEDGE_CHILD_RECEIPT_MISMATCH')
                await self._send(child)
                self._require_ready()
                return result
        except BaseException:
            self.stop()
            raise
        finally:
            self._busy = False

    async def _send(self, child):
        config = child.config()
        if (self.connector.get_leverage(config.trading_pair) != config.leverage
                or child.client_order_id in self.connector._order_tracker.active_orders):
            raise StorageError('PG_HEDGE_SDK_CONTEXT_INVALID')
        guard = self.source.guard(child.operation_id)
        completed = asyncio.get_running_loop().create_future()
        completed.add_done_callback(lambda future: None if future.cancelled() else future.exception())
        acknowledged_id = None

        async def acknowledge(exchange_order_id):
            nonlocal acknowledged_id
            await guard.acknowledge(exchange_order_id)
            acknowledged_id = exchange_order_id

        def failed(event):
            if event.order_id == child.client_order_id and not completed.done():
                completed.set_exception(StorageError('PG_HEDGE_SDK_SUBMISSION_UNKNOWN'))

        def created(event):
            if event.order_id == child.client_order_id and not completed.done():
                if not guard._consumed or acknowledged_id is None or event.exchange_order_id != acknowledged_id:
                    completed.set_exception(StorageError('PG_HEDGE_SDK_ACK_REQUIRED'))
                else:
                    completed.set_result(event.exchange_order_id)

        event = MarketEvent.BuyOrderCreated if config.side is TradeType.BUY else MarketEvent.SellOrderCreated
        on_created, on_failure = EventForwarder(created), EventForwarder(failed)
        self.connector.add_listener(event, on_created)
        self.connector.add_listener(MarketEvent.OrderFailure, on_failure)
        try:
            self._require_ready()
            wire = self.connector.submit_protected_order(order_id=child.client_order_id, trading_pair=config.trading_pair,
                                                         amount=config.amount, trade_type=config.side, order_type=OrderType.LIMIT_MAKER, price=config.price,
                                                         position_action=config.position_action, request_send_guard=guard, async_on_ack=acknowledge)
            if wire != child.client_order_id:
                raise StorageError('PG_HEDGE_SDK_WIRE_CHANGED')
            # The SDK owns its scheduled RPC. Caller stop/timeout cannot cancel
            # that RPC or erase late ACK capture. The final guard still denies
            # HTTP if stop happens before its one-shot post-auth check.
            await asyncio.shield(completed)
        finally:
            self.connector.remove_listener(event, on_created)
            self.connector.remove_listener(MarketEvent.OrderFailure, on_failure)

    async def record_fills(self, plan_operation_id, command):
        self._original()
        if type(command) is not FillSwap:
            raise StorageError('PG_HEDGE_FILL_COMMAND_INVALID')
        root = await self.repository.lookup(plan_operation_id, self._binding)
        if root is None or root.child_operation_id != command.prepare_operation_id:
            raise StorageError('PG_HEDGE_FILL_CHILD_MISMATCH')
        repository = PgSwapFillRepository(self.repository.swap)
        previous = await self._replayed(repository, 'SWAP_FILL', command)
        if previous is not None:
            return previous
        cut = (await self.repository.financial.observation_cut(self._binding)).cut
        return await repository.apply_fills(command, cut=cut, owner=self._owner)

    async def record_terminal(self, plan_operation_id, command):
        self._original()
        if type(command) is not ConfirmSwapTerminal:
            raise StorageError('PG_HEDGE_TERMINAL_COMMAND_INVALID')
        root = await self.repository.lookup(plan_operation_id, self._binding)
        if root is None or root.child_operation_id != command.prepare_operation_id:
            raise StorageError('PG_HEDGE_TERMINAL_CHILD_MISMATCH')
        repository = PgSwapTerminalRepository(self.repository.swap)
        previous = await self._replayed(repository, 'SWAP_TERMINAL', command)
        if previous is not None:
            return previous
        cut = (await self.repository.financial.observation_cut(self._binding)).cut
        return await repository.confirm(command, cut=cut, owner=self._owner)

    async def _replayed(self, repository, kind, command):
        previous = await repository._read(operation_id=command.operation_id, binding=self._binding)
        if previous is None:
            return None
        original = previous['command']
        if (original['kind'] != kind or original['binding'] != self._binding.json()
                or original['owner'] != self._owner.json() or original['command'] != command.json()):
            raise StorageError('PG_OPERATION_CONFLICT')
        # Primary verified history retains the original physical cut. Readback
        # does not rerun a command with a fresh version or grant send authority.
        return repository._receipt({**previous['receipt'], 'replayed': True})

    async def settle(self, command):
        self._original()
        if type(command) is not SettleHedge:
            raise StorageError('PG_HEDGE_SETTLEMENT_INVALID')
        previous = await self._replayed(self.repository, 'HEDGE_SETTLE', command)
        if previous is not None:
            return previous
        # Late truth remains admissible after local stop; SQL independently
        # verifies original fills/terminal/account facts. No maker resume here.
        cut = (await self.repository.financial.observation_cut(self._binding)).cut
        return await self.repository.settle(command, cut=cut, owner=self._owner)
