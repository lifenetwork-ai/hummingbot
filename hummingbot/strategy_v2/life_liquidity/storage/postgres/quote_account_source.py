"""Bind explicit quote transport to an authenticated UID and imported DB ages.

No account/owner bootstrap or source activation. Market/reference/fee/planner
provenance and external credential exclusion still require application assembly.
Credentials stay process-local and never enter SQL, receipts or error messages.
"""
import asyncio
import base64
import hashlib
import hmac
import time
from datetime import datetime, timedelta
from decimal import localcontext
from urllib.parse import urlencode, urlsplit

from hummingbot.connector.exchange.okx import okx_constants as CONSTANTS
from hummingbot.connector.exchange.okx.okx_auth import OkxAuth
from hummingbot.connector.exchange.okx.okx_exchange import OkxExchange
from hummingbot.core.web_assistant.connections.data_types import RESTMethod
from hummingbot.core.web_assistant.connections.request_guard import RESTRequestSnapshot
from hummingbot.strategy_v2.life_liquidity.storage.action_preparation import QuoteAdmissionFacts, QuotePolicySnapshot
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError, utc
from hummingbot.strategy_v2.life_liquidity.storage.postgres.quote_transport import (
    PgQuoteRequestGuard,
    QuoteSendAgeLimits,
)
from hummingbot.strategy_v2.life_liquidity.storage.postgres.runner_journal import PgQuoteRunnerJournal
from hummingbot.strategy_v2.life_liquidity.storage.postgres.shared_sdk_quota import PgSharedSdkQuota


class _UidRequestGuard:
    def __init__(self, source):
        self.source, self.request, self.started, self.checked = source, None, False, False
        self.consumed, self.sent = False, None

    async def authorize(self, request):
        if self.started:
            raise StorageError('PG_ACCOUNT_UID_REQUEST_ALREADY_USED')
        self.started = True
        self.source._request(request, RESTMethod.GET, self.source._uid_url)
        if request.data is not None:
            raise StorageError('PG_ACCOUNT_REQUEST_INVALID')
        self.request = request
        return True

    def check(self, request):
        expected, self.request = self.request, None
        if self.checked or expected is not request:
            return False
        self.checked = True
        self.source._request(request, RESTMethod.GET, self.source._uid_url)
        self.consumed, self.sent = True, request
        return True


