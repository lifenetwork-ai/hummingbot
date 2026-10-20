"""Native quote market provenance through signed SDK reads, fake HTTP only."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal as D
from test.hummingbot.core.web_assistant.connections.test_request_guard import Response
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_hedge_source import sources

import pytest

from hummingbot.connector.trading_rule import TradingRule
from hummingbot.strategy_v2.life_liquidity.reference import ReferenceEngine, ReferencePolicy
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot_market import PgSpotMarketSource


async def market(database, *, spot_fee=None, shared_quota=False, clean=False, objective='profit_mm'):
    t = await sources(database, spot_fee=spot_fee, clean=clean or shared_quota, fresh=clean or shared_quota, shared_quota=shared_quota)
    t.reader = t.source.reader
    await database[0].execute("INSERT INTO life_financial.spot_action_policies VALUES($1,$2,'main',8,65536,1000)", t.cfg.account_id, t.cfg.binding.policy_hash)
    await database[0].execute("INSERT INTO life_financial.spot_quote_admission_policies VALUES($1,$2,$3,0,0,.01,.01,.01,1000,1000,1000,1000)", t.cfg.account_id, t.cfg.binding.policy_hash, objective)
    t.connector._trading_rules['LIFE-USDT'] = TradingRule(
        'LIFE-USDT', min_order_size=D('.01'), min_price_increment=D('.01'), min_base_amount_increment=D('.01'))
    t.spot = {
        'instruments': [dict(instType='SPOT', instId='LIFE-USDT', baseCcy='LIFE', quoteCcy='USDT',
                             state='live', tickSz='.01', lotSz='.01', minSz='.01', groupId='1')],
        'books': [dict(ts='', seqId=1, bids=[['.99', '4', '0', '2'], ['.98', '6', '0', '2']],
                       asks=[['1.01', '4', '0', '2'], ['1.02', '6', '0', '2']])],
        'trade-fee': [dict(instType='SPOT', feeGroup=[dict(groupId='1', maker='-.001', taker='-.002')], ts='')],
    }
    original = t.session.request
    t.spot_at = None

    async def http(**kwargs):
        tail, params = kwargs['url'].rsplit('/', 1)[1], kwargs.get('params') or {}
        if tail not in t.spot or (params.get('instType') != 'SPOT' and params.get('instId') != 'LIFE-USDT'):
            return await original(**kwargs)
        t.requests.append(kwargs)
        response = Response()

        async def body():
            if t.spot_at is None:
                # A new synthetic batch at first receipt; never date an old
                # response at the parser or on subsequent reads.
                t.spot_at = int(datetime.now(timezone.utc).timestamp() * 1000)
                for rows in t.spot.values():
                    for row in rows:
                        if row.get('ts') == '':
                            row['ts'] = str(t.spot_at)
            return dict(code='0', data=deepcopy(t.spot[tail]))
        response.json = body
        return response
    t.session.request = http
    engine = ReferenceEngine(ReferencePolicy(1000, D('1'), D('1'), D('100'), D('0'), D('0'), 3),
                             life_anchor_usdt=D('1'), model_version='synthetic-market-v1',
                             life_source_id='okx:LIFE-USDT', source_anchors_usdt={})
    t.market_source = await PgSpotMarketSource.create(t.source, reference_engine=engine,
                                                      max_observation_skew_ms=1000, max_depth_distance_bps=D('500'),
                                                      max_levels=64, timeout_ms=5000)
    return t


@pytest.mark.asyncio
async def test_signed_market_capture_retains_prices_fees_and_account_without_money_permission(database):
    t = await market(database)
    try:
        before = await t.financial.snapshot(t.cfg.binding)
        capture = await t.market_source.collect()
        assert capture.trading_permission is False
        assert capture.reference.price_usdt == D('1')
        assert capture.fees.maker_cost_rate == D('.001')
        assert capture.fees.taker_cost_rate == D('.002')
        assert capture.book.exchange_timestamp_ms == t.spot_at
        assert capture.report.spot_market_json is not None
        assert (await t.financial.snapshot(t.cfg.binding)).cut == before.cut
        assert not [r for r in t.requests if r['method'] != 'GET']
    finally:
        await t.financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('rounding_ms', [D('.5'), D('101')])
async def test_native_policy_json_stamp_obeys_configured_clock_skew_including_millisecond_rounding(database, monkeypatch, rounding_ms):
    from datetime import timedelta
    t = await market(database)
    s = t.market_source
    clock, read = s._now, s.repository.quote_admission_policy
    starts = []

    def now():
        value = clock()
        starts.append(value)
        return value

    async def rounded(binding):
        value = await read(binding)
        return replace(value, observed_at=starts[0] - timedelta(microseconds=int(rounding_ms * 1000)))
    monkeypatch.setattr(s, '_now', now)
    monkeypatch.setattr(s.repository, 'quote_admission_policy', rounded)
    try:
        if rounding_ms < 1:
            assert (await s._policy_snapshot()).policy == s._policy
        else:
            with pytest.raises(StorageError, match='POLICY_UNQUALIFIED'):
                await s._policy_snapshot()
        assert s.reader.source.max_clock_skew_ms == 100
    finally:
        await t.financial.close()


@pytest.mark.asyncio
async def test_fresh_joint_account_uses_original_shared_sdk_quota_and_waits_before_capturing(database):
    t = await market(database, shared_quota=True)
    try:
        quota = t.source.reader.source._quota
        assert quota is not None and quota is t.swap_source._quota
        # Consume actual SDK UID capacity before a new source collection. The
        # owning quota wait precedes all timestamps/cuts in the new collection.
        await t.connector.get_account_uid()
        await t.swap_connector.get_account_uid()
        capture = await t.market_source.collect()
        assert capture.reference.price_usdt == 1
        assert t.source.reader.source._quota is quota
        assert t.swap_source._quota is quota
        assert capture.report.coverage_from_ms < int(capture.report.observed_at.timestamp() * 1000)
        assert not t.session.sent
    finally:
        await t.financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('side,expected', [('BUY', D('.9866666666666666666666666666')),
                                           ('SELL', D('1.0133333333333333333333333334'))])
async def test_unwind_uses_opposite_executable_depth_with_adverse_rounding_and_original_stamps(database, side, expected):
    t = await market(database)
    try:
        capture = await t.market_source.collect()
        facts = t.market_source.admission(capture, side=side, quantity_base=D('6'),
                                          carry_cost_usdt=D('.1'), inventory_risk_usdt=D('.2'))
        assert facts.exit_value_usdt == expected
        assert facts.exit_value_includes_impact and facts.impact_cost_usdt == 0
        assert (facts.carry_cost_usdt, facts.inventory_risk_usdt) == (D('.1'), D('.2'))
        assert facts.reference_price_usdt == 1
        assert facts.maker_fee_rate == D('.001') and facts.exit_fee_rate == D('.002')
        assert facts.observed_at == capture.received_at
        assert int(facts.reference_at.timestamp() * 1000) == int(facts.exit_value_at.timestamp() * 1000) == t.spot_at
        repeated = t.market_source.admission(capture, side=side, quantity_base=D('6'),
                                             carry_cost_usdt=D('.1'), inventory_risk_usdt=D('.2'))
        assert repeated == facts  # no timestamp renewal without a new read
    finally:
        await t.financial.close()


def live_spot(t):
    from hummingbot.core.data_type.common import OrderType, TradeType
    t.spot_order['state'] = 'partially_filled'
    t.pending['SPOT'] = [t.spot_order]
    t.connector.start_tracking_order(order_id='wire1', exchange_order_id='123456789', trading_pair='LIFE-USDT',
                                     trade_type=TradeType.BUY, price=D('1'), amount=D('2'), order_type=OrderType.LIMIT_MAKER)
    t.connector._order_tracker.active_orders['wire1'].executed_amount_base = D('1')
    t.spot['books'][0]['bids'].insert(0, ['1', '1', '0', '1'])


@pytest.mark.asyncio
async def test_complete_signed_own_order_is_subtracted_from_reference_and_unwind_not_public_post_only_book(database):
    t = await market(database, spot_fee='USDT')
    live_spot(t)
    try:
        capture = await t.market_source.collect()
        assert capture.book.bid == 1
        assert capture.reference.price_usdt == 1  # independent .99 / 1.01
        assert capture.independent_bids[0] == (D('.99'), D('4'))
        assert all(p != 1 for p, _ in capture.independent_bids)
        facts = t.market_source.admission(capture, side='BUY', quantity_base=D('1'),
                                          carry_cost_usdt=D('0'), inventory_risk_usdt=D('0'))
        assert facts.best_bid_usdt == 1 and facts.exit_value_usdt == D('.99')
    finally:
        await t.financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['future', 'stale', 'fee_stale', 'group', 'duplicate_group', 'fee_nan', 'wrong_currency',
                                   'wrong_product', 'not_live', 'rule', 'sdk_rule', 'unsorted', 'crossed', 'empty',
                                   'off_tick', 'off_lot', 'overflow', 'sequence', 'missing_fee', 'duplicate_instrument'])
async def test_unqualified_market_terms_never_become_quote_facts_or_orders(database, fault):
    t = await market(database)
    instrument, book, fees = t.spot['instruments'][0], t.spot['books'][0], t.spot['trade-fee'][0]
    now = int(datetime.now(timezone.utc).timestamp() * 1000)
    if fault in ('future', 'stale'):
        book['ts'] = str(now + (10000 if fault == 'future' else -10000))
    elif fault == 'fee_stale':
        fees['ts'] = str(now - 10000)
    elif fault == 'group':
        fees['feeGroup'][0]['groupId'] = 'foreign'
    elif fault == 'duplicate_group':
        fees['feeGroup'].append(deepcopy(fees['feeGroup'][0]))
    elif fault == 'fee_nan':
        fees['feeGroup'][0]['maker'] = 'NaN'
    elif fault == 'wrong_currency':
        instrument['quoteCcy'] = 'BTC'
    elif fault == 'wrong_product':
        instrument['instType'] = 'SWAP'
    elif fault == 'not_live':
        instrument['state'] = 'preopen'
    elif fault == 'rule':
        instrument['tickSz'] = '.001'
    elif fault == 'sdk_rule':
        t.connector._trading_rules['LIFE-USDT'].min_price_increment = D('.001')
    elif fault == 'unsorted':
        book['bids'].reverse()
    elif fault == 'crossed':
        book['bids'][0][0] = '1.02'
    elif fault == 'empty':
        book['asks'] = []
    elif fault == 'off_tick':
        book['bids'][0][0] = '.991'
    elif fault == 'off_lot':
        book['bids'][0][1] = '4.001'
    elif fault == 'overflow':
        book['bids'][0][1] = '1' + '0' * 40
    elif fault == 'sequence':
        book.pop('seqId')
    elif fault == 'missing_fee':
        fees.pop('feeGroup')
    else:
        t.spot['instruments'].append(deepcopy(instrument))
    try:
        before = await t.financial.snapshot(t.cfg.binding)
        with pytest.raises(StorageError):
            await t.market_source.collect()
        assert not t.financial._runtime.healthy
        assert (await t.financial.snapshot(t.cfg.binding)).cut == before.cut
        assert not [r for r in t.requests if r['method'] != 'GET']
    finally:
        await t.financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['depth', 'side', 'capture_copy', 'expired', 'ingress', 'engine', 'policy',
                                   'spot_credentials', 'swap_credentials', 'swap_auth', 'sdk_order', 'sdk_rule'])
async def test_capture_cannot_be_reused_after_depth_context_identity_or_age_failure(database, fault):
    import asyncio

    from hummingbot.core.data_type.common import OrderType, TradeType
    t = await market(database)
    try:
        capture = await t.market_source.collect()
        if fault == 'expired':
            await asyncio.sleep(1.05)
        elif fault == 'ingress':
            t.financial._runtime._ingress_epoch += 1
        elif fault == 'engine':
            t.market_source.engine.life_anchor_usdt = D('2')
        elif fault == 'policy':
            t.market_source._policy = replace(t.market_source._policy, max_fact_age_ms=60000)
        elif fault == 'spot_credentials':
            t.connector.okx_api_key = 'substituted-synthetic-key'
        elif fault == 'swap_credentials':
            t.swap_connector.okx_perpetual_api_key = 'substituted-synthetic-key'
        elif fault == 'swap_auth':
            t.swap_connector._auth = t.swap_connector.authenticator
        elif fault == 'sdk_rule':
            t.connector._trading_rules['LIFE-USDT'].min_order_size = D('.1')
        elif fault == 'sdk_order':
            t.connector.start_tracking_order(order_id='foreign', exchange_order_id='foreign', trading_pair='LIFE-USDT',
                                             trade_type=TradeType.BUY, price=D('1'), amount=D('1'), order_type=OrderType.LIMIT_MAKER)
        with pytest.raises(StorageError):
            t.market_source.admission(replace(capture) if fault == 'capture_copy' else capture,
                                      side='INVALID' if fault == 'side' else 'BUY',
                                      quantity_base=D('11') if fault == 'depth' else D('1'),
                                      carry_cost_usdt=D('0'), inventory_risk_usdt=D('0'))
        if fault == 'depth':
            assert t.financial._runtime.healthy
            assert t.market_source.admission(capture, side='BUY', quantity_base=D('1'),
                                             carry_cost_usdt=D('0'), inventory_risk_usdt=D('0')).exit_value_usdt == D('.99')
        else:
            assert not t.financial._runtime.healthy
    finally:
        await t.financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['missing_sdk', 'own_only', 'own_exceeds_public', 'sdk_fill'])
async def test_own_order_ambiguity_denies_independent_depth(database, fault):
    t = await market(database, spot_fee='USDT')
    live_spot(t)
    if fault == 'missing_sdk':
        t.connector.stop_tracking_order('wire1')
    elif fault == 'own_only':
        t.spot['books'][0]['bids'] = [t.spot['books'][0]['bids'][0]]
    elif fault == 'own_exceeds_public':
        t.spot['books'][0]['bids'][0][1] = '.5'
    else:
        t.connector._order_tracker.active_orders['wire1'].executed_amount_base = D('.5')
    try:
        with pytest.raises(StorageError):
            await t.market_source.collect()
        assert not t.financial._runtime.healthy
    finally:
        await t.financial.close()


def test_market_routes_have_real_sdk_limits_and_deny_query_widening():
    from hummingbot.connector.exchange.okx.okx_constants import RATE_LIMITS
    from hummingbot.core.api_throttler.async_throttler import AsyncThrottler
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.financial_source import PgSpotAccountReader
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot_market import ROUTES
    throttle = AsyncThrottler(RATE_LIMITS)
    for path, params in ROUTES:
        assert throttle.get_related_limits(path)[0] is not None
        PgSpotAccountReader._route(path, params)
        for bad in ({**params, 'extra': '1'}, {**params, 'instId': 'BTC-USDT'}):
            with pytest.raises(StorageError, match='ROUTE_INVALID'):
                PgSpotAccountReader._route(path, bad)


@pytest.mark.asyncio
async def test_market_sql_routes_match_exact_python_allowlist_without_granting_financial_permissions(database):
    import json

    from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot_market import ROUTES
    c = database[0]
    for path, params in ROUTES:
        assert await c.fetchval('SELECT life_financial.spot_provider_route($1,$2::jsonb)', path, json.dumps(params))
        for bad in ({**params, 'extra': '1'}, {**params, 'instId': 'BTC-USDT'}, {**params, 'instType': None}):
            assert not await c.fetchval('SELECT life_financial.spot_provider_route($1,$2::jsonb)', path, json.dumps(bad))
    assert not await c.fetchval("SELECT life_financial.spot_provider_route('/api/v5/trade/order', '{\"instId\":\"LIFE-USDT\"}'::jsonb)")


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['ingress', 'lost_diagnostic_commit', 'cancel', 'policy_change'])
async def test_collection_does_not_publish_on_interruption_unknown_commit_or_changed_native_context(database, fault, monkeypatch):
    import asyncio

    from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown
    t = await market(database)
    original_http, original_capture = t.session.request, t.reader._journal.capture_many

    async def http(**kwargs):
        response = await original_http(**kwargs)
        if kwargs['url'].endswith('/market/books'):
            if fault == 'ingress':
                t.financial._runtime._ingress_epoch += 1
            elif fault == 'cancel':
                raise asyncio.CancelledError()
            elif fault == 'policy_change':
                await database[0].execute('UPDATE life_financial.spot_quote_admission_policies SET tick_size=.001 WHERE account_id=$1', t.cfg.account_id)
        return response

    async def lost(pages):
        receipt = await original_capture(pages)
        if pages[0]['path'] == '/api/v5/public/instruments':
            raise CommitUnknown('PG_COMMIT_UNKNOWN')
        return receipt

    t.session.request = http
    monkeypatch.setattr(t.reader._journal, 'capture_many', lost if fault == 'lost_diagnostic_commit' else original_capture)
    try:
        with pytest.raises((StorageError, asyncio.CancelledError)):
            await t.market_source.collect()
        assert t.market_source._capture is None
        assert not t.reader._active and not t.market_source._busy
        assert not t.financial._runtime.healthy
        assert not [r for r in t.requests if r['method'] != 'GET']
        if fault == 'lost_diagnostic_commit':
            pages = await t.reader._journal.read_collection(t.reader.collection_id, max_rows=128, max_bytes=4194304)
            assert any(v.body['path'] == '/api/v5/market/books' for v in pages)
    finally:
        await t.financial.close()
