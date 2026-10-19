"""Signed maker cancellation followed by authenticated native terminal proof.

Cancellation is observation/reduction work, admissible after HALT. It grants no
new-risk permission, releases no shared claim, and never infers terminal state
from an HTTP cancellation ACK or from an empty SDK cache alone.
"""
import asyncio
import json
from decimal import Decimal

from hummingbot.connector.exchange.okx import okx_constants as C
from hummingbot.core.web_assistant.connections.data_types import RESTMethod
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError, fingerprint, identifier
from hummingbot.strategy_v2.life_liquidity.storage.postgres.joint_release import PgJointReleaseApplication
from hummingbot.strategy_v2.life_liquidity.storage.postgres.joint_source import PgJointAccountReconciler
from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import sql_error
from hummingbot.strategy_v2.life_liquidity.storage.spot_lifecycle import SpotLifecycleCommand


class _CancelGuard:
    def __init__(self, application, report, native):
        self.app, self.report, self.native = application, report, dict(native)
        self.epoch = application.collector._epoch
        self.started = self.checked = self.consumed = False
        self.request = None

    def _validate(self, request):
        app = self.app
        app._ready()
        app.collector.check_report(self.report, epoch=self.epoch)
        app._orders(self.report)
        source = app.collector.reader.source
        source._request(request, RESTMethod.POST, source._domain.rstrip('/') + C.OKX_ORDER_CANCEL_PATH)
        try:
            pairs = json.loads(request.data, object_pairs_hook=lambda values: values)
        except (ValueError, TypeError):
            raise StorageError('PG_MAKER_CANCEL_WIRE_INVALID') from None
        if (type(request.data) is not str or len(request.data.encode()) > 512 or type(pairs) is not list
                or len(pairs) != 2 or sorted(pairs) != sorted([('instId', 'LIFE-USDT'), ('clOrdId', self.native['wire_id'])])):
            raise StorageError('PG_MAKER_CANCEL_WIRE_INVALID')

    async def authorize(self, request):
        if self.started:
            raise StorageError('PG_MAKER_CANCEL_REQUEST_ALREADY_USED')
        self.started = True
        self._validate(request)
        await self.app._primary(self.report)
        self._validate(request)
        self.request = request
        return True

    def check(self, request):
        original, self.request = self.request, None
        if self.checked or original is not request:
            return False
        self.checked = True
        self._validate(request)
        self.consumed = True
        return True


