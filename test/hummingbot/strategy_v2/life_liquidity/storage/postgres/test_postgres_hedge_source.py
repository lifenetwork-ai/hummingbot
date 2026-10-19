"""Native hedge observations from signed HTTP, never re-dated fixture DTOs."""
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal as D
from test.hummingbot.core.web_assistant.connections.test_request_guard import Response
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_joint_source import joint

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
from hummingbot.strategy_v2.life_liquidity.storage.postgres.hedge_source import PgHedgeSource


def test_fixed_source_routes_have_actual_sdk_throttle_limits():
    from hummingbot.connector.exchange.okx.okx_constants import RATE_LIMITS
    from hummingbot.core.api_throttler.async_throttler import AsyncThrottler
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.hedge_source import ROUTES
    throttler = AsyncThrottler(RATE_LIMITS)
    for path in [*(path for path, _ in ROUTES), '/api/v5/account/positions']:
        limit, _ = throttler.get_related_limits(path)
        assert limit is not None, path
        assert limit.limit > 0 and limit.time_interval > 0


@pytest.mark.asyncio
async def test_exact_provider_tier_boundary_uses_the_higher_margin(database):
    t = await sources(database)
    try:
        cp = (await t.values[5].frame(t.cfg.binding))['policy']
        policy = (await t.values[8].frame(t.cfg.binding))['policy']
        cp['price_stress_bps'] = '0'
        cp['tiers'] = [dict(max_notional_quote='10', initial_margin_rate='.5', maintenance_margin_rate='.1'),
                       dict(max_notional_quote='1000', initial_margin_rate='.8', maintenance_margin_rate='.2')]
        t.public['position-tiers'] = [dict(instFamily='LIFE-USDT', tier='1', minSz='0', maxSz='40', imr='.5', mmr='.1'),
                                      dict(instFamily='LIFE-USDT', tier='2', minSz='40', maxSz='100000', imr='.8', mmr='.2')]
        from hummingbot.strategy_v2.life_liquidity.storage.postgres.hedge_source import ROUTES
        data = [t.public[path.rsplit('/', 1)[1]] for path, _ in ROUTES]
        with pytest.raises(StorageError, match='PG_HEDGE_SOURCE_TIER_UNDERESTIMATED'):
            t.source._market(data, cp, policy, 1, t.at)
    finally:
        await t.financial.close()


