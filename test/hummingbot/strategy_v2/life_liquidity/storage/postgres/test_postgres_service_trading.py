"""Configured controller/V2/protected SDK path, disposable DB and fake HTTP."""
import asyncio
import json
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from test.hummingbot.core.web_assistant.connections.test_request_guard import Response
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_service import limits
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_spot_market import market
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_spot_producer import policy
from types import SimpleNamespace

import pytest

from controllers.generic.life_liquidity import LifeLiquidityConfig
from hummingbot.core.data_type.common import TradeType
from hummingbot.core.data_type.order_book import OrderBook
from hummingbot.core.data_type.order_book_row import OrderBookRow
from hummingbot.data_feed.market_data_provider import MarketDataProvider
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError, fingerprint
from hummingbot.strategy_v2.life_liquidity.storage.postgres.service import PgLiquidityService
from hummingbot.strategy_v2.life_liquidity.storage.recorder_events import EventSource


def trading_policy():
    # Explicit synthetic calibration, never a live default.
    return dict(reference_policy=dict(max_age_ms=1000, min_life_depth_base='1', min_source_depth_quote='1',
                                      max_deviation_bps='100', max_influence='0', min_correlation='0', min_correlation_samples=3),
                life_anchor_usdt='1', model_version='synthetic-service-v1', max_observation_skew_ms=1000,
                max_depth_distance_bps='500', available_edge_quote_usdt='1', quote_schedule_timeout_ms=30000)


