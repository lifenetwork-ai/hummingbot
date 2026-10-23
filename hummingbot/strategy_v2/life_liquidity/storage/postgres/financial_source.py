"""Pinned signed OKX reads for joint intake and account-wide reconciliation.

No arbitrary URL/method/params, sender, activation or terminal release. Bounds
limit collection work/accepted decoded JSON; they are not an HTTP streaming cap.
"""
import asyncio
import json
import time
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from uuid import uuid4

from hummingbot.connector.exchange.okx import okx_constants as C
from hummingbot.core.web_assistant.connections.data_types import RESTMethod
from hummingbot.strategy_v2.life_liquidity.order_gateway import OkxSpotOrderGateway
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError, identifier, utc
from hummingbot.strategy_v2.life_liquidity.storage.postgres.observation_account_source import PgObservationAccountSource
from hummingbot.strategy_v2.life_liquidity.storage.postgres.provider_journal import PgProviderJournal
from hummingbot.strategy_v2.life_liquidity.storage.postgres.quote_account_source import (
    PgQuoteAccountSource,
    _UidConfiguration,
)


@dataclass
class _CollectionCapture:
    collector: object
    task: object
    collection_id: str
    pages: list
    attempted: bool = False


class _UidReadExpired(StorageError):
    """The original GET guard refused before the HTTP send boundary."""


class _FinancialReadGuard:
    def __init__(self, reader, path, params):
        self.reader, self.params = reader, tuple(params.items())
        self.url = reader.source._domain.rstrip('/') + path
        self.request, self.started, self.checked, self.consumed = None, False, False, False
        self.uid_expired = False

    def _validate(self, request):
        self.reader._original()
        self.reader.source._request(request, RESTMethod.GET, self.url, params=self.params)
        if request.data is not None:
            raise StorageError('PG_FINANCIAL_SOURCE_REQUEST_INVALID')
        status = self.reader.source._uid_status()
        if status == 'EXPIRED':
            self.uid_expired = True
            raise _UidReadExpired('PG_FINANCIAL_SOURCE_UID_EXPIRED')
        if status != 'FRESH':
            raise StorageError('PG_FINANCIAL_SOURCE_REQUEST_INVALID')

    async def authorize(self, request):
        if self.started:
            raise StorageError('PG_FINANCIAL_SOURCE_REQUEST_ALREADY_USED')
        self.started = True
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