async def sources(database, *, coverage_gap=False, clean=False, fresh=False, target='0'):
    t = (await clean_account(database, fresh=fresh, target=target) if clean else await joint(database, carry_options=None if coverage_gap else
                                                                                             dict(anchor_opening=True)))
    t.at = int(datetime.now(timezone.utc).timestamp() * 1000)
    t.public = {
        'instruments': [dict(instType='SWAP', instId='LIFE-USDT-SWAP', instFamily='LIFE-USDT',
                             state='live', ctType='linear', ctValCcy='LIFE', settleCcy='USDT',
                             ctVal='.25', lotSz='.1', tickSz='.01', minSz='.1', groupId='2')],
        'books': [dict(ts=str(t.at), bids=[['.99', '400', '0', '2']], asks=[['1.01', '400', '0', '2']])],
        'mark-price': [dict(instType='SWAP', instId='LIFE-USDT-SWAP', markPx='1', ts=str(t.at))],
        'index-tickers': [dict(instId='LIFE-USDT', idxPx='1', ts=str(t.at))],
        'funding-rate': [dict(instType='SWAP', instId='LIFE-USDT-SWAP', fundingRate='.001',
                              fundingTime=str(t.at + 3600000), nextFundingTime=str(t.at + 5 * 3600000), ts=str(t.at))],
        'position-tiers': [dict(instFamily='LIFE-USDT', tier='1', minSz='0', maxSz='100000', imr='.5', mmr='.1')],
        'trade-fee': [dict(instType='SWAP', feeGroup=[dict(groupId='2', maker='-.001', taker='-.002')], ts=str(t.at))],
        'leverage-info': [dict(instId='LIFE-USDT-SWAP', mgnMode='cross', posSide=side, lever='2') for side in ('long', 'short')],
    }
    t.public_received_at, t.stamp_public = None, True
    original = t.session.request

    async def http(**kwargs):
        tail = kwargs['url'].rsplit('/', 1)[1]
        if tail not in t.public:
            return await original(**kwargs)
        t.requests.append(kwargs)
        response = Response()

        async def body():
            if t.public_received_at is None:
                # One actual new synthetic provider batch at first HTTP, after
                # unrelated SDK/DB fixture setup. All original batch stamps are
                # then fixed; subsequent reads cannot renew this cached batch.
                t.public_received_at = int(datetime.now(timezone.utc).timestamp() * 1000)
                if t.stamp_public:
                    for rows in t.public.values():
                        for row in rows:
                            if 'ts' in row:
                                row['ts'] = str(t.public_received_at)
            return dict(code='0', data=deepcopy(t.public[tail]))
        response.json = body
        return response
    t.session.request = http
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from hummingbot.connector.derivative.okx_perpetual.okx_perpetual_derivative import OkxPerpetualDerivative
    from hummingbot.connector.trading_rule import TradingRule
    from hummingbot.core.data_type.common import PositionMode
    from hummingbot.core.web_assistant.connections.rest_connection import RESTConnection
    from hummingbot.core.web_assistant.rest_assistant import RESTAssistant
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.swap_send import PgSwapBoundary
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.swap_transport import PgSwapAccountSource
    if not clean:
        await database[0].execute('INSERT INTO life_financial.swap_boundary_policies VALUES($1,$2,1000,64,1048576)', t.cfg.account_id, t.cfg.binding.policy_hash)
        t.swap_connector = OkxPerpetualDerivative(okx_perpetual_api_key='synthetic-key', okx_perpetual_secret_key='synthetic-secret',
                                                  okx_perpetual_passphrase='synthetic-pass', trading_pairs=[], trading_required=False)
        t.swap_connector._contract_sizes['LIFE-USDT'] = D('.25')
        t.swap_connector._perpetual_trading.set_position_mode(PositionMode.HEDGE)
        t.swap_connector._perpetual_trading.set_leverage('LIFE-USDT', 2)
        t.swap_connector._trading_rules['LIFE-USDT'] = TradingRule('LIFE-USDT', min_order_size=D('.025'),
                                                                   min_price_increment=D('.01'), min_base_amount_increment=D('.025'))
        assistant = RESTAssistant(RESTConnection(t.session), t.throttler, auth=t.swap_connector.authenticator)
        t.swap_connector._web_assistants_factory = SimpleNamespace(get_rest_assistant=AsyncMock(return_value=assistant))
        t.swap_source = await PgSwapAccountSource.create(PgSwapBoundary(t.joint.swap), connector=t.swap_connector,
                                                         clock=lambda: datetime.now(timezone.utc), local_fence=lambda: True,
                                                         max_uid_age_ms=1000, max_auth_age_ms=1000, max_clock_skew_ms=100,
                                                         collection_timeout_ms=1000, max_pending=1)
    t.source = PgHedgeSource(t.joint, carry=t.values[8], swap_source=t.swap_source, max_levels=8, timeout_ms=5000,
                             available_edge_quote=D('1') if clean else D('0'))
    if clean:
        t.app.bind_sources(t.source)
    return t


async def clean_account(database, *, fresh=False, target='0'):
    import json
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_collateral_source import bodies
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_hedge_application import application
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from hummingbot.connector.exchange.okx.okx_exchange import OkxExchange
    from hummingbot.core.web_assistant.connections.rest_connection import RESTConnection
    from hummingbot.core.web_assistant.rest_assistant import RESTAssistant
    from hummingbot.strategy_v2.life_liquidity.storage.actions import RunnerActionFacts
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.financial_source import PgSpotAccountReader
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.hedge_application import PgHedgeApplication
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.joint_source import PgJointAccountReconciler
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.maker_cancellation import PgMakerCancellation
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.observation_account_source import (
        PgObservationAccountSource,
    )
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.runner_journal import PgQuoteRunnerJournal
    t = (await fresh_account(database) if fresh else await application(database, carry_options=dict(anchor_opening=True), target=target))
    t.swap_connector, t.throttler = t.connector, t.throttle
    t.connector = OkxExchange('synthetic-key', 'synthetic-secret', 'synthetic-pass', trading_pairs=[], trading_required=False)
    assistant = RESTAssistant(RESTConnection(t.session), t.throttler, auth=t.connector.authenticator)
    t.connector._web_assistants_factory = SimpleNamespace(get_rest_assistant=AsyncMock(return_value=assistant))
    t.connector.exchange_symbol_associated_to_pair = AsyncMock(return_value='LIFE-USDT')
    t.connector._api_request_url = AsyncMock(side_effect=lambda path_url, **_: 'https://www.okx.com' + path_url)
    t.requests = []
    t.config, t.balance, t.positions = bodies(mode='long_short_mode', short='0', cash='10')

    async def facts():
        return RunnerActionFacts(t.cfg.binding, datetime.now(timezone.utc), 'main', (), True)
    journal = PgQuoteRunnerJournal(t.financial, owner=t.owner, controller_id='main', config_version=1,
                                   runner_facts=facts, local_ready=lambda: True, clock=lambda: datetime.now(timezone.utc), max_actions=64)
    account = await PgObservationAccountSource.create(journal, connector=t.connector, max_uid_age_ms=1000,
                                                      max_auth_age_ms=1000, max_clock_skew_ms=100, max_pending=2, collection_timeout_ms=1000)
    reader = PgSpotAccountReader(account, max_reads=128, max_bytes=4194304, timeout_ms=4000)
    t.joint = PgJointAccountReconciler(reader, t.repo.swap, max_pages=2, max_age_ms=1000)

    async def http(**kwargs):
        t.requests.append(kwargs)
        if kwargs['method'] == 'POST':
            t.session.sent.append(kwargs)
        response = Response()

        async def body():
            tail = kwargs['url'].rsplit('/', 1)[1]
            if kwargs['method'] == 'POST':
                request = json.loads(kwargs['data'])
                return dict(code='0', data=[dict(sCode='0', ordId='123456', clOrdId=request['clOrdId'])])
            data = {'config': [t.config], 'balance': [t.balance], 'positions': t.positions,
                    'bills-archive': [dict(billId='100')], 'orders-pending': [], 'orders-algo-pending': [], 'fills-history': []}[tail]
            return dict(code='0', data=deepcopy(data))
        response.json = body
        return response
    t.session.request = http

    async def collect(operation_id):
        return await t.source.collect(operation_id)
    cancellation = PgMakerCancellation(t.joint, paused=lambda: t.paused[0], max_cancels=2,
                                       retry_interval_ms=100, timeout_ms=4000)
    t.app = await PgHedgeApplication.create(t.repo, connector=t.swap_connector, pause_makers=t.pause,
                                            maker_cancellation=cancellation, collect=collect,
                                            local_fence=lambda: t.ready[0], clock=lambda: datetime.now(timezone.utc),
                                            max_uid_age_ms=1000, max_auth_age_ms=1000, max_clock_skew_ms=100,
                                            collection_timeout_ms=1000, cycle_timeout_ms=5000)
    t.swap_source = t.app.source
    return t