async def configured(database, *, model_change=None, shared_quota=True, maker_cycle=False, spot_overrides=None,
                     hedge_policy=None, independent_fill_value=None, omit_financial_policy=None, strategy_changes=None,
                     configuration_extra=None, provisional_quotes=False, **options):
    template = LifeLiquidityConfig(id='main', storage_backend='postgres')
    template.strategy = type(template.strategy).model_validate(dict(
        template.strategy.model_dump(mode='json'),
        perpetual=dict(enabled=True, position_mode='HEDGE', margin_mode='cross', leverage=2)))
    if strategy_changes is not None:
        template.strategy = type(template.strategy).model_validate(dict(
            template.strategy.model_dump(mode='json'), **strategy_changes))
    registered = tuple(EventSource('main', 'life.yml', 'strategy_v2', m) for m in ('okx', 'okx_perpetual'))

    def configuration(binding):
        model = trading_policy()
        if hedge_policy is not None:
            model['hedge_enabled'] = True
        if model_change is not None:
            model_change(model)
        return dict(controller=template.model_copy(update={'storage_binding': binding, 'recovery_account_uid': binding.account_uid}).model_dump(mode='json'),
                    recorder_sources=[asdict(s) for s in registered], spot_quote_producer=policy(), native_trading=model,
                    **(configuration_extra or {}))
    spot_options = dict(config_hash=fingerprint(template.strategy.model_dump(mode='json')), safety_size=2)
    if maker_cycle:
        # A multi-generation workflow has one explicitly longer synthetic lease
        # at initial seed, not an extension of any captured owner or feed fact.
        spot_options.update(owner_duration=timedelta(seconds=120), session_duration=timedelta(seconds=180))
    spot_options.update(spot_overrides or {})
    t = await market(database, clean=True, shared_quota=shared_quota, stream_market=True, configuration_body=configuration,
                     spot_options=spot_options)
    config = template.model_copy(update={'storage_binding': t.cfg.binding, 'recovery_account_uid': t.cfg.binding.account_uid})
    connectors = dict(okx=t.connector, okx_perpetual=t.swap_connector)
    # Two possible fee currencies must jointly fit the imported .001 hold.
    t.spot['trade-fee'][0]['feeGroup'][0]['maker'] = '-.0005'
    t.connector.enable_protected_trading_pair('LIFE-USDT')
    # SDK budget validation runs normally against these explicit fake balances.
    t.connector._account_balances.update(LIFE=D('5'), USDT=D('10'))
    t.connector._account_available_balances.update(LIFE=D('5'), USDT=D('10'))
    book = OrderBook()
    book.apply_snapshot([OrderBookRow(.99, 4, 1)], [OrderBookRow(1.01, 4, 1)], 1)
    t.connector.order_book_tracker._order_books['LIFE-USDT'] = book
    await database[0].execute('INSERT INTO life_financial.spot_fill_policies VALUES($1,$2,1000,4096)',
                              t.cfg.account_id, t.cfg.binding.policy_hash)
    if omit_financial_policy != 'funding':
        await database[0].execute('INSERT INTO life_financial.funding_accounting_policies VALUES($1,$2,64,128,1048576)',
                                  t.cfg.account_id, t.cfg.binding.policy_hash)
    if omit_financial_policy not in ('funding', 'joint'):
        await database[0].execute('INSERT INTO life_financial.joint_batch_policies VALUES($1,$2,32,64,1048576)',
                                  t.cfg.account_id, t.cfg.binding.policy_hash)
    if maker_cycle or hedge_policy is not None:
        # Explicit synthetic imported policy; service startup creates no policy.
        await database[0].execute('INSERT INTO life_financial.portfolio_policies VALUES($1,$2,1,1000,5000,64,1048576)',
                                  t.cfg.account_id, t.cfg.binding.policy_hash)
        await database[0].execute('INSERT INTO life_financial.shared_release_policies VALUES($1,$2,64,1048576)',
                                  t.cfg.account_id, t.cfg.binding.policy_hash)
    if hedge_policy is not None:
        from bidict import bidict

        from hummingbot.strategy_v2.life_liquidity.shared_capital import _json
        body = _json(asdict(hedge_policy))
        await database[0].execute('INSERT INTO life_financial.hedge_policies VALUES($1,$2,$3::jsonb,$4,64,128,1048576)',
                                  t.cfg.account_id, t.cfg.binding.policy_hash, json.dumps(body), fingerprint(body))
        await database[0].execute('INSERT INTO life_financial.swap_terminal_policies VALUES($1,$2,64,1048576)',
                                  t.cfg.account_id, t.cfg.binding.policy_hash)
        t.swap_connector.enable_protected_trading_pair('LIFE-USDT')
        t.swap_connector._set_trading_pair_symbol_map(bidict({'LIFE-USDT-SWAP': 'LIFE-USDT'}))
    http, orders = t.session.request, []
    t.service_orders = orders

    async def exchange(**kwargs):
        for sdk in connectors.values():
            sdk._set_current_timestamp(datetime.now(timezone.utc).timestamp())
        tail = kwargs['url'].rsplit('/', 1)[1]
        if kwargs['method'] == 'POST' and tail == 'order':
            body = json.loads(kwargs['data'])
            swap = body['instId'] == 'LIFE-USDT-SWAP'
            order = dict(instType='SWAP' if swap else 'SPOT', instId=body['instId'], clOrdId=body['clOrdId'],
                         ordId=str(123456 + len(orders)), state='live', side=body['side'], sz=body['sz'], px=body['px'],
                         accFillSz='0', tdMode=body['tdMode'], ordType=body['ordType'])
            if swap:
                order.update(posSide=body['posSide'], lever='2', reduceOnly=False)
            orders.append(order)
            t.requests.append(kwargs)
            response = Response()

            async def ack():
                return dict(code='0', data=[dict(sCode='0', ordId=orders[-1]['ordId'], clOrdId=body['clOrdId'])])
            response.json = ack
            return response
        elif tail in ('order', 'orders-pending'):
            t.requests.append(kwargs)
            response = Response()

            async def data():
                params = kwargs.get('params') or {}
                rows = [o for o in orders if o['state'] in ('live', 'partially_filled')] if tail == 'orders-pending' else orders
                if tail == 'orders-pending':
                    rows = sorted(rows, key=lambda o: int(o['ordId']), reverse=True)
                if params.get('clOrdId'):
                    rows = [o for o in rows if o['clOrdId'] == params['clOrdId']]
                for key in ('instType', 'instId'):
                    if params.get(key):
                        rows = [o for o in rows if o[key] == params[key]]
                return dict(code='0', data=rows)
            response.json = data
            return response
        response = await http(**kwargs)
        if maker_cycle and tail == 'books' and (kwargs.get('params') or {}).get('instId') == 'LIFE-USDT':
            original_book = response.json

            async def public_book():
                data = await original_book()
                # New fake public snapshots include actual standing own depth.
                # The production builder must subtract it before valuation.
                for side, key in (('buy', 'bids'), ('sell', 'asks')):
                    levels = {D(v[0]): D(v[1]) for v in data['data'][0][key]}
                    for order in orders:
                        if order['instType'] == 'SPOT' and order['side'] == side and order['state'] in ('live', 'partially_filled'):
                            price = D(order['px'])
                            levels[price] = levels.get(price, D('0')) + D(order['sz']) - D(order['accFillSz'])
                    data['data'][0][key] = [[str(p), str(q), '0', '2'] for p, q in sorted(levels.items(), reverse=side == 'buy')]
                return data
            response.json = public_book
        return response
    t.session.request = exchange
    if provisional_quotes:
        await database[0].execute('INSERT INTO life_financial.quote_birth_policies VALUES($1,$2,64,1048576)', t.cfg.account_id, t.cfg.binding.policy_hash)
    await t.financial.close()
    service = await PgLiquidityService.open(
        config, runtime_config=t.cfg, owner=t.owner, connectors=connectors,
        market_data_provider=MarketDataProvider(connectors), sources=registered, limits=limits(),
        clock=t.reader.source.journal.clock, independent_fill_value=independent_fill_value or (lambda *args: None),
        independent_cashflow_value=lambda *args: None, native_trading=True, provisional_quotes=provisional_quotes, **options)
    return t, service