class PgMakerCancellation:
    def __init__(self, collector, *, paused, max_cancels, retry_interval_ms, timeout_ms):
        if (type(collector) is not PgJointAccountReconciler or not callable(paused)
                or type(max_cancels) is not int or not 0 < max_cancels <= 64
                or type(retry_interval_ms) is not int or not 0 < retry_interval_ms <= 60000
                or type(timeout_ms) is not int or not 0 < timeout_ms <= 60000):
            raise StorageError('PG_MAKER_CANCEL_CONFIG_INVALID')
        collector._original()
        self.collector = collector
        self.financial, self.runtime, self.owner = collector.financial, collector.runtime, collector.swap.owner
        self.binding, self.connector = collector.binding, collector.reader.source._connector
        self.release = PgJointReleaseApplication(collector)
        self._paused, self.max_cancels = paused, max_cancels
        self.retry_interval_ms, self.timeout_ms = retry_interval_ms, timeout_ms
        self._busy = self._stopped = False
        self._hedge_handoff = None
        self._context = (collector, self.financial, self.runtime, self.owner, self.binding, self.connector, self.release)

    def _ready(self):
        if self._stopped:
            raise StorageError('PG_MAKER_CANCEL_STOPPED')
        if self._context != (self.collector, self.financial, self.runtime, self.owner, self.binding, self.connector, self.release):
            raise StorageError('PG_MAKER_CANCEL_CONTEXT_CHANGED')
        self.collector._original()
        if (self.collector.reader.source._connector is not self.connector or self.collector.swap.owner != self.owner
                or self._paused() is not True):
            raise StorageError('PG_MAKER_CANCEL_PAUSED_SCOPE_REQUIRED')

    def stop(self):
        if not self._stopped:
            self._stopped = True
            original = self._context[2]
            original._healthy = False
            original._ingress_epoch += 1

    @staticmethod
    def _id(operation_id, native, stage):
        return 'maker-' + stage + ':' + fingerprint([operation_id, native['intent_id'], native['prepare_operation_id']])

    async def _primary(self, report):
        import asyncpg

        async def read(connection):
            try:
                return await connection.fetchrow('''SELECT a.version,
                    life_financial.guard_spot_observation($1,$2,$3,a.version)
                    FROM life_financial.accounts a WHERE a.account_id=$1''',
                                                 self.runtime.config.account_id, self.binding.json(), self.owner.json())
            except asyncpg.RaiseError as error:
                raise sql_error(error) from None
        row = await self.runtime._admit(read, safety=True, operation='maker_cancel_identity')
        self._ready()
        if row is None or row['version'] != report.cut.checkpoint.revision:
            raise StorageError('PG_MAKER_CANCEL_NATIVE_CUT_CHANGED')

    def _orders(self, report):
        self._ready()
        self.collector.check_report(report, epoch=self.collector._epoch)
        records = [(json.loads(row.native_json), None if row.exchange_json is None else json.loads(row.exchange_json))
                   for row in report.orders]
        spot = {native['wire_id']: (native, exchange) for native, exchange in records if native['product'] == 'SPOT'}
        # Inspect actual SDK objects as well as the qualified runner-journal
        # scope. SQL records never populate/restore this SDK cache.
        for wire, order in self.connector._order_tracker.active_orders.items():
            item = spot.get(wire)
            if item is None:
                raise StorageError('PG_MAKER_CANCEL_SDK_SCOPE_INVALID')
            native, exchange = item
            if (order.client_order_id != wire or order.trading_pair != 'LIFE-USDT'
                    or order.trade_type.name != native['side'] or order.amount != Decimal(native['quantity'])
                    or order.price != Decimal(native['price'])
                    or order.exchange_order_id not in (None, None if exchange is None else exchange['ordId'])):
                raise StorageError('PG_MAKER_CANCEL_SDK_SCOPE_INVALID')
        return [(native, exchange) for native, exchange in records if native['product'] == 'SPOT']

    async def _attempt(self, operation_id, native):
        request_id, attempt_id = (self._id(operation_id, native, stage) for stage in ('request', 'attempt'))
        if await self.financial.lookup_lifecycle(attempt_id, self.binding) is not None:
            raise StorageError('PG_MAKER_CANCEL_RECONCILE_BEFORE_RETRY')
        row = await self.financial.attempt(native['intent_id'], self.binding)
        if row is None or row.state.value not in ('SEND_UNKNOWN', 'ACKED') or row.client_order_id != native['wire_id']:
            raise StorageError('PG_MAKER_CANCEL_ATTEMPT_CHANGED')
        if not row.cancel_requested:
            self.runtime._ingress_epoch += 1
            cut = await self.financial.observation_cut(self.binding)
            await self.financial.apply_lifecycle(SpotLifecycleCommand(request_id, native['prepare_operation_id'],
                                                                      native['intent_id'], native['wire_id'], 'REQUEST_CANCEL'), cut=cut.cut, owner=self.owner)
        self._ready()
        self.runtime._ingress_epoch += 1
        cut = await self.financial.observation_cut(self.binding)
        receipt = await self.financial.apply_lifecycle(SpotLifecycleCommand(attempt_id, native['prepare_operation_id'],
                                                                            native['intent_id'], native['wire_id'], 'RECORD_CANCEL_ATTEMPT', cut.observed_at, row.cancel_attempts,
                                                                            self.retry_interval_ms), cut=cut.cut, owner=self.owner)
        self._ready()
        if receipt.replayed:
            raise StorageError('PG_MAKER_CANCEL_RECONCILE_BEFORE_RETRY')

    def take_hedge_report(self, operation_id):
        """Consume the same operation's fresh, quiescent source handoff once.

        No refresh or authority is minted here. Native financial commands must
        still consume the exact captured cut; any intervening write/ingress,
        expiry, foreign SDK order or changed collector invalidates the handoff.
        """
        self._ready()
        handoff = self._hedge_handoff
        self._hedge_handoff = None
        if handoff is None:
            return None
        original_id, report, epoch = handoff
        if self._busy or operation_id != original_id:
            raise StorageError('PG_MAKER_CANCEL_SOURCE_HANDOFF_INVALID')
        self.collector.check_report(report, epoch=epoch)
        return report

    async def cancel_makers(self, operation_id, *, include_hedge_market=False):
        identifier(operation_id)
        self._ready()
        if type(include_hedge_market) is not bool:
            raise StorageError('PG_MAKER_CANCEL_SOURCE_HANDOFF_INVALID')
        if self._busy:
            raise StorageError('PG_MAKER_CANCEL_BACKPRESSURE')
        self._busy = True
        self._hedge_handoff = None
        requested = set()
        try:
            async with asyncio.timeout(self.timeout_ms / 1000):
                # At most max_cancels physical attempts. A still-live order
                # requires a later explicit reconciliation, never an HTTP loop.
                for _ in range(self.max_cancels + 1):
                    market = False
                    if include_hedge_market:
                        # This native read is only a work-selection hint. Never
                        # fetch a price batch before expected cancel/release
                        # writes. The complete signed collection below still
                        # authenticates scope and owns the only reusable cut.
                        scope = await self.collector._scope()
                        market = all(v['state'] in ('TERMINAL', 'ABORTED_BEFORE_SEND')
                                     for v in scope['ORDER'].values() if v['product'] == 'SPOT')
                    report = await self.collector.collect(include_hedge_market=market)
                    orders = self._orders(report)
                    if any(native['state'] == 'PREPARED' for native, _ in orders):
                        raise StorageError('PG_MAKER_CANCEL_UNSENT_RECONCILIATION_REQUIRED')
                    live = [(native, exchange) for native, exchange in orders
                            if exchange is not None and exchange['state'] in ('live', 'partially_filled')]
                    if not live:
                        if report.runner_executor_ids or self.connector._order_tracker.active_orders:
                            raise StorageError('PG_MAKER_CANCEL_SDK_NOT_QUIESCENT')
                        changed = False
                        for native, exchange in orders:
                            if native['state'] not in ('TERMINAL', 'ABORTED_BEFORE_SEND'):
                                changed = True
                                self._ready()
                                receipt = await self.release.confirm_terminal(self._id(operation_id, native, 'terminal'), native['intent_id'])
                                if receipt.state != 'TERMINAL':
                                    raise StorageError('PG_MAKER_CANCEL_TERMINAL_UNQUALIFIED')
                        self._ready()
                        if include_hedge_market and not changed and report.market_json is not None:
                            self._hedge_handoff = (operation_id, report, self.collector._epoch)
                        return True
                    candidate = next((native for native, _ in live if native['wire_id'] not in requested), None)
                    if candidate is None:
                        raise StorageError('PG_MAKER_CANCEL_PENDING')
                    if len(requested) >= self.max_cancels:
                        raise StorageError('PG_MAKER_CANCEL_LIMIT')
                    await self._attempt(operation_id, candidate)
                    # Lifecycle publication invalidates the old cut/report.
                    report = await self.collector.collect()
                    orders = self._orders(report)
                    current = next(((native, exchange) for native, exchange in orders
                                    if native['intent_id'] == candidate['intent_id']), None)
                    if current is None or current[0] != candidate:
                        raise StorageError('PG_MAKER_CANCEL_ATTEMPT_CHANGED')
                    requested.add(candidate['wire_id'])
                    if current[1] is None or current[1]['state'] not in ('live', 'partially_filled'):
                        continue
                    guard = _CancelGuard(self, report, candidate)
                    if await self.connector.cancel_by_client_id('LIFE-USDT', candidate['wire_id'], request_send_guard=guard) is not True or not guard.consumed:
                        raise StorageError('PG_MAKER_CANCEL_TRANSPORT_UNQUALIFIED')
                raise StorageError('PG_MAKER_CANCEL_LIMIT')
        except BaseException as error:
            self.stop()
            if isinstance(error, (StorageError, asyncio.CancelledError)):
                raise
            raise StorageError('PG_MAKER_CANCEL_UNAVAILABLE') from None
        finally:
            self._busy = False