async def fresh_account(database):
    import json
    from dataclasses import asdict
    from datetime import timedelta
    from test.hummingbot.core.web_assistant.connections.test_request_guard import Session, Throttler
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_shared_capital import setup
    from test.hummingbot.strategy_v2.life_liquidity.test_shared_capital import policy as capital_policy
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from hummingbot.connector.derivative.okx_perpetual.okx_perpetual_derivative import OkxPerpetualDerivative
    from hummingbot.connector.trading_rule import TradingRule
    from hummingbot.core.data_type.common import PositionMode
    from hummingbot.core.web_assistant.connections.rest_connection import RESTConnection
    from hummingbot.core.web_assistant.rest_assistant import RESTAssistant
    from hummingbot.strategy_v2.life_liquidity.carry_monitor import CarryPolicy
    from hummingbot.strategy_v2.life_liquidity.shared_capital import _json
    from hummingbot.strategy_v2.life_liquidity.storage.contracts import fingerprint
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.carry import PgCarryRepository
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.hedge import PgHedgeRepository
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.swap import PgSwapRepository
    base = await setup(database, configured=capital_policy(max_age_ms=5000))
    financial, cfg, owner, cp, feed, capital = base
    opened = owner.valid_from - timedelta(seconds=1)
    carry_policy = CarryPolicy('12345', 'LIFE-USDT-SWAP', int(opened.timestamp() * 1000), 5000, 500,
                               4 * 3600000, 16 * 3600000, D('.002'), D('10'), D('100'))
    body = _json(asdict(carry_policy))
    await database[0].execute('INSERT INTO life_financial.carry_policies VALUES($1,$2,$3::jsonb,$4,64)',
                              cfg.account_id, cfg.binding.policy_hash, json.dumps(body), fingerprint(dict(policy=body, max_payments=64)))
    carry = PgCarryRepository(capital, observation=lambda: None, max_rows=64, max_bytes=1048576)
    values = (*base, carry_policy, [None], carry)
    sp = dict(controller_id='main', tick_size_usdt='0.01', min_size_contracts='0.1', max_quote_levels=8, max_capture_age_ms=5000)
    await database[0].execute('INSERT INTO life_financial.swap_policies VALUES($1,$2,$3::jsonb,$4)', cfg.account_id, cfg.binding.policy_hash, json.dumps(sp), fingerprint(sp))
    await database[0].execute("INSERT INTO life_financial.spot_capital_policies VALUES($1,$2,5,10,1,$3,'100',4096)", cfg.account_id, cfg.binding.policy_hash, opened)
    await database[0].execute('INSERT INTO life_financial.swap_fill_policies VALUES($1,$2,0,0,0,0,64,128,500)', cfg.account_id, cfg.binding.policy_hash)
    await database[0].execute('INSERT INTO life_financial.swap_boundary_policies VALUES($1,$2,1000,64,1048576)', cfg.account_id, cfg.binding.policy_hash)
    repo = PgHedgeRepository(PgSwapRepository(capital))
    connector = OkxPerpetualDerivative(okx_perpetual_api_key='synthetic-key', okx_perpetual_secret_key='synthetic-secret',
                                       okx_perpetual_passphrase='synthetic-pass', trading_pairs=[], trading_required=False)
    connector._contract_sizes['LIFE-USDT'] = D('.25')
    connector._perpetual_trading.set_position_mode(PositionMode.HEDGE)
    connector._perpetual_trading.set_leverage('LIFE-USDT', 2)
    connector._trading_rules['LIFE-USDT'] = TradingRule('LIFE-USDT', min_order_size=D('.025'), min_price_increment=D('.01'), min_base_amount_increment=D('.025'))
    session, throttle = Session(), Throttler()
    throttle.release.set()
    connector._web_assistants_factory = SimpleNamespace(get_rest_assistant=AsyncMock(return_value=
                                                                                     RESTAssistant(RESTConnection(session), throttle, auth=connector.authenticator)))
    paused, ready = [False], [True]

    def pause():
        paused[0] = True
        return True
    return SimpleNamespace(**locals())