@pytest.mark.asyncio
async def test_disabled_spot_cannot_open_the_native_dual_product_trading_profile(database):
    service = None
    try:
        # The disabled value is imported into the original controller JSON and
        # config hash before seeding. This is not a mutated post-start object.
        with pytest.raises(StorageError, match='PG_SERVICE_ASSEMBLY_INVALID'):
            _, service = await configured(database, strategy_changes={'spot': {'enabled': False}})
        for table in ('send_attempts', 'shared_capital_claims', 'quote_actions', 'hedge_plans'):
            assert await database[0].fetchval('SELECT count(*) FROM life_financial.' + table) == 0
    finally:
        if service is not None:
            await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('side', [TradeType.BUY, TradeType.SELL])
async def test_configured_native_quote_uses_real_runner_executor_and_persists_ack_once(database, side):
    t, service = await configured(database)
    try:
        action = await service.quote('configured-first', side, 0)
        executor, = service.runner.executor_orchestrator.active_executors['main']
        assert executor.config == action.executor_config
        assert executor._order.order_id == service.quote_source.wire_for_action(action)
        assert executor._order.order.exchange_order_id == '123456'
        assert (await service.financial.attempt('configured-first', t.cfg.binding)).state.value == 'ACKED'
        assert len([r for r in t.requests if r['method'] == 'POST']) == 1
        assert not service.reporting._failures and service.reporting.ready
        # Neither a durable claim nor equal action bytes grant a second direct
        # orchestrator call, even in the original process.
        assert not await service.reporting.before_dispatch(action)
        with pytest.raises(StorageError, match='DISPATCH_ORIGIN'):
            service.runner.executor_orchestrator.execute_action(action.model_copy(deep=True))
        assert len(service.runner.executor_orchestrator.active_executors['main']) == 1
        assert len([r for r in t.requests if r['method'] == 'POST']) == 1
        # Equal config is not original executor ownership.
        active = service.runner.executor_orchestrator.active_executors['main']
        active[0] = SimpleNamespace(config=executor.config.model_copy(deep=True))
        try:
            with pytest.raises(StorageError, match='UNQUALIFIED_EXECUTOR'):
                await service.controller.runner_facts()
            assert service._revoked and not executor.is_active
        finally:
            active[0] = executor
    finally:
        await service.close()
        for executor in service.runner.executor_orchestrator.active_executors['main']:
            assert executor._control_loop_task.done()
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_native_quote_replay_never_allocates_another_claim_or_sends_again(database):
    t, service = await configured(database)
    try:
        await service.quote('permanent-one', TradeType.BUY, 0)
        before = await service.financial.snapshot(t.cfg.binding)
        with pytest.raises(StorageError, match='INTENT_USED'):
            await service.quote('permanent-one', TradeType.BUY, 0)
        assert (await service.financial.snapshot(t.cfg.binding)).balance == before.balance
        assert len([r for r in t.requests if r['method'] == 'POST']) == 1
        assert service._revoked and all(not e.is_active for e in service.runner.executor_orchestrator.active_executors['main'])
    finally:
        await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['stop', 'cancel'])
