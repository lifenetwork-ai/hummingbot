"""Pinned signed OKX private reads for explicit SPOT financial collection.

No arbitrary URL/method/params, sender, activation or terminal release. Bounds
limit collection work/accepted decoded JSON; they are not an HTTP streaming cap.
"""
import asyncio
import json
import time
from contextlib import asynccontextmanager

from hummingbot.connector.exchange.okx import okx_constants as C
from hummingbot.core.web_assistant.connections.data_types import RESTMethod
from hummingbot.strategy_v2.life_liquidity.order_gateway import OkxSpotOrderGateway
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError, identifier, utc
from hummingbot.strategy_v2.life_liquidity.storage.postgres.observation_account_source import PgObservationAccountSource
from hummingbot.strategy_v2.life_liquidity.storage.postgres.quote_account_source import PgQuoteAccountSource


class _FinancialReadGuard:
    def __init__(self, reader, path, params):
        self.reader, self.params = reader, tuple(params.items())
        self.url = reader.source._domain.rstrip('/') + path
        self.request, self.started, self.checked, self.consumed = None, False, False, False

    def _validate(self, request):
        self.reader._original()
        self.reader.source._request(request, RESTMethod.GET, self.url, params=self.params)
        if request.data is not None or not self.reader.source._uid_fresh():
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

    def _original(self):
        self.source._credentials()
        if (not self._active or self.source.journal.repository is not self.repository
                or self.repository._runtime is not self.runtime or self.source.journal.binding != self.binding
                or self.repository.binding != self.binding or self.source.journal.owner != self.owner or self.runtime._closing):
            raise StorageError('PG_FINANCIAL_SOURCE_CONTEXT_CHANGED')

    @asynccontextmanager
    async def collection(self):
        if self._active:
            raise StorageError('PG_FINANCIAL_SOURCE_BACKPRESSURE')
        self._active, self._reads, self._bytes = True, 0, 0
        self.runtime._ingress_epoch += 1
        self.runtime._observation_inflight += 1
        try:
            before, started = utc(self.source.journal.clock()), time.monotonic()
            async with asyncio.timeout(self.timeout_ms / 1000):
                self._original()
                if type(self.source) is PgObservationAccountSource:
                    await self.source.validate_observation_identity()
                    self._original()
                await self.source._uid(force=True)
                self._original()
                yield self
                self._original()
                await self.source._uid(force=True)
                self._original()
                if type(self.source) is PgObservationAccountSource:
                    await self.source.validate_observation_identity()
                    self._original()
                after, elapsed = utc(self.source.journal.clock()), time.monotonic() - started
                if after < before or abs((after - before).total_seconds() - elapsed) * 1000 > self.source.max_clock_skew_ms:
                    raise StorageError('PG_ACCOUNT_CLOCK_UNQUALIFIED')
        except BaseException as error:
            self.runtime._healthy = False
            if isinstance(error, (StorageError, asyncio.CancelledError)):
                raise
            raise StorageError('PG_FINANCIAL_SOURCE_UNAVAILABLE') from None
        finally:
            self._active = False
            self.runtime._observation_inflight -= 1

    @classmethod
    def _route(cls, path, params):
        if type(params) is not dict or any(type(key) is not str or type(value) is not str for key, value in params.items()):
            raise StorageError('PG_FINANCIAL_SOURCE_ROUTE_INVALID')
        required = {
            C.OKX_ACCOUNT_BILLS_ARCHIVE_PATH: {'limit': '100'},
            C.OKX_TRADE_FILLS_HISTORY_PATH: {'instType': 'SPOT', 'limit': '100'},
            C.OKX_ORDERS_PENDING_PATH: {'instType': 'SPOT', 'limit': '100'},
            C.OKX_ORDERS_ALGO_PENDING_PATH: {'instType': 'SPOT', 'limit': '100'},
            C.OKX_ORDER_DETAILS_PATH: {'instId': 'LIFE-USDT'},
            C.OKX_BALANCE_PATH: {},
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
        self._original()
        self._route(path, params)
        if self._reads >= self.max_reads:
            raise StorageError('PG_FINANCIAL_SOURCE_READ_BOUND')
        self._reads += 1
        await self.source._uid()
        self._original()
        guard = _FinancialReadGuard(self, path, params)
        result = await self.source._connector._api_get(path_url=path, params=params, is_auth_required=True,
                                                       request_send_guard=guard)
        self._original()
        if not guard.consumed or not self.source._uid_fresh():
            raise StorageError('PG_FINANCIAL_SOURCE_TRANSPORT_UNQUALIFIED')
        try:
            self._bytes += len(json.dumps(result, ensure_ascii=False, allow_nan=False).encode('utf-8'))
        except (TypeError, ValueError):
            raise StorageError('PG_FINANCIAL_SOURCE_RESPONSE_INVALID') from None
        if self._bytes > self.max_bytes:
            raise StorageError('PG_FINANCIAL_SOURCE_BYTE_BOUND')
        if type(result) is not dict or result.get('code') != '0' or type(result.get('data')) is not list:
            raise StorageError('PG_FINANCIAL_SOURCE_RESPONSE_INVALID')
        return result

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

    async def get_spot_cash_balances(self):
        return await self._read(C.OKX_BALANCE_PATH, {})
