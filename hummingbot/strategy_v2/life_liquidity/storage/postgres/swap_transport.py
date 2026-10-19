"""Explicit signed SWAP HTTP boundary; configured controller assembly is separate.

Only an original local one-shot may consume a newly committed arm. SQL receipts
and cold readback are diagnostic. Secrets and auth headers never enter storage.
Caller runner fencing is a required integration input, not SDK authentication.
"""
import asyncio
import base64
import hashlib
import hmac
import time
from datetime import datetime
from urllib.parse import urlsplit

from hummingbot.connector.derivative.okx_perpetual import okx_perpetual_constants as CONSTANTS
from hummingbot.connector.derivative.okx_perpetual.okx_perpetual_derivative import OkxPerpetualDerivative
from hummingbot.connector.derivative.okx_perpetual.okx_perpetual_web_utils import get_rest_url_for_endpoint
from hummingbot.core.web_assistant.connections.data_types import RESTMethod
from hummingbot.core.web_assistant.connections.request_guard import RESTRequestSnapshot
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError, identifier, utc
from hummingbot.strategy_v2.life_liquidity.storage.postgres.quote_account_source import _UidRequestGuard
from hummingbot.strategy_v2.life_liquidity.storage.postgres.shared_sdk_quota import PgSharedSdkQuota
from hummingbot.strategy_v2.life_liquidity.storage.postgres.swap_ack import PgSwapAckIngress
from hummingbot.strategy_v2.life_liquidity.storage.postgres.swap_send import ArmSwapRequest, PgSwapBoundary


