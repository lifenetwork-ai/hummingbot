"""Signed, bounded SWAP reads for complete shared-product reconciliation."""
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_financial_source import private_http
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_quote_account_source import source
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_quote_transport import transport

import pytest

from hummingbot.connector.exchange.okx import okx_constants as C
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
from hummingbot.strategy_v2.life_liquidity.storage.postgres.financial_source import PgSpotAccountReader
from hummingbot.strategy_v2.life_liquidity.storage.postgres.provider_journal import PgProviderJournal


@pytest.mark.asyncio
@pytest.mark.parametrize('method,args,params', [
    ('get_all_swap_fill_history_page', ('123',), {'instType': 'SWAP', 'limit': '100', 'after': '123'}),
    ('get_swap_fill_history_page', ('456', '123'), {'instType': 'SWAP', 'instId': 'LIFE-USDT-SWAP', 'ordId': '456', 'limit': '100', 'after': '123'}),
    ('get_swap_order_by_client_id', ('wire1',), {'instId': 'LIFE-USDT-SWAP', 'clOrdId': 'wire1'}),
    ('get_all_open_swap_orders_page', (), {'instType': 'SWAP', 'limit': '100'}),
    ('get_all_open_swap_orders_page', ('123',), {'instType': 'SWAP', 'limit': '100', 'after': '123'}),
    ('get_all_open_orders_page', (), {'limit': '100'}),
    ('get_all_open_orders_page', ('123',), {'limit': '100', 'after': '123'}),
    *[('get_all_pending_swap_algo_orders_page', (kind,), {'instType': 'SWAP', 'limit': '100', 'ordType': kind})
      for kind in PgSpotAccountReader.SWAP_ALGO_TYPES],
    *[('get_all_pending_algo_orders_page', (kind,), {'limit': '100', 'ordType': kind})
      for kind in PgSpotAccountReader.SWAP_ALGO_TYPES],
])
async def test_signed_swap_reads_keep_exact_product_query_and_original_diagnostic_pages_without_send_authority(database, method, args, params):
    t = await transport(database)
    requests = private_http(t)
    try:
        reader = PgSpotAccountReader(await source(t), max_reads=4, max_bytes=1048576, timeout_ms=1000)
        before = await t.repo.snapshot(t.cfg.binding)
        async with reader.collection():
            assert await getattr(reader, method)(*args) == {'code': '0', 'data': []}
        pages = await PgProviderJournal(t.repo).read_collection(reader.collection_id, max_rows=4, max_bytes=1048576)
        assert len(pages) == 1 and pages[0].body['params'] == params
        assert not pages[0].trading_permission
        assert (await t.repo.snapshot(t.cfg.binding)).balance == before.balance
        assert (await t.repo.snapshot(t.cfg.binding)).budget == before.budget
        assert len(requests) == 3 and len(t.session.sent) == 0
        assert all(row['data'] is None and not row['allow_redirects'] for row in requests)
        assert all(row['headers']['OK-ACCESS-KEY'] == 'synthetic-key' for row in requests)
    finally:
        await t.close()


@pytest.mark.parametrize('path,params', [
    (C.OKX_ORDERS_PENDING_PATH, {'instType': 'SWAP', 'limit': '100', 'instId': 'LIFE-USDT-SWAP'}),
    (C.OKX_ORDERS_PENDING_PATH, {'instType': 'FUTURES', 'limit': '100'}),
    (C.OKX_TRADE_FILLS_HISTORY_PATH, {'instType': 'SWAP', 'limit': '100', 'instId': 'LIFE-USDT', 'ordId': '123'}),
    (C.OKX_TRADE_FILLS_HISTORY_PATH, {'instType': 'SWAP', 'limit': '100', 'instId': 'BTC-USDT-SWAP', 'ordId': '123'}),
    (C.OKX_ORDERS_ALGO_PENDING_PATH, {'instType': 'SPOT', 'limit': '100', 'ordType': 'chase'}),
    (C.OKX_ORDER_DETAILS_PATH, {'instId': 'LIFE-USDT-SWAP', 'clOrdId': 'wire1', 'foo': 'bar'}),
])
def test_joint_routes_cannot_hide_foreign_pending_orders_or_mix_product_units(path, params):
    with pytest.raises(StorageError, match='PG_FINANCIAL_SOURCE_ROUTE_INVALID'):
        PgSpotAccountReader._route(path, params)