async def test_native_quote_interrupted_original_source_never_dispatches_or_posts(database, fault):
    t, service = await configured(database)
    entered, release = asyncio.Event(), asyncio.Event()
    http = t.session.request

    async def held(**kwargs):
        if kwargs['url'].endswith('/market/books'):
            entered.set()
            await release.wait()
        return await http(**kwargs)
    t.session.request = held
    task = asyncio.create_task(service.quote('interrupted', TradeType.BUY, 0))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        if fault == 'stop':
            service.revoke()
        else:
            task.cancel()
        release.set()
        with pytest.raises((StorageError, asyncio.CancelledError)):
            await task
        assert not service.runner.executor_orchestrator.active_executors['main']
        assert not service.journal._entries
        assert not [r for r in t.requests if r['method'] == 'POST']
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        await service.close()


@pytest.mark.asyncio
async def test_unconfigured_native_model_cannot_install_the_trading_profile(database):
    with pytest.raises(StorageError, match='TRADING_CONFIG_INVALID'):
        await configured(database, model_change=lambda model: model.pop('quote_schedule_timeout_ms'))


@pytest.mark.asyncio
async def test_configured_native_service_requires_the_original_shared_sdk_quota(database):
    with pytest.raises(StorageError, match='SHARED_QUOTA_REQUIRED'):
        await configured(database, shared_quota=False)


@pytest.mark.asyncio
async def test_native_mode_revocation_after_claim_denies_http_and_retains_the_original_claim(database, monkeypatch):
    t, service = await configured(database)
    publication = ('capture_and_publish' if any(m.version == 98 for m in service.runtime._bundle) else 'publish')
    publish = getattr(service.journal, publication)

    async def draining(*args, **kwargs):
        result = await publish(*args, **kwargs)
        await database[0].execute("UPDATE life_financial.accounts SET mode='DRAINING'")
        return result
    monkeypatch.setattr(service.journal, publication, draining)
    try:
        with pytest.raises(StorageError, match='DISPATCH_REJECTED'):
            await service.quote('revoked', TradeType.BUY, 0)
        assert service._revoked and not [r for r in t.requests if r['method'] == 'POST']
        assert await service.financial.attempt('revoked', t.cfg.binding) is None
        assert any(c.intent_id == 'revoked' for c in await service.source.capital.claims(t.cfg.binding))
    finally:
        # The privileged out-of-band origin mutation also prevents publication
        # of diagnostic reports under the former actor. Do not erase it.
        with pytest.raises(StorageError, match='RECORDER_ORIGIN_INVALID'):
            await service.close()


@pytest.mark.asyncio
async def test_native_service_cancelled_close_keeps_db_until_late_original_ack(database):
    t, service = await configured(database)
    http, entered, release = t.session.request, asyncio.Event(), asyncio.Event()

    async def held(**kwargs):
        if kwargs['method'] == 'POST':
            entered.set()
            await release.wait()
        return await http(**kwargs)
    t.session.request = held
    quote = asyncio.create_task(service.quote('late-ack', TradeType.BUY, 0))
    closing = None
    try:
        await asyncio.wait_for(entered.wait(), 25)
        closing = asyncio.create_task(service.close())
        await asyncio.sleep(.01)
        closing.cancel()
        with pytest.raises(asyncio.CancelledError):
            await closing
        assert service._close_task is not None and not service._close_task.done()
        assert not service.runtime._closing
        release.set()
        await quote
        await service.close()
        assert await database[0].fetchval("SELECT state FROM life_financial.send_attempts WHERE intent_id='late-ack'") == 'ACKED'
        assert service._close_task.done() and not service.runtime._tasks
        assert len([r for r in t.requests if r['method'] == 'POST']) == 1
    finally:
        release.set()
        await asyncio.gather(quote, return_exceptions=True)
        if closing is not None:
            await asyncio.gather(closing, return_exceptions=True)
        await service.close()