class PgSpotAccountReader:
    SWAP_ALGO_TYPES = (*OkxSpotOrderGateway.SPOT_ALGO_TYPES, 'chase')

    def __init__(self, source, *, max_reads, max_bytes, timeout_ms):
        if (type(source) not in (PgQuoteAccountSource, PgObservationAccountSource)
                or type(source) is PgQuoteAccountSource and source._policy is None
                or type(source) is PgObservationAccountSource and source._identity_checked is not True
                or type(max_reads) is not int or not 0 < max_reads <= 512
                or type(max_bytes) is not int or not 0 < max_bytes <= 16 * 1024 * 1024
                or type(timeout_ms) is not int or not 0 < timeout_ms <= 60000):
            raise StorageError('PG_FINANCIAL_SOURCE_CONFIG_INVALID')
        self.source = source
        self.repository, self.runtime, self.binding = source.journal.repository, source.journal.repository._runtime, source.journal.binding
        self.owner = source.journal.owner
        self.max_reads, self.max_bytes, self.timeout_ms = max_reads, max_bytes, timeout_ms
        self._active, self._reads, self._bytes = False, 0, 0
        self._journal, self.collection_id = PgProviderJournal(self.repository), None
        self._read_active = False
        self.started_at = None
        self._opening_configuration = self._closing_configuration = None
        self._configuration_seal = self._configuration_task = None
        self._collection_capture = None
        self._context = (source, source.journal, self.repository, self.runtime, self.binding, self.owner, self._journal)

    def _original(self):
        source, journal, repository, runtime, binding, owner, capture = self._context
        if (self.source is not source or source.journal is not journal or self.repository is not repository
                or self.runtime is not runtime or self.binding != binding or self.owner != owner or self._journal is not capture):
            raise StorageError('PG_FINANCIAL_SOURCE_CONTEXT_CHANGED')
        self.source._credentials()
        if (not self._active or self.source.journal.repository is not self.repository
                or self.repository._runtime is not self.runtime or self.source.journal.binding != self.binding
                or self.repository.binding != self.binding or self.source.journal.owner != self.owner or self.runtime._closing):
            raise StorageError('PG_FINANCIAL_SOURCE_CONTEXT_CHANGED')
        capture = self._collection_capture
        if capture is not None and (type(capture) is not _CollectionCapture
                                    or capture.task is not asyncio.current_task() or capture.collection_id != self.collection_id
                                    or not capture.collector.owns_reader_collection(self)):
            raise StorageError('PG_FINANCIAL_SOURCE_COLLECTION_ORIGIN')

    @asynccontextmanager
    async def collection(self, *, account_brackets=False, quota_continuation=None, _collector=None):
        if type(account_brackets) is not bool:
            raise StorageError('PG_FINANCIAL_SOURCE_CONFIG_INVALID')
        if self._active or self._read_active:
            raise StorageError('PG_FINANCIAL_SOURCE_BACKPRESSURE')
        if _collector is not None:
            from hummingbot.strategy_v2.life_liquidity.storage.postgres.joint_observer import PgJointFinancialObserver
            from hummingbot.strategy_v2.life_liquidity.storage.postgres.joint_source import PgJointAccountReconciler
            if (type(_collector) not in (PgJointAccountReconciler, PgJointFinancialObserver) or not account_brackets
                    or not _collector.owns_reader_collection(self)
                    or not any(m.version == 99 for m in self._context[3]._bundle)):
                raise StorageError('PG_FINANCIAL_SOURCE_COLLECTION_ORIGIN')
        runtime = self._context[3]
        journal = self._context[6]
        self._active, self._reads, self._bytes = True, 0, 0
        self.collection_id = 'provider:' + uuid4().hex
        self._opening_configuration = self._closing_configuration = None
        self._configuration_seal = None
        capture = None if _collector is None else _CollectionCapture(_collector, asyncio.current_task(), self.collection_id, [])
        self._collection_capture = capture
        runtime._ingress_epoch += 1
        runtime._observation_inflight += 1
        try:
            before, started = utc(self.source.journal.clock()), time.monotonic()
            async with asyncio.timeout(self.timeout_ms / 1000), AsyncExitStack() as stack:
                if self.source._quota is not None:
                    await stack.enter_async_context(self.source._quota.collection(continuation=quota_continuation))
                elif quota_continuation is not None:
                    raise StorageError('PG_SHARED_QUOTA_CONTINUATION_INVALID')
                try:
                    self._original()
                    if type(self.source) is PgObservationAccountSource:
                        await self.source.validate_observation_identity()
                        self._original()
                    opening = (await self._configuration_bracket() if account_brackets else await self.source._uid(force=True))
                    self._original()
                    if account_brackets:
                        self._opening_configuration = opening
                        self._configuration_seal = self.collection_id, opening, None
                    # Quota waiting contains no received account/market fact.
                    self.started_at = opening.observed_at if account_brackets else utc(self.source.journal.clock())
                    yield self
                    self._original()
                    closing = (await self._configuration_bracket() if account_brackets else await self.source._uid(force=True))
                    self._original()
                    if account_brackets:
                        self._closing_configuration = closing
                        self._configuration_seal = self.collection_id, opening, closing
                    if type(self.source) is PgObservationAccountSource:
                        await self.source.validate_observation_identity()
                        self._original()
                finally:
                    if capture is not None:
                        await self._flush_collection(capture, journal)
                # The original observation barrier still covers physical flush.
                # This check and the collector's later native/source comparison
                # precede publication of any qualified source report.
                if capture is not None:
                    self._original()
                after, elapsed = utc(self.source.journal.clock()), time.monotonic() - started
                if after < before or abs((after - before).total_seconds() - elapsed) * 1000 > self.source.max_clock_skew_ms:
                    raise StorageError('PG_ACCOUNT_CLOCK_UNQUALIFIED')
        except BaseException as error:
            runtime._healthy = False
            if isinstance(error, (StorageError, asyncio.CancelledError)):
                raise
            raise StorageError('PG_FINANCIAL_SOURCE_UNAVAILABLE') from None
        finally:
            self._collection_capture = None
            self._active = False
            runtime._observation_inflight -= 1

    async def _capture_pages(self, pages, *, batch=False):
        capture = self._collection_capture
        if capture is None:
            if batch:
                await self._journal.capture_many(pages)
            else:
                await self._journal.capture(pages[0])
            return
        # A request already received under its original signed guard must be
        # retained even if credentials/context were revoked during HTTP. Source
        # validation still rejects that response before publishing any report.
        if capture.task is not asyncio.current_task():
            raise StorageError('PG_FINANCIAL_SOURCE_COLLECTION_ORIGIN')
        if capture.attempted or len(capture.pages) + len(pages) > self.max_reads:
            raise StorageError('PG_FINANCIAL_SOURCE_COLLECTION_BOUND')
        # Store detached immutable wire strings. Consumer parsing cannot mutate
        # the original response or request metadata awaiting diagnostic COMMIT.
        capture.pages.extend(json.dumps(p, ensure_ascii=False, allow_nan=False) for p in pages)

    async def _flush_collection(self, capture, journal):
        if capture.task is not asyncio.current_task() or capture.attempted:
            raise StorageError('PG_FINANCIAL_SOURCE_COLLECTION_ORIGIN')
        capture.attempted = True  # Exactly one attempt, including unknown COMMIT.
        if capture.pages:
            # Original journal/runtime, even after credentials/context refusal.
            # These bytes are diagnostics and cannot authenticate source facts.
            await journal.capture_collection(tuple(json.loads(p) for p in capture.pages))
        if self._collection_capture is not capture or capture.collection_id != self.collection_id:
            raise StorageError('PG_FINANCIAL_SOURCE_COLLECTION_ORIGIN')

    async def _configuration_bracket(self):
        self._original()
        if self._read_active:
            raise StorageError('PG_FINANCIAL_SOURCE_READ_BACKPRESSURE')
        if self._reads >= self.max_reads:
            raise StorageError('PG_FINANCIAL_SOURCE_READ_BOUND')
        self._read_active, self._configuration_task = True, asyncio.current_task()
        try:
            proof = await self.source._uid(force=True, configuration=True)
            await self._capture_configuration(proof)
            return proof
        finally:
            self._read_active, self._configuration_task = False, None

    async def _capture_configuration(self, proof):
        """Retain an original signed UID bracket before exposing source facts."""
        self._original()
        if (type(proof) is not _UidConfiguration or proof.source is not self.source
                or self.source._configuration_proof is not proof or not self.source._uid_fresh()
                or self._configuration_task is not asyncio.current_task() or not self._read_active):
            raise StorageError('PG_FINANCIAL_SOURCE_CONFIGURATION_ORIGIN')
        if self._reads >= self.max_reads:
            raise StorageError('PG_FINANCIAL_SOURCE_READ_BOUND')
        self._reads += 1
        self._bytes += len(proof.body_json.encode('utf-8'))
        if self._bytes > self.max_bytes:
            raise StorageError('PG_FINANCIAL_SOURCE_BYTE_BOUND')
        page = dict(binding=self.binding.json(), owner=self.owner.json(), collection_id=self.collection_id,
                    sequence=self._reads, path=C.OKX_ACCOUNT_CONFIG_PATH, params={}, response_json=proof.body_json)
        await self._capture_pages((page,))
        self._original()
        if self.source._configuration_proof is not proof:
            raise StorageError('PG_FINANCIAL_SOURCE_CONFIGURATION_ORIGIN')
        self._validate_response(json.loads(proof.body_json))

    def account_configuration(self, *, closing=False):
        """Detached original signed bracket; native report waits for flush."""
        proof = self._closing_configuration if closing else self._opening_configuration
        seal = self._configuration_seal
        source, journal, repository, runtime, binding, owner, capture = self._context
        source._credentials()
        if (type(closing) is not bool or type(proof) is not _UidConfiguration or proof.source is not source
                or seal is None or seal[0] != self.collection_id or proof is not seal[2 if closing else 1]
                or self.source is not source or self.source.journal is not journal
                or self.repository is not repository or self.runtime is not runtime or self.binding != binding
                or self.owner != owner or self._journal is not capture or runtime._closing
                or journal.repository is not repository or journal.owner != owner or repository._runtime is not runtime):
            raise StorageError('PG_FINANCIAL_SOURCE_CONFIGURATION_ORIGIN')
        age = (utc(journal.clock()) - proof.observed_at).total_seconds() * 1000
        elapsed = (time.monotonic() - proof.started_monotonic) * 1000
        if (not 0 <= age <= source.max_uid_age_ms or not 0 <= elapsed <= source.max_uid_age_ms
                or abs(age - elapsed) > source.max_clock_skew_ms):
            raise StorageError('PG_FINANCIAL_SOURCE_CONFIGURATION_EXPIRED')
        return json.loads(proof.body_json)

    @classmethod
    def _route(cls, path, params):
        if type(params) is not dict or any(type(key) is not str or type(value) is not str for key, value in params.items()):
            raise StorageError('PG_FINANCIAL_SOURCE_ROUTE_INVALID')
        spot = {
            '/api/v5/public/instruments': {'instType': 'SPOT', 'instId': 'LIFE-USDT'},
            '/api/v5/market/books': {'instId': 'LIFE-USDT', 'sz': '64'},
            '/api/v5/account/trade-fee': {'instType': 'SPOT', 'instId': 'LIFE-USDT'},
        }.get(path)
        if spot is not None and params == spot:
            return
        market = {
            '/api/v5/public/instruments': {'instType': 'SWAP', 'instId': 'LIFE-USDT-SWAP'},
            '/api/v5/market/books': {'instId': 'LIFE-USDT-SWAP', 'sz': '64'},
            '/api/v5/public/mark-price': {'instType': 'SWAP', 'instId': 'LIFE-USDT-SWAP'},
            '/api/v5/market/index-tickers': {'instId': 'LIFE-USDT'},
            '/api/v5/public/funding-rate': {'instId': 'LIFE-USDT-SWAP'},
            '/api/v5/public/position-tiers': {'instType': 'SWAP', 'tdMode': 'cross', 'instFamily': 'LIFE-USDT'},
            '/api/v5/account/trade-fee': {'instType': 'SWAP', 'instFamily': 'LIFE-USDT'},
            '/api/v5/account/leverage-info': {'instId': 'LIFE-USDT-SWAP', 'mgnMode': 'cross'},
        }.get(path)
        if market is not None:
            if params != market:
                raise StorageError('PG_FINANCIAL_SOURCE_ROUTE_INVALID')
            return
        if path in (C.OKX_ORDERS_PENDING_PATH, C.OKX_ORDERS_ALGO_PENDING_PATH) and 'instType' not in params:
            if path == C.OKX_ORDERS_ALGO_PENDING_PATH and params == {'limit': '100', 'ordType': 'chase'}:
                return
            return cls._route(path, {**params, 'instType': 'SPOT'})
        # Normalize only the fixed SWAP instrument/product to the existing
        # exact SPOT query grammar. Account-wide routes retain no instId filter;
        # a per-order history must carry both the original ordId and instrument.
        if (path in (C.OKX_TRADE_FILLS_HISTORY_PATH, C.OKX_ORDERS_PENDING_PATH, C.OKX_ORDERS_ALGO_PENDING_PATH)
                and params.get('instType') == 'SWAP'):
            if 'instId' in params and params['instId'] != 'LIFE-USDT-SWAP':
                raise StorageError('PG_FINANCIAL_SOURCE_ROUTE_INVALID')
            if path == C.OKX_ORDERS_ALGO_PENDING_PATH and params == {'instType': 'SWAP', 'limit': '100', 'ordType': 'chase'}:
                return
            normalized = {**params, 'instType': 'SPOT'}
            if 'instId' in normalized:
                normalized['instId'] = 'LIFE-USDT'
            return cls._route(path, normalized)
        if path == C.OKX_ORDER_DETAILS_PATH and params.get('instId') == 'LIFE-USDT-SWAP':
            return cls._route(path, {**params, 'instId': 'LIFE-USDT'})
        required = {
            C.OKX_ACCOUNT_BILLS_ARCHIVE_PATH: {'limit': '100'},
            C.OKX_TRADE_FILLS_HISTORY_PATH: {'instType': 'SPOT', 'limit': '100'},
            C.OKX_ORDERS_PENDING_PATH: {'instType': 'SPOT', 'limit': '100'},
            C.OKX_ORDERS_ALGO_PENDING_PATH: {'instType': 'SPOT', 'limit': '100'},
            C.OKX_ORDER_DETAILS_PATH: {'instId': 'LIFE-USDT'},
            C.OKX_BALANCE_PATH: {},
            C.OKX_ACCOUNT_CONFIG_PATH: {},
            '/api/v5/account/positions': {},
        }.get(path)
        if required is None or any(params.get(key) != value for key, value in required.items()):
            raise StorageError('PG_FINANCIAL_SOURCE_ROUTE_INVALID')
        extras = set(params) - set(required)
        if path in (C.OKX_ACCOUNT_BILLS_ARCHIVE_PATH, C.OKX_TRADE_FILLS_HISTORY_PATH, C.OKX_ORDERS_PENDING_PATH):
            if 'after' in extras:
                cls._page({}, params['after'])
                extras.remove('after')
            if path == C.OKX_TRADE_FILLS_HISTORY_PATH and extras == {'instId', 'ordId'}:
                if params['instId'] != 'LIFE-USDT':
                    raise StorageError('PG_FINANCIAL_SOURCE_ROUTE_INVALID')
                identifier(params['ordId'])
                extras.clear()
        elif path == C.OKX_ORDERS_ALGO_PENDING_PATH and extras == {'ordType'}:
            if params['ordType'] not in OkxSpotOrderGateway.SPOT_ALGO_TYPES:
                raise StorageError('PG_FINANCIAL_SOURCE_ROUTE_INVALID')
            extras.clear()
        elif path == C.OKX_ORDER_DETAILS_PATH and extras == {'clOrdId'}:
            identifier(params['clOrdId'])
            extras.clear()
        elif path in (C.OKX_ORDERS_ALGO_PENDING_PATH, C.OKX_ORDER_DETAILS_PATH):
            raise StorageError('PG_FINANCIAL_SOURCE_ROUTE_INVALID')
        if extras:
            raise StorageError('PG_FINANCIAL_SOURCE_ROUTE_INVALID')

    async def _read(self, path, params):
        if self._read_active:
            raise StorageError('PG_FINANCIAL_SOURCE_READ_BACKPRESSURE')
        self._read_active = True
        try:
            return await self._read_one(path, params)
        finally:
            self._read_active = False

    async def _read_one(self, path, params):
        result, page = await self._fetch_one(path, params)
        await self._capture_pages((page,))
        self._validate_response(result)
        return result

    async def read_batch(self, routes):
        """At most eight original signed reads, with bounded response retention.

        Default calls commit before returning data. A native joint collector or
        observer may parse internally behind its observation barrier; one collection
        COMMIT precedes report publication. Neither mode batches money commands.
        Partial transport failure retains already received pages.
        """
        if (type(routes) is not tuple or not 0 < len(routes) <= 8 or self._read_active):
            raise StorageError('PG_FINANCIAL_SOURCE_READ_BACKPRESSURE')
        for path, params in routes:
            self._route(path, params)
        if self._reads + len(routes) > self.max_reads:
            raise StorageError('PG_FINANCIAL_SOURCE_READ_BOUND')
        self._read_active = True
        received, pages = [], []
        try:
            try:
                for path, params in routes:
                    result, page = await self._fetch_one(path, params)
                    received.append(result)
                    pages.append(page)
            except BaseException:
                if pages:
                    await self._capture_pages(tuple(pages), batch=True)
                raise
            await self._capture_pages(tuple(pages), batch=True)
            for result in received:
                self._validate_response(result)
            return tuple(received)
        finally:
            self._read_active = False

    async def _fetch_one(self, path, params):
        self._original()
        binding, owner = self._context[4:6]
        self._route(path, params)
        if self._reads >= self.max_reads:
            raise StorageError('PG_FINANCIAL_SOURCE_READ_BOUND')
        self._reads += 1
        sequence, collection_id = self._reads, self.collection_id
        await self.source._uid()
        self._original()
        for attempt in range(2):
            guard = _FinancialReadGuard(self, path, params)
            try:
                result = await self.source._connector._api_get(path_url=path, params=params, is_auth_required=True,
                                                               request_send_guard=guard)
                break
            except _UidReadExpired:
                if attempt or not guard.uid_expired or guard.consumed:
                    raise
                # Only this exact guard's proven unsent GET is retried once.
                # The old request/authentication is discarded. Clock faults,
                # received/unknown HTTP outcomes and all order sends are excluded.
                # The enclosing collection deadline and wire-page cap remain.
                await self.source._uid(force=True)
                self._original()
        if not guard.consumed:
            raise StorageError('PG_FINANCIAL_SOURCE_TRANSPORT_UNQUALIFIED')
        try:
            encoded = json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
            self._bytes += len(encoded.encode('utf-8'))
        except (TypeError, ValueError):
            raise StorageError('PG_FINANCIAL_SOURCE_RESPONSE_INVALID') from None
        if self._bytes > self.max_bytes:
            raise StorageError('PG_FINANCIAL_SOURCE_BYTE_BOUND')
        # Retain received parseable diagnostics before any semantic/context
        # rejection. This is decoded JSON, not raw HTTP bytes or authenticated
        # financial facts. Unknown commit never returns data to the parser.
        return json.loads(encoded), {'binding': binding.json(), 'owner': owner.json(),
                                     'collection_id': collection_id, 'sequence': sequence,
                                     'path': path, 'params': dict(guard.params), 'response_json': encoded}

    def _validate_response(self, result):
        self._original()
        if not self.source._uid_fresh():
            raise StorageError('PG_FINANCIAL_SOURCE_TRANSPORT_UNQUALIFIED')
        if type(result) is not dict or result.get('code') != '0' or type(result.get('data')) is not list:
            raise StorageError('PG_FINANCIAL_SOURCE_RESPONSE_INVALID')

    @staticmethod
    def _page(params, after):
        if after is not None:
            if type(after) is not str or not after.isascii() or not after.isdecimal() or not 0 < len(after) <= 96:
                raise StorageError('PG_FINANCIAL_SOURCE_CURSOR_INVALID')
            params['after'] = after
        return params

    async def get_account_bills_page(self, after=None):
        return await self._read(C.OKX_ACCOUNT_BILLS_ARCHIVE_PATH, self._page({'limit': '100'}, after))

    async def get_all_spot_fill_history_page(self, after=None):
        return await self._read(C.OKX_TRADE_FILLS_HISTORY_PATH, self._page({'instType': 'SPOT', 'limit': '100'}, after))

    async def get_spot_fill_history_page(self, trading_pair, exchange_order_id, after=None):
        if trading_pair != 'LIFE-USDT':
            raise StorageError('PG_FINANCIAL_SOURCE_MARKET_INVALID')
        identifier(exchange_order_id)
        return await self._read(C.OKX_TRADE_FILLS_HISTORY_PATH,
                                self._page({'instType': 'SPOT', 'instId': trading_pair, 'ordId': exchange_order_id, 'limit': '100'}, after))

    async def get_order_by_client_id(self, trading_pair, client_order_id):
        if trading_pair != 'LIFE-USDT':
            raise StorageError('PG_FINANCIAL_SOURCE_MARKET_INVALID')
        identifier(client_order_id)
        return await self._read(C.OKX_ORDER_DETAILS_PATH, {'instId': trading_pair, 'clOrdId': client_order_id})

    async def get_all_open_spot_orders_page(self, after=None):
        return await self._read(C.OKX_ORDERS_PENDING_PATH, self._page({'instType': 'SPOT', 'limit': '100'}, after))

    async def get_all_pending_spot_algo_orders_page(self, ord_type):
        if ord_type not in OkxSpotOrderGateway.SPOT_ALGO_TYPES:
            raise StorageError('PG_FINANCIAL_SOURCE_ALGO_INVALID')
        return await self._read(C.OKX_ORDERS_ALGO_PENDING_PATH, {'ordType': ord_type, 'instType': 'SPOT', 'limit': '100'})

    async def get_all_swap_fill_history_page(self, after=None):
        return await self._read(C.OKX_TRADE_FILLS_HISTORY_PATH, self._page({'instType': 'SWAP', 'limit': '100'}, after))

    async def get_swap_fill_history_page(self, exchange_order_id, after=None):
        identifier(exchange_order_id)
        return await self._read(C.OKX_TRADE_FILLS_HISTORY_PATH,
                                self._page({'instType': 'SWAP', 'instId': 'LIFE-USDT-SWAP',
                                            'ordId': exchange_order_id, 'limit': '100'}, after))

    async def get_swap_order_by_client_id(self, client_order_id):
        identifier(client_order_id)
        return await self._read(C.OKX_ORDER_DETAILS_PATH, {'instId': 'LIFE-USDT-SWAP', 'clOrdId': client_order_id})

    async def get_all_open_swap_orders_page(self, after=None):
        return await self._read(C.OKX_ORDERS_PENDING_PATH, self._page({'instType': 'SWAP', 'limit': '100'}, after))

    async def get_all_open_orders_page(self, after=None):
        # No product/instrument filter: foreign MARGIN/FUTURES/OPTION/EVENTS
        # orders must remain visible even while account positions are zero.
        return await self._read(C.OKX_ORDERS_PENDING_PATH, self._page({'limit': '100'}, after))

    async def get_all_pending_algo_orders_page(self, ord_type):
        if ord_type not in self.SWAP_ALGO_TYPES:
            raise StorageError('PG_FINANCIAL_SOURCE_ALGO_INVALID')
        return await self._read(C.OKX_ORDERS_ALGO_PENDING_PATH, {'ordType': ord_type, 'limit': '100'})

    async def get_all_pending_swap_algo_orders_page(self, ord_type):
        if ord_type not in self.SWAP_ALGO_TYPES:
            raise StorageError('PG_FINANCIAL_SOURCE_ALGO_INVALID')
        return await self._read(C.OKX_ORDERS_ALGO_PENDING_PATH, {'ordType': ord_type, 'instType': 'SWAP', 'limit': '100'})

    async def get_spot_cash_balances(self):
        return await self._read(C.OKX_BALANCE_PATH, {})

    async def get_account_configuration(self):
        return await self._read(C.OKX_ACCOUNT_CONFIG_PATH, {})

    async def get_all_account_positions(self):
        # No instType/instId filter: foreign/isolated exposure must be visible.
        return await self._read('/api/v5/account/positions', {})