@pytest.mark.asyncio
async def test_fresh_native_policy_starts_from_signed_sources_without_synthetic_seed_observations(database):
    t = await sources(database, clean=True, fresh=True)
    try:
        assert (await t.values[5].frame(t.cfg.binding))['snapshot'] is None
        assert (await t.values[8].frame(t.cfg.binding))['bundle'] is None
        result = await t.app.observe_sources('fresh')
        assert result.capital.decision.allowed and result.carry.decision.allowed
        assert (await t.values[5].frame(t.cfg.binding))['snapshot']['sequence'] == 1
        assert (await t.financial.balance(t.cfg.binding)).total_quote_usdt == 10
        assert not t.session.sent
    finally:
        await t.financial.close()


@pytest.mark.asyncio
async def test_native_source_binding_is_single_install_and_replacement_revokes_original_runtime(database):
    t = await sources(database, clean=True)
    try:
        with pytest.raises(StorageError, match='SOURCE_BINDING_INVALID'):
            t.app.bind_sources(t.source)
        t.source.available_edge_quote = D('100')
        with pytest.raises(StorageError, match='CONTEXT_CHANGED'):
            await t.app.execute('mutated')
        assert not t.financial._runtime.healthy
        assert not t.session.sent
        assert not await t.values[5].claims(t.cfg.binding)
    finally:
        await t.financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('product', ['SPOT', 'SWAP', 'ACK', 'MISSING'])
async def test_foreign_sdk_order_appearing_during_final_throttle_revokes_prepared_hedge(database, product):
    import asyncio

    from hummingbot.core.data_type.common import OrderType, TradeType
    t = await sources(database, clean=True)
    prepared = asyncio.Event()
    original = t.repo.plan

    async def blocked(*args, **kwargs):
        result = await original(*args, **kwargs)
        assert result.child_state == 'PREPARED'
        t.throttler.entered.clear()
        t.throttler.release.clear()
        prepared.set()
        return result
    t.repo.plan = blocked
    try:
        pending = asyncio.create_task(t.app.execute('sdk-scope'))
        await asyncio.wait_for(prepared.wait(), 5)
        await asyncio.wait_for(t.throttler.entered.wait(), 5)
        before = t.financial._runtime._ingress_epoch
        if product == 'ACK':
            t.swap_connector._order_tracker.active_orders[t.app._sdk_child.client_order_id].update_exchange_order_id('999')
        elif product == 'MISSING':
            t.swap_connector._order_tracker.stop_tracking_order(t.app._sdk_child.client_order_id)
        else:
            sdk = t.connector if product == 'SPOT' else t.swap_connector
            sdk.start_tracking_order('foreign', '999', 'LIFE-USDT', TradeType.BUY, D('1'), D('1'), OrderType.LIMIT_MAKER)
        assert t.financial._runtime._ingress_epoch == before  # No SQL/event hint.
        t.throttler.release.set()
        with pytest.raises(StorageError):
            await pending
        assert not t.session.sent
        assert not t.financial._runtime.healthy
        assert await database[0].fetchval('SELECT state FROM life_financial.send_attempts') == 'PREPARED'
        assert len(await t.values[5].claims(t.cfg.binding)) == 1
    finally:
        t.throttler.release.set()
        await t.financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['fill', 'terminal'])