class PgQuoteAccountSource:
    def __init__(self, journal, *, connector, admission_source=None, market_source=None, max_uid_age_ms, max_auth_age_ms,
                 max_clock_skew_ms, max_pending, collection_timeout_ms):
        if (type(journal) is not PgQuoteRunnerJournal or type(connector) is not OkxExchange
                or type(connector._auth) is not OkxAuth
                or (market_source is None and not callable(admission_source))
                or (market_source is not None and admission_source is not None)
                or type(max_pending) is not int or not 0 < max_pending <= 64
                or any(type(value) is not int or not 0 < value <= 60000 for value in (
                    max_uid_age_ms, max_auth_age_ms, max_clock_skew_ms, collection_timeout_ms))):
            raise StorageError('PG_ACCOUNT_SOURCE_CONFIG_INVALID')
        self.journal, self._connector, self._admission_source = journal, connector, admission_source
        self._market_source = market_source
        self._bound_market_source = market_source
        self._intents = {}
        self._bound_intents = self._intents
        if market_source is not None:
            from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot_market import PgSpotMarketSource
            if (type(market_source) is not PgSpotMarketSource or market_source.repository is not journal.repository
                    or market_source.reader.source.journal is not journal
                    or market_source.reader.source._connector is not connector
                    or market_source.source.owner != journal.owner):
                raise StorageError('PG_ACCOUNT_MARKET_SOURCE_MISMATCH')
            market_source._original()
        self._auth = connector._auth
        self._values = (connector.okx_api_key, connector.okx_secret_key, connector.okx_passphrase)
        self._domain = connector.domain
        if (any(type(value) is not str or not value for value in self._values)
                or self._domain not in {CONSTANTS.get_okx_base_url(name) for name in CONSTANTS.subdomain_to_api_subdomain}):
            raise StorageError('PG_ACCOUNT_SOURCE_CONFIG_INVALID')
        self._order_url = self._domain.rstrip('/') + CONSTANTS.OKX_PLACE_ORDER_PATH
        self._uid_url = self._domain.rstrip('/') + CONSTANTS.OKX_ACCOUNT_CONFIG_PATH
        self.max_uid_age_ms, self.max_auth_age_ms, self.max_clock_skew_ms = max_uid_age_ms, max_auth_age_ms, max_clock_skew_ms
        self.max_pending, self.collection_timeout_ms = max_pending, collection_timeout_ms
        self._policy, self._uid_at, self._uid_monotonic = None, None, None
        self._revoked, self._pending = False, 0
        self._uid_lock = asyncio.Lock()
        self._quota = PgSharedSdkQuota.for_connector(connector)
        self._credentials()

    @classmethod
    async def create(cls, journal, **kwargs):
        self = cls(journal, **kwargs)
        self._policy = await self._read_policy()
        if self._market_source is not None and self._policy != self._market_source._policy:
            raise StorageError('PG_ACCOUNT_MARKET_POLICY_MISMATCH')
        return self

    @property
    def endpoint_url(self):
        return self._order_url

    @property
    def age_limits(self):
        if self._policy is None:
            raise StorageError('PG_ACCOUNT_POLICY_REQUIRED')
        p = self._policy
        return QuoteSendAgeLimits(p.max_fact_age_ms, p.max_reference_age_ms, p.max_fee_age_ms, p.max_capture_age_ms)

    @property
    def pending_count(self):
        return self._pending

    def guard(self, *, action, preparation):
        if self._market_source is not None and self.wire_for_action(action) != preparation.prepare.client_order_id:
            raise StorageError('PG_ACCOUNT_MARKET_INTENT_MISMATCH')
        return PgQuoteRequestGuard(self.journal, action=action, preparation=preparation,
                                   admission_source=self._admission_source, endpoint_url=self.endpoint_url,
                                   age_limits=self.age_limits, account_source=self)

    def capture_intent(self, intent):
        """Create a NEW executor capture once, after external risk preflight."""
        from hummingbot.strategy_v2.executors.order_executor.data_types import ExecutionStrategy, OrderExecutorConfig
        from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot_intent import PgSpotQuoteIntent
        from hummingbot.strategy_v2.models.executor_actions import CreateExecutorAction
        self._credentials()
        if (self._market_source is None or self._policy is None or type(intent) is not PgSpotQuoteIntent
                or intent.owner != self.journal.owner or intent.controller_id != self.journal.controller_id
                or intent.config_version != self.journal.config_version or not self.journal._ready()
                or not self.journal.repository._runtime.healthy):
            raise StorageError('PG_ACCOUNT_MARKET_INTENT_MISMATCH')
        self._market_source._original()
        p = self._policy
        with localcontext() as precision:
            precision.prec = 200
            valid_steps = (intent.quantity_base >= p.min_size and intent.quantity_base % p.lot_size == 0
                           and intent.price_usdt % p.tick_size == 0)
        if (not valid_steps or intent.intent_id in self._intents or intent.intent_id in self.journal._entries
                or len(self._intents) >= self.journal.max_actions):
            raise StorageError('PG_ACCOUNT_MARKET_INTENT_ALREADY_USED_OR_INVALID')
        cfg = OrderExecutorConfig(id=intent.intent_id, controller_id=intent.controller_id,
                                  timestamp=utc(self.journal.clock()).timestamp(), connector_name='okx',
                                  trading_pair='LIFE-USDT', side=intent.side, amount=intent.quantity_base,
                                  price=intent.price_usdt, level_id=str(intent.level), execution_strategy=ExecutionStrategy.LIMIT_MAKER)
        action = CreateExecutorAction(controller_id=intent.controller_id, executor_config=cfg)
        self._intents[intent.intent_id] = intent, self.journal._capture(action)
        return action

    def wire_for_action(self, action):
        self._credentials()
        row = self.journal._capture(action)
        value = self._intents.get(row.intent_id)
        if self._market_source is None or value is None or value[1] != row:
            raise StorageError('PG_ACCOUNT_MARKET_INTENT_MISMATCH')
        return value[0].wire_id

    def _credentials(self):
        c, a = self._connector, self._auth
        if self._market_source is not self._bound_market_source or self._intents is not self._bound_intents:
            self._revoked = True
            raise StorageError('PG_ACCOUNT_MARKET_SOURCE_CHANGED')
        try:
            if PgSharedSdkQuota.for_connector(c) is not self._quota:
                raise StorageError('PG_ACCOUNT_QUOTA_CONTEXT_CHANGED')
        except StorageError:
            self._revoked = True
            raise
        if (self._revoked or c._auth is not a or c.domain != self._domain
                or (c.okx_api_key, c.okx_secret_key, c.okx_passphrase) != self._values
                or (a.api_key, a.secret_key, a.passphrase) != self._values):
            self._revoked = True
            raise StorageError('PG_ACCOUNT_CREDENTIAL_CHANGED')

    def _request(self, request, method, url, *, params=()):
        self._credentials()
        if (type(request) is not RESTRequestSnapshot or request.method is not method
                or request.url != url or request.params != params or request.is_auth_required is not True):
            raise StorageError('PG_ACCOUNT_REQUEST_INVALID')
        headers = dict(request.headers)
        if headers.get('OK-ACCESS-KEY') != self._values[0] or headers.get('OK-ACCESS-PASSPHRASE') != self._values[2]:
            raise StorageError('PG_ACCOUNT_REQUEST_CREDENTIAL_MISMATCH')
        try:
            timestamp = headers['OK-ACCESS-TIMESTAMP']
            at = utc(datetime.fromisoformat(timestamp.replace('Z', '+00:00')))
            age = (utc(self.journal.clock()) - at).total_seconds() * 1000
            path = urlsplit(url).path + ('?' + urlencode(params) if params else '')
            unsigned = timestamp + method.value + path + (request.data or '')
            expected = base64.b64encode(hmac.new(self._values[1].encode(), unsigned.encode(), hashlib.sha256).digest())
            signature_valid = hmac.compare_digest(expected, headers['OK-ACCESS-SIGN'].encode())
        except (KeyError, TypeError, ValueError, StorageError):
            raise StorageError('PG_ACCOUNT_REQUEST_SIGNATURE_INVALID') from None
        if not signature_valid or not -self.max_clock_skew_ms <= age <= self.max_auth_age_ms:
            raise StorageError('PG_ACCOUNT_REQUEST_SIGNATURE_INVALID')
        simulated = {key.lower(): value for key, value in request.headers}.get('x-simulated-trading')
        environment = self.journal.binding.environment
        if (environment == 'DEMO' and simulated != '1'
                or environment == 'PRODUCTION' and simulated not in (None, '0')):
            raise StorageError('PG_ACCOUNT_ENVIRONMENT_MISMATCH')

    async def _read_policy(self):
        self._credentials()
        before, started = utc(self.journal.clock()), time.monotonic()
        value = await self.journal.repository.quote_admission_policy(self.journal.binding)
        after, elapsed = utc(self.journal.clock()), time.monotonic() - started
        skew = timedelta(milliseconds=self.max_clock_skew_ms)
        if (type(value) is not QuotePolicySnapshot or value.policy.binding != self.journal.binding
                or after < before or abs((after - before).total_seconds() - elapsed) * 1000 > self.max_clock_skew_ms
                or not before - skew <= value.observed_at <= after + skew):
            raise StorageError('PG_ACCOUNT_CLOCK_UNQUALIFIED')
        self._credentials()
        return value.policy

    def _uid_fresh(self):
        if self._uid_at is None:
            return False
        age = (utc(self.journal.clock()) - self._uid_at).total_seconds() * 1000
        elapsed = (time.monotonic() - self._uid_monotonic) * 1000
        return (0 <= age <= self.max_uid_age_ms and 0 <= elapsed <= self.max_uid_age_ms
                and abs(age - elapsed) <= self.max_clock_skew_ms)

    async def _uid(self, *, force=False):
        async with self._uid_lock:
            self._credentials()
            if not force and self._uid_fresh():
                return
            guard = _UidRequestGuard(self)
            before, started = utc(self.journal.clock()), time.monotonic()
            uid = await self._connector.get_account_uid(request_send_guard=guard)
            if not guard.consumed:
                raise StorageError('PG_ACCOUNT_UID_TRANSPORT_UNQUALIFIED')
            self._credentials()
            self._request(guard.sent, RESTMethod.GET, self._uid_url)
            after, elapsed = utc(self.journal.clock()), time.monotonic() - started
            if after < before or abs((after - before).total_seconds() - elapsed) * 1000 > self.max_clock_skew_ms:
                raise StorageError('PG_ACCOUNT_CLOCK_UNQUALIFIED')
            if uid != self.journal.binding.account_uid:
                self._revoked = True
                raise StorageError('PG_ACCOUNT_UID_MISMATCH')
            self._uid_at, self._uid_monotonic = utc(self.journal.clock()), time.monotonic()

    async def capture(self, request, *, guard=None):
        self._request(request, RESTMethod.POST, self.endpoint_url)
        if self._pending >= self.max_pending:
            raise StorageError('PG_ACCOUNT_SOURCE_BACKPRESSURE')
        self._pending += 1
        try:
            epoch = self.journal.repository._runtime._ingress_epoch
            async with asyncio.timeout(self.collection_timeout_ms / 1000):
                await self._uid()
                if self._market_source is None and await self._read_policy() != self._policy:
                    self._revoked = True
                    raise StorageError('PG_ACCOUNT_POLICY_CHANGED')
                if self._market_source is None:
                    facts = await self._admission_source()
                else:
                    if (type(guard) is not PgQuoteRequestGuard or guard._account_source is not self
                            or guard._authorized_request is not None):
                        raise StorageError('PG_ACCOUNT_MARKET_GUARD_REQUIRED')
                    guard.pending_order()
                    capture = await self._market_source.collect(pending_quote=guard)
                    if capture.report.quote_policy != self._policy:
                        raise StorageError('PG_ACCOUNT_MARKET_POLICY_MISMATCH')
                    costs = guard._preparation.admission
                    # Keep the original preparation's economic costs and age.
                    # A new book read cannot renew an old cost assessment.
                    limits = self.age_limits
                    now = utc(self.journal.clock())
                    if any(not 0 <= (now - at).total_seconds() * 1000 <= limit for at, limit in (
                            (costs.observed_at, limits.fact_ms), (costs.reference_at, limits.reference_ms),
                            (costs.exit_value_at, limits.reference_ms), (costs.fee_at, limits.fee_ms))):
                        raise StorageError('PG_ACCOUNT_PREPARATION_FACTS_EXPIRED')
                    cfg = guard._action.executor_config
                    facts = self._market_source.admission(capture, side=cfg.side.name, quantity_base=cfg.amount,
                                                          carry_cost_usdt=costs.carry_cost_usdt,
                                                          inventory_risk_usdt=costs.inventory_risk_usdt,
                                                          pending_quote=guard)
                    if self.journal.repository._runtime._ingress_epoch != epoch + 1:
                        raise StorageError('PG_ACCOUNT_MARKET_INGRESS_CHANGED')
                    guard._market_capture = capture
                if type(facts) is not QuoteAdmissionFacts or facts.binding != self.journal.binding:
                    raise StorageError('PG_ACCOUNT_ADMISSION_SOURCE_INVALID')
                if not self.check(request):
                    raise StorageError('PG_ACCOUNT_SOURCE_EXPIRED')
                return facts
        except StorageError:
            raise
        except Exception:
            raise StorageError('PG_ACCOUNT_SOURCE_UNAVAILABLE') from None
        finally:
            self._pending -= 1

    def check(self, request):
        try:
            self._request(request, RESTMethod.POST, self.endpoint_url)
            return self._uid_fresh()
        except (StorageError, TypeError, ValueError):
            return False

    def revoke(self):
        self._revoked = True
