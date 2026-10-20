"""Explicit one-use PG quote boundary for an independently assembled sender.

Facts, runner membership, authenticated UID and external write exclusion still
need qualified application sources. This adapter is not installed by backend
selection and cannot turn historical SQL readback into a new send capability.
"""
import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal as D
from urllib.parse import urlsplit

from hummingbot.core.data_type.in_flight_order import OrderState
from hummingbot.core.web_assistant.connections.data_types import RESTMethod
from hummingbot.core.web_assistant.connections.request_guard import RESTRequestSnapshot
from hummingbot.strategy_v2.life_liquidity.storage.action_preparation import PrepareQuoteAction, QuoteAdmissionFacts
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError, utc
from hummingbot.strategy_v2.life_liquidity.storage.postgres.ack_ingress import PgAckIngress
from hummingbot.strategy_v2.life_liquidity.storage.postgres.runner_journal import PgQuoteRunnerJournal
from hummingbot.strategy_v2.life_liquidity.storage.quote_send import ArmQuoteAction, QuoteArmReceipt
from hummingbot.strategy_v2.models.executor_actions import CreateExecutorAction


@dataclass(frozen=True)
class QuoteSendAgeLimits:
    """Explicit local limits; source assembly must bind these to DB policy."""
    fact_ms: int
    reference_ms: int
    fee_ms: int
    capture_ms: int

    def __post_init__(self):
        if any(type(value) is not int or not 0 < value <= 2147483647 for value in self.__dict__.values()):
            raise StorageError('PG_REQUEST_AGE_LIMITS_INVALID')