async def test_stopped_typed_application_keeps_late_native_bookkeeping_admissible(database, kind):
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.hedge import SettleHedge
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.portfolio import MeasurePortfolio, PgPortfolioRepository
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.swap_fills import FillSwap, SwapFillObservation
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.swap_terminal import ConfirmSwapTerminal
    t = await sources(database, clean=True)
    try:
        if kind == 'terminal':
            await database[0].execute('INSERT INTO life_financial.portfolio_policies VALUES($1,$2,1,1000,5000,64,1048576)', t.cfg.account_id, t.cfg.binding.policy_hash)
            await database[0].execute('INSERT INTO life_financial.swap_terminal_policies VALUES($1,$2,64,1048576)', t.cfg.account_id, t.cfg.binding.policy_hash)
        root = await t.app.execute('late')
        child = t.app._sdk_child
        t.app.stop()
        now = datetime.now(timezone.utc)
        at = int(now.timestamp() * 1000)
        if kind == 'fill':
            trade = SwapFillObservation('late-trade', D('.5'), D('1.01'), 'USDT', D('-.001'), at,
                                        D('1.01'), at, at, 'independent_market', D('0'), None)
            command = FillSwap('late-fill', child.operation_id, child.config().id, child.client_order_id,
                               'LIFE-USDT', 'SELL', str(t.owner.session_id), t.owner.epoch, '123456', D('.5'), (trade,), now)
            assert (await t.app.record_fills('late', command)).state == 'APPLIED'
            assert (await t.app.record_fills('late', command)).replayed
            assert (await t.financial.balance(t.cfg.binding)).total_quote_usdt == D('9.999')
        else:
            await PgPortfolioRepository(t.repo.swap).measure(MeasurePortfolio('late-nav'),
                                                             cut=(await t.financial.observation_cut(t.cfg.binding)).cut, owner=t.owner)
            prepared_at = await database[0].fetchval('SELECT prepared_at_ms FROM life_financial.swap_preparations WHERE operation_id=$1', root.child_operation_id)
            command = ConfirmSwapTerminal('late-terminal', child.operation_id, '123456', 'canceled', D('0'), (), at,
                                          prepared_at, 'okx_joint_reconciled')
            # A received terminal observation is retained after stop, but the
            # earlier account proof cannot certify a new terminal time. Keep
            # explicit native pending reconciliation and every original claim.
            assert (await t.app.record_terminal('late', command)).state == 'RECOVERY_REQUIRED'
            assert await database[0].fetchval('SELECT reason FROM life_financial.swap_terminal_facts') == 'SWAP_TERMINAL_FINANCIAL_CUT_UNQUALIFIED'
            assert (await t.app.record_terminal('late', command)).replayed
            settled = await t.app.settle(SettleHedge('late-settle', 'late'))
            assert settled.state == 'PARTIAL'
            assert (await t.app.settle(SettleHedge('late-settle', 'late'))).replayed
            assert (await t.financial.balance(t.cfg.binding)).total_quote_usdt == 10
        assert len(t.session.sent) == 1
        assert len(await t.values[5].claims(t.cfg.binding)) == 1
        assert not t.financial._runtime.healthy
        assert (await t.app.execute('late')).replayed
        with pytest.raises(StorageError):
            await t.app.execute('new-risk-after-stop')
        assert len(t.session.sent) == 1
    finally:
        await t.financial.close()


@pytest.mark.asyncio
async def test_typed_unknown_sdk_response_keeps_original_replay_diagnostic_without_another_post(database):
    t = await sources(database, clean=True)
    original = t.session.request

    async def malformed(**kwargs):
        response = await original(**kwargs)
        if kwargs['method'] == 'POST':
            async def body():
                return dict(code='0', data=[])
            response.json = body
        return response
    t.session.request = malformed
    try:
        with pytest.raises(StorageError, match='SDK_SUBMISSION_UNKNOWN'):
            await t.app.execute('unknown')
        assert await database[0].fetchval('SELECT state FROM life_financial.send_attempts') == 'SEND_UNKNOWN'
        assert (await t.app.execute('unknown')).replayed
        assert len(t.session.sent) == 1
        assert len(await t.values[5].claims(t.cfg.binding)) == 1
        with pytest.raises(StorageError):
            await t.app.execute('sibling')
        assert len(t.session.sent) == 1
    finally:
        await t.financial.close()


@pytest.mark.asyncio
async def test_native_signed_source_composes_cancellation_plan_and_actual_sdk_send(database):
    t = await sources(database, clean=True)
    try:
        result = await t.app.execute('qualified-hedge')
        assert result.child_state == 'PREPARED'
        assert len(t.session.sent) == 1
        claim = (await t.values[5].claims(t.cfg.binding))[0]
        assert t.swap_connector._order_tracker.active_orders[claim.wire_id].exchange_order_id == '123456'
        assert await database[0].fetchval('SELECT state FROM life_financial.send_attempts') == 'ACKED'
        assert t.source._qualified[3].available_edge_quote == 1
        assert (await t.app.execute('qualified-hedge')).replayed
        assert len(t.session.sent) == 1
    finally:
        await t.financial.close()