class PgSwapAccountSource:
    def __init__(self, boundary, *, connector, clock, local_fence, max_uid_age_ms, max_auth_age_ms,
                 max_clock_skew_ms, collection_timeout_ms, max_pending):
        if (type(boundary) is not PgSwapBoundary or type(connector) is not OkxPerpetualDerivative
                or not callable(clock) or not callable(local_fence) or type(max_pending) is not int or not 0 < max_pending <= 64
                or any(type(v) is not int or not 0 < v <= 60000 for v in (
                    max_uid_age_ms, max_auth_age_ms, max_clock_skew_ms, collection_timeout_ms))):
            raise StorageError('PG_SWAP_SOURCE_CONFIG_INVALID')
        self.boundary, self._connector, self.clock, self._local_fence = boundary, connector, clock, local_fence
        self._runtime, self._binding, self._owner = boundary.runtime, boundary.binding, boundary.owner
        self._domain = connector.domain
        if self._domain not in CONSTANTS.REST_URLS:
            raise StorageError('PG_SWAP_SOURCE_CONFIG_INVALID')
        self._order_url = get_rest_url_for_endpoint('/api/v5/trade/order', self._domain)
        self._uid_url = get_rest_url_for_endpoint('/api/v5/account/config', self._domain)
        self._values = self._credentials_values()
        if any(type(v) is not str or not v for v in self._values):
            raise StorageError('PG_SWAP_SOURCE_CONFIG_INVALID')
        self.max_uid_age_ms, self.max_auth_age_ms, self.max_clock_skew_ms = max_uid_age_ms, max_auth_age_ms, max_clock_skew_ms
        self.collection_timeout_ms, self.max_pending = collection_timeout_ms, max_pending
        self._uid_at = self._uid_monotonic = self._policy = None
        self._pending, self._revoked, self._uid_lock = 0, False, asyncio.Lock()
        self._quota = PgSharedSdkQuota.for_connector(connector)

    @classmethod
    async def create(cls, boundary, **kwargs):
        value = cls(boundary, **kwargs)
        value._policy = await boundary.policy(boundary.binding)
        if type(value._policy) is not dict or value._policy.get('policy_hash') != boundary.binding.policy_hash:
            raise StorageError('PG_SWAP_BOUNDARY_POLICY_REQUIRED')
        return value

    def _credentials_values(self):
        c = self._connector
        return c.okx_perpetual_api_key, c.okx_perpetual_secret_key, c.okx_perpetual_passphrase

    def _ready(self):
        self.boundary._original()
        try:
            if PgSharedSdkQuota.for_connector(self._connector) is not self._quota:
                self._revoked = True
        except StorageError:
            self._revoked = True
            raise
        if (self.boundary.runtime is not self._runtime or self.boundary.binding != self._binding
                or self.boundary.owner != self._owner or self._connector.domain != self._domain
                or self._credentials_values() != self._values):
            self._revoked = True
        if not self._revoked:
            try:
                self._revoked = self._local_fence() is not True
            except Exception:
                self._revoked = True
                raise StorageError('PG_SWAP_LOCAL_FENCE_UNQUALIFIED') from None
        return not self._revoked and self._runtime.healthy

    def _request(self, request, method, url):
        if (not self._ready() or type(request) is not RESTRequestSnapshot or request.method is not method
                or request.url != url or request.params or request.is_auth_required is not True
                or method is RESTMethod.GET and request.data is not None
                or method is RESTMethod.POST and type(request.data) is not str):
            raise StorageError('PG_SWAP_REQUEST_SCOPE_INVALID')
        headers = dict(request.headers)
        if method is RESTMethod.POST and headers.get('Content-Type') != 'application/json':
            raise StorageError('PG_SWAP_REQUEST_SCOPE_INVALID')
        try:
            timestamp = headers['OK-ACCESS-TIMESTAMP']
            age = (utc(self.clock()) - utc(datetime.fromisoformat(timestamp.replace('Z', '+00:00')))).total_seconds() * 1000
            message = timestamp + method.value + urlsplit(url).path + (request.data or '')
            signature = base64.b64encode(hmac.new(self._values[1].encode(), message.encode(), hashlib.sha256).digest()).decode()
            valid = (headers['OK-ACCESS-KEY'] == self._values[0] and headers['OK-ACCESS-PASSPHRASE'] == self._values[2]
                     and hmac.compare_digest(signature, headers['OK-ACCESS-SIGN'])
                     and -self.max_clock_skew_ms <= age <= self.max_auth_age_ms)
        except (KeyError, TypeError, ValueError):
            valid = False
        if not valid:
            raise StorageError('PG_SWAP_REQUEST_SIGNATURE_INVALID')
        simulated = {k.lower(): v for k, v in request.headers}.get('x-simulated-trading')
        demo = self._binding.environment == 'DEMO' or (self._binding.environment == 'OFFLINE_SIMULATION'
                                                       and self._domain == 'okx_perpetual_demo')
        if (demo and simulated != '1' or not demo and simulated not in (None, '0')):
            raise StorageError('PG_SWAP_REQUEST_ENVIRONMENT_MISMATCH')

    def _uid_fresh(self):
        if self._uid_at is None:
            return False
        wall = (utc(self.clock()) - self._uid_at).total_seconds() * 1000
        elapsed = (time.monotonic() - self._uid_monotonic) * 1000
        return (0 <= wall <= self.max_uid_age_ms and 0 <= elapsed <= self.max_uid_age_ms
                and abs(wall - elapsed) <= self.max_clock_skew_ms)

    async def refresh_uid(self, *, force=False):
        """Qualify this exact SDK account before a native market capture."""
        if type(force) is not bool or not self._ready():
            raise StorageError('PG_SWAP_SOURCE_CHANGED')
        async with asyncio.timeout(self.collection_timeout_ms / 1000):
            async with self._uid_lock:
                if force or not self._uid_fresh():
                    guard = _UidRequestGuard(self)
                    before, started = utc(self.clock()), time.monotonic()
                    uid = await self._connector.get_account_uid(request_send_guard=guard)
                    after, elapsed = utc(self.clock()), time.monotonic() - started
                    if (not guard.consumed or after < before
                            or abs((after - before).total_seconds() - elapsed) * 1000 > self.max_clock_skew_ms):
                        raise StorageError('PG_SWAP_UID_TRANSPORT_UNQUALIFIED')
                    self._request(guard.sent, RESTMethod.GET, self._uid_url)
                    if uid != self._binding.account_uid:
                        self._revoked = True
                        raise StorageError('PG_SWAP_UID_MISMATCH')
                    self._uid_at, self._uid_monotonic = after, time.monotonic()

    async def capture(self, request):
        self._request(request, RESTMethod.POST, self._order_url)
        if self._pending >= self.max_pending:
            raise StorageError('PG_SWAP_SOURCE_BACKPRESSURE')
        self._pending += 1
        try:
            async with asyncio.timeout(self.collection_timeout_ms / 1000):
                # Planning may have consumed most of a prior identity window.
                # Obtain a new signed identity for this physical send attempt;
                # account, market and action timestamps are never renewed.
                await self.refresh_uid(force=True)
                if await self.boundary.policy(self._binding) != self._policy or not self.check(request):
                    raise StorageError('PG_SWAP_SOURCE_CHANGED')
        finally:
            self._pending -= 1

    def check(self, request):
        try:
            self._request(request, RESTMethod.POST, self._order_url)
            return self._uid_fresh()
        except (StorageError, ValueError, TypeError):
            return False

    def guard(self, prepare_operation_id):
        return PgSwapRequestGuard(self, prepare_operation_id)

    def revoke(self):
        self._revoked = True