class PgQuoteRequestGuard:
    def __init__(self, journal, *, action, preparation, admission_source, endpoint_url, age_limits, account_source=None):
        if (type(journal) is not PgQuoteRunnerJournal or type(action) is not CreateExecutorAction
                or type(preparation) is not PrepareQuoteAction
                or type(endpoint_url) is not str or type(age_limits) is not QuoteSendAgeLimits):
            raise StorageError('PG_REQUEST_GUARD_CONFIG_INVALID')
        endpoint = urlsplit(endpoint_url)
        if (endpoint.scheme != 'https' or not endpoint.hostname or endpoint.username is not None
                or endpoint.password is not None or endpoint.query or endpoint.fragment
                or endpoint.path != '/api/v5/trade/order'):
            raise StorageError('PG_REQUEST_GUARD_CONFIG_INVALID')
        self._journal, self._preparation = journal, preparation
        self._repository, self._runtime = journal.repository, journal.repository._runtime
        self._binding, self._owner = journal.binding, journal.owner
        self._action = action.model_copy(deep=True)
        self._admission_source, self._endpoint_url = admission_source, endpoint_url
        self._age_limits, self._facts = age_limits, None
        if account_source is not None:
            from hummingbot.strategy_v2.life_liquidity.storage.postgres.quote_account_source import PgQuoteAccountSource
            if (type(account_source) is not PgQuoteAccountSource or account_source.journal is not journal
                    or account_source.age_limits != age_limits or account_source.endpoint_url != endpoint_url):
                raise StorageError('PG_REQUEST_ACCOUNT_SOURCE_MISMATCH')
        self._account_source = account_source
        if not callable(admission_source) and (account_source is None or account_source._market_source is None):
            raise StorageError('PG_REQUEST_GUARD_CONFIG_INVALID')
        self._started = self._checked = self._consumed = self._ack_started = False
        self._authorized_request = None
        self._request_sha256 = None
        self._ingress = PgAckIngress(self._repository)
        self._captured_ingress_epoch = None
        self._authorization_task = None
        self._market_capture = None
        self._pending_sdk_order = None

    def pending_order(self, native=None, *, armed=False, observation=False):
        """Original unsent SDK order only; SQL readback cannot mint this link."""
        from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot_application import PgSpotQuoteApplication
        app = self._journal._transport
        cfg, prep = self._action.executor_config, self._preparation.prepare
        if (type(observation) is not bool or observation and armed
                or type(app) is not PgSpotQuoteApplication or app._guards.get(cfg.id) is not self
                or asyncio.current_task() is not self._authorization_task or not self._started or self._consumed
                or app._pending.get(cfg.id) is None or app._pending[cfg.id].done()
                or app._physical.get(cfg.id) is None or app._physical[cfg.id].done()):
            raise StorageError('PG_REQUEST_PENDING_ORIGIN_REQUIRED')
        app._original()
        entry = self._journal._entry(self._action)
        if (entry.prepared is not self._preparation or not entry.accepted or entry.revoked
                or entry.arm_started is not armed or not self._journal.local_runner_create_fence(self._action)):
            raise StorageError('PG_REQUEST_PENDING_ORIGIN_REQUIRED')
        app._sdk_check(self._action, prep.client_order_id, {'clOrdId': prep.client_order_id},
                       observation_guard=self if observation else None)
        order = app.connector._order_tracker.active_orders[prep.client_order_id]
        if self._pending_sdk_order is None and not armed:
            self._pending_sdk_order = order
        if order is not self._pending_sdk_order:
            raise StorageError('PG_REQUEST_PENDING_ORIGIN_REQUIRED')
        if order.executed_amount_base != 0 or order.current_state is not OrderState.PENDING_CREATE:
            raise StorageError('PG_REQUEST_PENDING_ORIGIN_REQUIRED')
        if native is not None and (
                native['product'] != 'SPOT' or native['state'] != 'PREPARED' or native['exchange_order_id'] is not None
                or (native['intent_id'], native['prepare_operation_id'], native['wire_id'], native['side'],
                    native['session_id'], native['epoch']) != (cfg.id, prep.operation_id, prep.client_order_id,
                                                               cfg.side.name, prep.session_id, prep.epoch)
                or D(native['quantity']) != cfg.amount or D(native['price']) != cfg.price):
            raise StorageError('PG_REQUEST_PENDING_ORIGIN_REQUIRED')
        return order

    def _context_ready(self):
        return (self._journal.repository is self._repository and self._repository._runtime is self._runtime
                and self._journal.binding == self._binding and self._journal.owner == self._owner
                and self._runtime.healthy)

    def _scope(self, request):
        headers = dict(request.headers) if type(request) is RESTRequestSnapshot else {}
        # These are local authentication metadata only, never proof of account
        # identity. A qualified source must separately bind the authenticated UID.
        if (type(request) is not RESTRequestSnapshot or request.method is not RESTMethod.POST
                or request.url != self._endpoint_url or request.params or request.is_auth_required is not True
                or type(request.data) is not str or headers.get('Content-Type') != 'application/json'
                or any(not headers.get(key) for key in (
                    'OK-ACCESS-KEY', 'OK-ACCESS-SIGN', 'OK-ACCESS-TIMESTAMP', 'OK-ACCESS-PASSPHRASE'))):
            raise StorageError('PG_REQUEST_SCOPE_INVALID')

    async def authorize(self, request):
        if self._started:
            raise StorageError('PG_REQUEST_GUARD_ALREADY_USED')
        # No retry even after a source/DB failure or caller cancellation. Once
        # physical arm may have committed, only exact recovery/readback is safe.
        self._started = True
        self._authorization_task = asyncio.current_task()
        ingress_epoch = self._runtime._ingress_epoch
        self._scope(request)
        if not self._context_ready() or not self._journal.local_runner_create_fence(self._action):
            return False
        facts = (await self._admission_source() if self._account_source is None
                 else await self._account_source.capture(request, guard=self))
        if self._account_source is not None and self._account_source._market_source is not None:
            # Exactly one original authenticated collection may advance this
            # guard's epoch. Arbitrary callback awaits never get this allowance.
            if self._market_capture is None or self._runtime._ingress_epoch != ingress_epoch + 1:
                return False
            ingress_epoch += 1
        if type(facts) is not QuoteAdmissionFacts:
            raise StorageError('PG_REQUEST_SOURCE_INVALID')
        command = ArmQuoteAction(self._preparation.prepare.operation_id, self._preparation.action, facts, request.data)
        source_cut = None if self._market_capture is None else self._market_capture.report.cut
        result = await self._journal.arm_quote_action(self._action, command, _source_cut=source_cut)
        if (type(result) is not QuoteArmReceipt or result.replayed
                or result.binding != self._journal.binding or result.operation_id != command.prepare_operation_id
                or result.intent_id != command.action.intent_id or result.wire_sha256 != command.wire_sha256
                or not self._context_ready() or self._runtime._ingress_epoch != ingress_epoch
                or not self._journal.local_runner_create_fence(self._action)):
            return False
        self._authorized_request = request
        self._facts = facts
        self._request_sha256 = result.wire_sha256
        self._captured_ingress_epoch = ingress_epoch
        return True

    def _fresh(self):
        try:
            now, facts, limits = utc(self._journal.clock()), self._facts, self._age_limits
            sources = ((facts.observed_at, limits.fact_ms), (facts.reference_at, limits.reference_ms),
                       (facts.exit_value_at, limits.reference_ms), (facts.fee_at, limits.fee_ms),
                       (datetime.fromtimestamp(self._action.executor_config.timestamp, timezone.utc), limits.capture_ms))
            return all(0 <= (now - at).total_seconds() * 1000 <= limit for at, limit in sources)
        except Exception:
            return False

    def check(self, request):
        authorized = self._authorized_request
        self._authorized_request = None
        already_checked, self._checked = self._checked, True
        self._scope(request)
        if (already_checked or self._consumed or authorized is not request
                or not self._context_ready() or self._captured_ingress_epoch != self._runtime._ingress_epoch
                or not self._journal.local_runner_create_fence(self._action) or not self._fresh()
                or self._account_source is not None and not self._account_source.check(request)):
            return False
        if self._market_capture is not None:
            source = self._account_source._market_source
            source._original()
            source.collector.check_report(self._market_capture.report, epoch=self._captured_ingress_epoch)
            source._sdk_scope(self._market_capture.report, pending_quote=self, armed=True)
        # No awaits from consumption to the first HTTP request await. Transport
        # failures remain SEND_UNKNOWN and never restore this local capability.
        self._consumed = True
        return True

    async def acknowledge(self, exchange_order_id):
        if not self._consumed:
            raise StorageError('PG_REQUEST_ACK_BEFORE_SEND')
        if self._ack_started:
            raise StorageError('PG_REQUEST_ACK_ALREADY_USED')
        self._ack_started = True
        try:
            received = await self._ingress.capture(self._preparation.prepare, exchange_order_id,
                                                   owner=self._owner, request_sha256=self._request_sha256)
            # Capture is not accounting permission: the original observation
            # transaction independently verifies ownership/cut/money state.
            receipt = await self._repository.apply_received(received.command, owner=self._owner)
            await self._ingress.mark_applied(received, owner=self._owner)
            return receipt
        except BaseException:
            self._runtime._healthy = False
            raise