@pytest.mark.asyncio
async def test_existing_native_maker_is_canceled_and_terminal_before_signed_hedge_capture(database):
    import json
    from dataclasses import replace
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_financial_commands import order
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_shared_capital import native_claim

    from hummingbot.core.data_type.common import OrderType, TradeType
    from hummingbot.core.data_type.in_flight_order import OrderState, OrderUpdate
    from hummingbot.strategy_v2.life_liquidity.shared_capital import CapitalSnapshot
    from hummingbot.strategy_v2.life_liquidity.storage.financial import AckSpot
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.hedge import PlanHedge, UpdateHedgeTarget
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.shared_capital import PgSharedCapitalRepository
    from hummingbot.strategy_v2.life_liquidity.storage.products import ReserveCapital
    t = await sources(database, clean=True, target='5')
    try:
        await t.app.observe_sources('opening')
        balanced = await t.repo.plan(PlanHedge.capture('balanced', t.source._qualified[3]),
                                     cut=(await t.financial.snapshot(t.cfg.binding)).cut, owner=t.owner)
        assert balanced.decision['reason_code'] == 'HEDGE_DEADBAND'
        body = (await t.values[5].frame(t.cfg.binding))['snapshot']
        # Seed an existing native maker from the exact already qualified
        # snapshot, without changing its sequence or original timestamps.
        # This reservation/ACK fixture grants no SPOT HTTP submission.
        numbers = ('spot_life_base', 'spot_cash_quote', 'long_base', 'short_base', 'collateral_quote',
                   'initial_margin_quote', 'maintenance_margin_quote', 'funding_liability_quote', 'mark_price_usdt', 'index_price_usdt')
        snapshot = CapitalSnapshot(**{**body, **{k: D(body[k]) for k in numbers}, 'pending': ()})
        capital = PgSharedCapitalRepository(t.financial, owner=t.owner, observation=lambda: snapshot, max_rows=64, max_bytes=1048576)
        claim = replace(native_claim(t.owner, 'intent1', amount='2'), wire_id='wire1')
        assert (await capital.reserve(ReserveCapital('existing-capital', claim), await capital.cut(t.cfg.binding), t.owner)).decision.allowed
        # The carried native snapshot remains unchanged; reserve its original
        # prospective claim through the same explicit native carry kernel.
        from hummingbot.strategy_v2.life_liquidity.storage.postgres.carry import PgCarryRepository
        from hummingbot.strategy_v2.life_liquidity.storage.postgres.funding import ApplyFunding
        bundle = (await t.values[8].frame(t.cfg.binding))['bundle']
        decoded = ApplyFunding.from_json(dict(operation_id='decode-only', bundle=bundle)).bundle
        carry = PgCarryRepository(capital, observation=lambda: decoded, max_rows=64, max_bytes=1048576)
        assert (await carry.check_carry(t.cfg.binding)).allowed
        prepared = order(t.owner, risk_epoch=1, fee_buffer_base_life=D('0'), fee_buffer_quote_usdt=D('.002'))
        await t.financial.prepare(prepared, cut=(await t.financial.snapshot(t.cfg.binding)).cut, owner=t.owner)
        await t.financial.arm(prepared.operation_id, cut=(await t.financial.snapshot(t.cfg.binding)).cut, owner=t.owner)
        now = datetime.now(timezone.utc)
        await t.financial.acknowledge(AckSpot('existing-ack', prepared.operation_id, 'intent1', 'wire1', 'LIFE-USDT', 'BUY',
                                              str(t.owner.session_id), t.owner.epoch, '4321', now),
                                      cut=(await t.financial.snapshot(t.cfg.binding)).cut, owner=t.owner)
        t.connector.start_tracking_order('wire1', '4321', 'LIFE-USDT', TradeType.BUY, D('1'), D('2'), OrderType.LIMIT_MAKER)
        state = ['live']
        provider_order = dict(instType='SPOT', instId='LIFE-USDT', clOrdId='wire1', ordId='4321',
                              state='live', side='buy', sz='2', accFillSz='0', px='1', tdMode='cash', ordType='post_only')
        original = t.session.request

        async def server(**kwargs):
            tail = kwargs['url'].rsplit('/', 1)[1]
            if tail not in ('order', 'orders-pending', 'cancel-order') or tail == 'order' and kwargs['method'] == 'POST':
                return await original(**kwargs)
            response = Response()
            if tail == 'cancel-order':
                t.session.sent.append(kwargs)
                assert json.loads(kwargs['data']) == dict(instId='LIFE-USDT', clOrdId='wire1')
                native = await t.financial.attempt('intent1', t.cfg.binding)
                assert native.cancel_requested and native.cancel_attempts == 1
                state[0] = 'canceled'
                await t.connector._order_tracker._process_order_update(OrderUpdate(trading_pair='LIFE-USDT',
                                                                                   update_timestamp=datetime.now(timezone.utc).timestamp(), new_state=OrderState.CANCELED,
                                                                                   client_order_id='wire1', exchange_order_id='4321'))

            async def body():
                data = [dict(sCode='0', clOrdId='wire1')] if tail == 'cancel-order' else (
                    [{**provider_order, 'state': state[0]}] if tail == 'order' or state[0] == 'live' else [])
                return dict(code='0', data=data)
            response.json = body
            return response
        t.session.request = server
        await t.repo.update_target(UpdateHedgeTarget('target-zero', 0, D('0')),
                                   cut=(await t.financial.snapshot(t.cfg.binding)).cut, owner=t.owner)
        # The next actual synthetic market batch starts only on its first HTTP
        # read. No prior provider timestamp is renewed in a retained report.
        t.public_received_at = None
        root = await t.app.execute('after-maker')
        assert root.child_state == 'PREPARED'
        assert await database[0].fetchval("SELECT state FROM life_financial.send_attempts WHERE product='SPOT'") == 'TERMINAL'
        assert await database[0].fetchval("SELECT state FROM life_financial.send_attempts WHERE product='SWAP'") == 'ACKED'
        assert len(t.session.sent) == 2 and not t.connector._order_tracker.active_orders
        assert len(await t.values[5].claims(t.cfg.binding)) == 2  # No implicit shared release.
        assert (await t.financial.balance(t.cfg.binding)).total_quote_usdt == 10
    finally:
        await t.financial.close()