class PgSwapRequestGuard:
    def __init__(self, source, prepare_operation_id):
        if type(source) is not PgSwapAccountSource:
            raise StorageError('PG_SWAP_SOURCE_REQUIRED')
        identifier(prepare_operation_id)
        self._source, self._prepare = source, prepare_operation_id
        self._started = self._checked = self._consumed = self._ack_started = False
        self._request = self._result = self._started_at = self._monotonic = self._epoch = None

    async def authorize(self, request):
        if self._started:
            raise StorageError('PG_SWAP_REQUEST_ALREADY_USED')
        self._started = True
        s = self._source
        self._epoch = s._runtime._ingress_epoch
        await s.capture(request)
        self._started_at, self._monotonic = utc(s.clock()), time.monotonic()
        cut = (await s.boundary.financial.snapshot(s._binding)).cut
        self._result = await s.boundary.arm(ArmSwapRequest(self._prepare, request.data), cut=cut, owner=s._owner)
        if self._result.replayed or self._result.wire_sha256 != hashlib.sha256(request.data.encode()).hexdigest():
            return False
        self._request = request
        return self._valid(request)

    def _valid(self, request):
        s, r = self._source, self._result
        if r is None or not s.check(request) or s._runtime._ingress_epoch != self._epoch:
            return False
        now_ms = utc(s.clock()).timestamp() * 1000
        elapsed = (time.monotonic() - self._monotonic) * 1000
        wall = (utc(s.clock()) - self._started_at).total_seconds() * 1000
        # Both UTC and monotonic must remain inside the original DB window.
        return (r.checked_at_ms - s.max_clock_skew_ms <= now_ms and now_ms + s.max_clock_skew_ms <= r.valid_until_ms
                and elapsed <= r.valid_until_ms - r.checked_at_ms and 0 <= wall
                and abs(wall - elapsed) <= s.max_clock_skew_ms)

    def check(self, request):
        previous, self._request = self._request, None
        checked, self._checked = self._checked, True
        if checked or previous is not request or not self._valid(request):
            return False
        self._consumed = True
        return True

    async def acknowledge(self, exchange_order_id):
        if not self._consumed:
            raise StorageError('PG_SWAP_ACK_BEFORE_SEND')
        if self._ack_started:
            raise StorageError('PG_SWAP_ACK_ALREADY_USED')
        self._ack_started = True
        s = self._source
        try:
            ingress = PgSwapAckIngress(s.boundary)
            received = await ingress.capture(self._prepare, exchange_order_id, wire_sha256=self._result.wire_sha256)
            return await ingress.deliver(received)
        except BaseException:
            s._runtime._healthy = False
            raise