@pytest.mark.asyncio
async def test_carry_anchor_must_be_inside_authenticated_bill_coverage(database):
    t = await sources(database, coverage_gap=True)
    try:
        with pytest.raises(StorageError, match='FUNDING_COVERAGE_GAP'):
            await t.source.refresh('gap')
        assert not t.financial._runtime.healthy
    finally:
        await t.financial.close()


@pytest.mark.asyncio
async def test_signed_capture_refreshes_exact_native_account_without_counting_funding_twice(database):
    t = await sources(database)
    try:
        before = await t.financial.snapshot(t.cfg.binding)
        result = await t.source.refresh('capture1')
        assert result.capital.decision.allowed and result.carry.decision.allowed
        assert result.market.bids == ((D('.99'), D('100')),)
        assert result.market.fee_rate == D('.002')
        assert result.market.available_edge_quote == 0  # No invented economic edge.
        frame = await t.values[5].frame(t.cfg.binding)
        assert D(frame['snapshot']['spot_cash_quote']) == D('9.89')
        assert D(frame['snapshot']['collateral_quote']) == D('9.9')
        assert D(frame['snapshot']['funding_liability_quote']) == 0
        carry = await t.values[8].frame(t.cfg.binding)
        assert carry['bundle']['funding']['payments'][0]['bill_id'] == '777'
        after = await t.financial.snapshot(t.cfg.binding)
        assert after.balance == before.balance and after.budget == before.budget
        assert await database[0].fetchval('SELECT halted FROM life_financial.accounts')
        assert not result.trading_permission
        public = [r for r in t.requests if r['url'].rsplit('/', 1)[1] in t.public]
        assert len(public) == 8 and all(r['headers']['OK-ACCESS-SIGN'] for r in public)
        with pytest.raises(StorageError, match='ORIGINAL_OPERATION'):
            await t.source.refresh('capture1')
        assert len([r for r in t.requests if r['url'].rsplit('/', 1)[1] in t.public]) == 8
    finally:
        await t.financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['stale', 'crossed', 'fee_group', 'rebate', 'contract', 'tier', 'funding', 'foreign',
                                   'sdk', 'swap_sdk', 'sdk_leverage', 'sdk_contract', 'credentials', 'exchange_leverage', 'missing_leverage'])
async def test_unqualified_market_or_scope_cannot_refresh_native_capacity(database, fault):
    t = await sources(database)
    if fault == 'stale':
        t.stamp_public = False
        t.public['books'][0]['ts'] = str(t.at - 6000)
    elif fault == 'crossed':
        t.public['books'][0]['bids'][0][0] = '1.02'
    elif fault == 'fee_group':
        t.public['trade-fee'][0]['feeGroup'][0]['groupId'] = 'foreign'
    elif fault == 'rebate':
        t.public['trade-fee'][0]['feeGroup'][0]['maker'] = '1'
        t.public['trade-fee'][0]['feeGroup'][0]['taker'] = '1'
    elif fault == 'contract':
        t.public['instruments'][0]['ctVal'] = '1'
    elif fault == 'tier':
        t.public['position-tiers'][0]['imr'] = '.9'
    elif fault == 'funding':
        t.public['funding-rate'][0]['nextFundingTime'] = str(t.at)
    elif fault == 'foreign':
        t.pending['SWAP'] = [dict(clOrdId='foreign')]
    elif fault in ('sdk', 'swap_sdk'):
        from hummingbot.core.data_type.common import OrderType, TradeType
        sdk = t.connector if fault == 'sdk' else t.swap_connector
        sdk.start_tracking_order('foreign', '999', 'LIFE-USDT', TradeType.BUY, D('1'), D('1'), OrderType.LIMIT_MAKER)
    elif fault == 'sdk_leverage':
        t.swap_connector._perpetual_trading.set_leverage('LIFE-USDT', 3)
    elif fault == 'sdk_contract':
        t.swap_connector._contract_sizes['LIFE-USDT'] = D('1')
    elif fault == 'exchange_leverage':
        t.public['leverage-info'][0]['lever'] = '10'
    elif fault == 'missing_leverage':
        t.public['leverage-info'].pop()
    else:
        t.swap_connector.okx_perpetual_api_key = 'changed'
    try:
        before = await t.values[5].frame(t.cfg.binding)
        if fault == 'rebate':
            result = await t.source.refresh('rebates')
            assert result.market.fee_rate == 0 and result.market.available_edge_quote == 0
        else:
            with pytest.raises(StorageError):
                await t.source.refresh('denied')
            assert (await t.values[5].frame(t.cfg.binding)) == before
            assert not t.financial._runtime.healthy
    finally:
        await t.financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('mark,collateral', [('1.005', '9.895'), ('.995', '9.9')])
async def test_stale_positive_upl_and_new_unreceived_mark_gains_cannot_finance_a_hedge(database, mark, collateral):
    t = await sources(database)
    t.public['mark-price'][0]['markPx'] = mark
    try:
        result = await t.source.refresh('repriced')
        assert result.capital.decision.allowed
        snapshot = (await t.values[5].frame(t.cfg.binding))['snapshot']
        assert D(snapshot['collateral_quote']) == D(collateral)
        assert D(snapshot['spot_cash_quote']) == D('9.89')
        assert (await t.financial.balance(t.cfg.binding)).total_quote_usdt == D('9.89')
    finally:
        await t.financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['stop', 'other_ingress', 'commit_unknown'])
async def test_refresh_interruption_retains_original_observation_and_never_grants_a_child(database, fault):
    import asyncio

    from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown, fingerprint
    t = await sources(database)
    entered, release = asyncio.Event(), asyncio.Event()
    original = t.values[5]._command

    async def blocked(*args, **kwargs):
        entered.set()
        await release.wait()
        return await original(*args, **kwargs)
    t.values[5]._command = blocked
    commit = t.financial._runtime._commit

    async def uncertain(transaction):
        await commit(transaction)
        raise ConnectionError('synthetic lost COMMIT response')
    try:
        pending = asyncio.create_task(t.source.refresh('interrupted'))
        await asyncio.wait_for(entered.wait(), 5)
        if fault == 'stop':
            t.source.stop()
        elif fault == 'other_ingress':
            t.financial._runtime._ingress_epoch += 1
        else:
            t.financial._runtime._commit = uncertain
        release.set()
        with pytest.raises(StorageError) as error:
            await pending
        if fault == 'commit_unknown':
            assert isinstance(error.value, CommitUnknown)
            operation = 'hedge-source-capital:' + fingerprint(['interrupted'])
            assert (await t.values[5].lookup(operation, t.cfg.binding)).replayed
        assert not t.financial._runtime.healthy
        assert not t.session.sent
        assert (await t.financial.balance(t.cfg.binding)).total_quote_usdt == D('9.89')
        with pytest.raises(StorageError):
            await t.source.refresh('different-id')
    finally:
        release.set()
        t.financial._runtime._commit = commit
        await t.financial.close()


@pytest.mark.asyncio
async def test_market_provider_route_grammar_has_exact_sql_and_python_parity(database):
    import json

    from hummingbot.strategy_v2.life_liquidity.storage.postgres.financial_source import PgSpotAccountReader
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.hedge_source import ROUTES
    for path, params in ROUTES:
        PgSpotAccountReader._route(path, dict(params))
        assert await database[0].fetchval('SELECT life_financial.spot_provider_route($1,$2::jsonb)', path, json.dumps(params))
        for wrong in ({}, {**params, 'after': '1'}, {**params, next(iter(params)): 'foreign'}):
            with pytest.raises(StorageError):
                PgSpotAccountReader._route(path, wrong)
            assert not await database[0].fetchval('SELECT life_financial.spot_provider_route($1,$2::jsonb)', path, json.dumps(wrong))
