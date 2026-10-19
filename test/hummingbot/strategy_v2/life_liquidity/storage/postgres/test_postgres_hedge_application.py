"""Hedge application composition over native journals and fake signed HTTP."""
import asyncio
import hashlib
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal as D
from test.hummingbot.core.web_assistant.connections.test_request_guard import Response, Session, Throttler
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_hedge import child, setup
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from hummingbot.connector.derivative.okx_perpetual.okx_perpetual_derivative import OkxPerpetualDerivative
from hummingbot.connector.trading_rule import TradingRule
from hummingbot.core.data_type.common import PositionMode
from hummingbot.core.web_assistant.connections.rest_connection import RESTConnection
from hummingbot.core.web_assistant.rest_assistant import RESTAssistant
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
from hummingbot.strategy_v2.life_liquidity.storage.postgres.hedge import PlanHedge, SettleHedge
from hummingbot.strategy_v2.life_liquidity.storage.postgres.hedge_application import PgHedgeApplication


async def application(database, *, carry_options=None, target='0'):
    values, repo, market, policy = await setup(database, carry_options=carry_options, target=target)
    financial, cfg, owner = values[:3]
    await database[0].execute('INSERT INTO life_financial.swap_boundary_policies VALUES($1,$2,1000,64,1048576)', cfg.account_id, cfg.binding.policy_hash)
    connector = OkxPerpetualDerivative(okx_perpetual_api_key='synthetic-key', okx_perpetual_secret_key='synthetic-secret',
                                       okx_perpetual_passphrase='synthetic-pass', trading_pairs=[], trading_required=False)
    connector.enable_protected_trading_pair('LIFE-USDT')
    connector._contract_sizes['LIFE-USDT'] = D('.25')
    connector._perpetual_trading.set_position_mode(PositionMode.HEDGE)
    connector._perpetual_trading.set_leverage('LIFE-USDT', 2)
    connector._trading_rules['LIFE-USDT'] = TradingRule('LIFE-USDT', min_order_size=D('.025'),
                                                        min_price_increment=D('.01'), min_base_amount_increment=D('.025'),
                                                        buy_order_collateral_token='USDT', sell_order_collateral_token='USDT')
    connector.exchange_symbol_associated_to_pair = AsyncMock(return_value='LIFE-USDT-SWAP')
    session, throttle = Session(), Throttler()
    throttle.release.set()
    connector._web_assistants_factory = SimpleNamespace(get_rest_assistant=AsyncMock(return_value=
                                                                                     RESTAssistant(RESTConnection(session), throttle, auth=connector.authenticator)))
    events, ready, paused = [], [True], [False]
    sequence = [0]
    response_body = [dict(code='0', data=[dict(sCode='0', ordId='123456', clOrdId='HedgeWireone')])]

    async def send(**kwargs):
        session.sent.append(kwargs)
        response = Response()

        async def body():
            return dict(code='0', data=[dict(uid='12345')]) if kwargs['url'].endswith('/account/config') else response_body[0]
        response.json = body
        return response
    session.request = send

    def pause():
        paused[0] = True
        events.append('pause')
        return True

    async def cancel():
        events.append('cancel')
        return True

    async def collect(operation_id):
        from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_portfolio import refresh
        events.append('collect')
        assert paused[0]
        await refresh(database, values)
        at = int((await database[0].fetchval('SELECT clock_timestamp()')).timestamp() * 1000)
        sequence[0] += 1
        captured = await child(database, values, key='one' if operation_id == 'plan1' else operation_id)
        return PlanHedge.capture(operation_id, replace(market, sequence=sequence[0], observed_at_ms=at), child=captured)

    app = await PgHedgeApplication.create(
        repo, connector=connector, pause_makers=pause, cancel_makers=cancel,
        collect=collect, local_fence=lambda: ready[0], clock=lambda: datetime.now(timezone.utc),
        max_uid_age_ms=1000, max_auth_age_ms=1000, max_clock_skew_ms=250,
        collection_timeout_ms=1000, cycle_timeout_ms=5000)
    return SimpleNamespace(**locals())


@pytest.mark.asyncio
async def test_hedge_application_cancels_collects_prepares_and_sends_original_child_once(database):
    t = await application(database)
    try:
        result = await t.app.execute('plan1')
        assert result.child_state == 'PREPARED' and not result.trading_permission
        assert t.events == ['pause', 'cancel', 'collect']
        posts = [r for r in t.session.sent if r['method'] == 'POST']
        assert len(posts) == 1
        import json
        wire = json.loads(posts[0]['data'])
        assert (wire['clOrdId'], D(wire['sz']), D(wire['px']), wire['posSide']) == ('HedgeWireone', D('8'), D('1.01'), 'short')
        saved = await t.app.source.boundary.lookup(result.child_operation_id, t.cfg.binding)
        assert saved.wire_sha256 == hashlib.sha256(posts[0]['data']).hexdigest()
        assert await database[0].fetchval('SELECT state FROM life_financial.send_attempts') == 'ACKED'
        tracked = t.connector._order_tracker.active_orders['HedgeWireone']
        assert tracked.exchange_order_id == '123456' and tracked.amount == 2 and tracked.leverage == 2
        assert (await t.app.execute('plan1')).replayed
        assert len([r for r in t.session.sent if r['method'] == 'POST']) == 1
        assert t.events == ['pause', 'cancel', 'collect']
        assert len(await t.values[5].claims(t.cfg.binding)) == 1
        assert (await t.financial.balance(t.cfg.binding)).total_quote_usdt == 10
    finally:
        await t.financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('phase', ['cancel', 'collect', 'throttle'])
async def test_stop_during_hedge_await_never_sends_or_drops_original_claim(database, phase):
    t = await application(database)
    entered, release = asyncio.Event(), asyncio.Event()
    original = getattr(t.app, '_' + phase, None)
    if phase == 'throttle':
        t.throttle.release.clear()
    else:
        async def blocked(*args):
            entered.set()
            await release.wait()
            return await original(*args)
        setattr(t.app, '_' + phase, blocked)
    try:
        pending = asyncio.create_task(t.app.execute('plan1'))
        if phase == 'throttle':
            await t.throttle.entered.wait()
        else:
            await entered.wait()
        t.app.stop()
        release.set()
        t.throttle.release.set()
        with pytest.raises((StorageError, ValueError)):
            await pending
        assert not [r for r in t.session.sent if r['method'] == 'POST']
        claims = await t.values[5].claims(t.cfg.binding)
        assert len(claims) == (1 if phase == 'throttle' else 0)
        assert (await t.financial.balance(t.cfg.binding)).total_quote_usdt == 10
    finally:
        await t.financial.close()


@pytest.mark.asyncio
async def test_uncertain_hedge_response_keeps_original_and_cold_replay_cannot_retry(database):
    t = await application(database)
    t.response_body[0] = dict(code='0', data=[])
    try:
        with pytest.raises(StorageError, match='SDK_SUBMISSION_UNKNOWN'):
            await t.app.execute('plan1')
        assert await database[0].fetchval('SELECT state FROM life_financial.send_attempts') == 'SEND_UNKNOWN'
        assert (await t.repo.lookup('plan1', t.cfg.binding)).child_state == 'PREPARED'
        assert len(await t.values[5].claims(t.cfg.binding)) == 1
        assert (await t.app.execute('plan1')).replayed
        assert len([r for r in t.session.sent if r['method'] == 'POST']) == 1
        with pytest.raises(StorageError):
            await t.app.execute('sibling')
        assert len(await t.values[5].claims(t.cfg.binding)) == 1
    finally:
        await t.financial.close()


@pytest.mark.asyncio
async def test_late_partial_fills_and_settlement_replay_exact_original_without_refund(database):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_portfolio import refresh

    from hummingbot.strategy_v2.life_liquidity.storage.postgres.swap_fills import FillSwap, SwapFillObservation
    t = await application(database)
    try:
        root = await t.app.execute('plan1')
        t.app.stop()
        now = await database[0].fetchval('SELECT clock_timestamp()')
        at = int(now.timestamp() * 1000)
        trade = SwapFillObservation('hedge-trade1', D('.5'), D('1.01'), 'USDT', D('-.001'), at,
                                    D('1.01'), at, at, 'independent_market', D('0'), None)
        fill = FillSwap('hedge-fill', 'hedge-prepare:one', 'hedge-one', 'HedgeWireone', 'LIFE-USDT', 'SELL',
                        str(t.owner.session_id), t.owner.epoch, '123456', D('.5'), (trade,), now)
        assert (await t.app.record_fills('plan1', fill)).state == 'APPLIED'
        await refresh(database, t.values, short='.5', cash='9.999')
        settled = await t.app.settle(SettleHedge('settle1', 'plan1'))
        assert settled.state == 'PARTIAL' and not settled.trading_permission
        assert (await t.app.record_fills('plan1', fill)).replayed
        assert (await t.app.settle(SettleHedge('settle1', 'plan1'))).replayed
        with pytest.raises(StorageError, match='OPERATION_CONFLICT'):
            await t.app.settle(SettleHedge('settle1', 'other'))
        with pytest.raises(StorageError, match='OPERATION_CONFLICT'):
            await t.app.record_fills('plan1', replace(fill, observed_at=now.replace(microsecond=0)))
        frame = await t.repo.frame(t.cfg.binding)
        assert D(frame['plans'][0]['progress']['cost_hold']) == D(root.decision['estimated_cost_quote'])
        assert await database[0].fetchval('SELECT incurred_usdt FROM life_financial.hedge_plans') == D('.001')
        assert len(await t.values[5].claims(t.cfg.binding)) == 1
        assert (await t.financial.balance(t.cfg.binding)).total_quote_usdt == D('9.999')
        assert len([r for r in t.session.sent if r['method'] == 'POST']) == 1
    finally:
        await t.financial.close()


@pytest.mark.asyncio
async def test_stop_after_preparation_retains_child_without_http_or_reissued_send(database):
    t = await application(database)
    original = t.repo.plan

    async def stopped(*args, **kwargs):
        result = await original(*args, **kwargs)
        t.app.stop()
        return result
    t.repo.plan = stopped
    try:
        with pytest.raises(StorageError, match='STOPPED'):
            await t.app.execute('plan1')
        assert not t.session.sent
        assert await database[0].fetchval('SELECT state FROM life_financial.send_attempts') == 'PREPARED'
        assert len(await t.values[5].claims(t.cfg.binding)) == 1
        assert (await t.app.execute('plan1')).replayed
        assert not t.session.sent
    finally:
        await t.financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['stop', 'cancel'])
async def test_stop_after_post_still_records_received_ack_without_resuming(database, fault):
    from hummingbot.core.event.event_forwarder import EventForwarder
    from hummingbot.core.event.events import MarketEvent
    t = await application(database)
    entered, release = asyncio.Event(), asyncio.Event()
    acknowledged = asyncio.Event()
    listener = EventForwarder(lambda _: acknowledged.set())
    t.connector.add_listener(MarketEvent.SellOrderCreated, listener)
    original = t.session.request

    async def sent(**kwargs):
        response = await original(**kwargs)
        if kwargs['method'] == 'POST':
            entered.set()
            await release.wait()
        return response
    t.session.request = sent
    try:
        pending = asyncio.create_task(t.app.execute('plan1'))
        await asyncio.wait_for(entered.wait(), 5)
        if fault == 'cancel':
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
            release.set()
        else:
            t.app.stop()
            release.set()
            with pytest.raises(StorageError, match='STOPPED'):
                await pending
        await asyncio.wait_for(acknowledged.wait(), 5)
        assert await database[0].fetchval('SELECT state FROM life_financial.send_attempts') == 'ACKED'
        assert len(await t.values[5].claims(t.cfg.binding)) == 1
        assert (await t.app.execute('plan1')).replayed
        assert len([r for r in t.session.sent if r['method'] == 'POST']) == 1
    finally:
        t.connector.remove_listener(MarketEvent.SellOrderCreated, listener)
        await t.financial.close()


@pytest.mark.asyncio
async def test_cancel_reply_cannot_bless_unreconciled_provider_maker(database):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_shared_capital import native_claim
    t = await application(database)
    original = t.app._collect

    async def unresolved(operation_id):
        command = await original(operation_id)
        account = t.values[4][0]
        t.values[4][0] = replace(account, sequence=account.sequence + 1,
                                 pending=(native_claim(t.owner, 'unreconciled-maker'),))
        await t.values[5].check_capital(t.cfg.binding)
        return command
    t.app._collect = unresolved
    try:
        with pytest.raises(StorageError, match='HEDGE_CHILD_NOT_READY'):
            await t.app.execute('plan1')
        assert not [r for r in t.session.sent if r['method'] == 'POST']
        assert not await t.values[5].claims(t.cfg.binding)
        assert (await t.financial.balance(t.cfg.binding)).total_quote_usdt == 10
    finally:
        await t.financial.close()


@pytest.mark.asyncio
async def test_native_application_retry_exhaustion_retains_gross_cost_and_original_claims(database):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_portfolio import refresh

    from hummingbot.core.data_type.in_flight_order import OrderState, OrderUpdate
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.portfolio import MeasurePortfolio, PgPortfolioRepository
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.swap_terminal import ConfirmSwapTerminal
    t = await application(database)
    try:
        await database[0].execute('INSERT INTO life_financial.portfolio_policies VALUES($1,$2,1,1000,5000,64,1048576)', t.cfg.account_id, t.cfg.binding.policy_hash)
        await database[0].execute('INSERT INTO life_financial.swap_terminal_policies VALUES($1,$2,64,1048576)', t.cfg.account_id, t.cfg.binding.policy_hash)
        total_cost = D('0')
        for i in range(3):
            plan_id, key = 'plan' + str(i + 1), 'one' if i == 0 else 'plan' + str(i + 1)
            exchange_id = str(123456 + i)
            t.response_body[0] = dict(code='0', data=[dict(sCode='0', ordId=exchange_id, clOrdId='HedgeWire' + key)])
            root = await t.app.execute(plan_id)
            assert root.child_state == 'PREPARED'
            total_cost += D(root.decision['estimated_cost_quote'])
            await refresh(database, t.values)
            await PgPortfolioRepository(t.repo.swap).measure(MeasurePortfolio('nav' + str(i)),
                                                             cut=(await t.financial.snapshot(t.cfg.binding)).cut, owner=t.owner)
            prepared_at = await database[0].fetchval('SELECT prepared_at_ms FROM life_financial.swap_preparations WHERE operation_id=$1', root.child_operation_id)
            terminal = ConfirmSwapTerminal('terminal' + str(i), root.child_operation_id, exchange_id, 'canceled', D('0'), (),
                                           t.values[4][0].observed_at_ms, prepared_at, 'okx_joint_reconciled')
            assert (await t.app.record_terminal(plan_id, terminal)).state == 'TERMINAL'
            assert (await t.app.record_terminal(plan_id, terminal)).replayed
            # Simulated received SDK cancellation, independent of native
            # bookkeeping. A diagnostic receipt cannot mark an SDK order done.
            await t.connector._order_tracker._process_order_update(OrderUpdate(trading_pair='LIFE-USDT',
                                                                               update_timestamp=t.values[4][0].observed_at_ms / 1000, new_state=OrderState.CANCELED,
                                                                               client_order_id='HedgeWire' + key, exchange_order_id=exchange_id))
            settled = await t.app.settle(SettleHedge('settle' + str(i), plan_id))
            assert settled.state == 'TERMINAL'
            assert (await t.app.settle(SettleHedge('settle' + str(i), plan_id))).replayed
        with pytest.raises(StorageError, match='HEDGE_CHILD_NOT_READY'):
            await t.app.execute('capped')
        capped = await t.repo.plan(PlanHedge.capture('capped-reason', replace(t.market, sequence=4,
                                                                              observed_at_ms=t.values[4][0].observed_at_ms)), cut=(await t.financial.observation_cut(t.cfg.binding)).cut, owner=t.owner)
        assert capped.decision['reason_code'] == 'HEDGE_RETRY_CAP' and capped.child_state is None
        assert len([r for r in t.session.sent if r['method'] == 'POST']) == 3
        assert not t.connector._order_tracker.active_orders
        assert len(await t.values[5].claims(t.cfg.binding)) == 3
        frame = await t.repo.frame(t.cfg.binding)
        assert D(frame['plans'][-1]['progress']['cost_hold']) == total_cost
        assert (await t.financial.balance(t.cfg.binding)).total_quote_usdt == 10
    finally:
        await t.financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['leverage', 'quantization'])
async def test_sdk_transform_cannot_change_native_hedge_geometry(database, fault):
    t = await application(database)
    if fault == 'leverage':
        t.connector._perpetual_trading.set_leverage('LIFE-USDT', 3)
    else:
        t.connector._trading_rules['LIFE-USDT'].min_price_increment = D('.1')
    try:
        with pytest.raises(StorageError, match='SDK_CONTEXT_INVALID|SDK_SUBMISSION_UNKNOWN'):
            await t.app.execute('plan1')
        assert not [r for r in t.session.sent if r['method'] == 'POST']
        assert await database[0].fetchval('SELECT state FROM life_financial.send_attempts') == 'PREPARED'
        assert len(await t.values[5].claims(t.cfg.binding)) == 1
        assert (await t.financial.balance(t.cfg.binding)).total_quote_usdt == 10
    finally:
        await t.financial.close()


@pytest.mark.asyncio
async def test_context_replacement_during_sdk_throttle_revokes_original_send(database):
    t = await application(database)
    t.throttle.release.clear()
    try:
        pending = asyncio.create_task(t.app.execute('plan1'))
        await asyncio.wait_for(t.throttle.entered.wait(), 5)
        t.app.repository = object()
        t.throttle.release.set()
        with pytest.raises(StorageError):
            await pending
        assert not [r for r in t.session.sent if r['method'] == 'POST']
        assert len(await t.values[5].claims(t.cfg.binding)) == 1
        assert not t.repo.runtime.healthy
        assert await database[0].fetchval('SELECT state FROM life_financial.send_attempts') == 'PREPARED'
    finally:
        await t.financial.close()


@pytest.mark.asyncio
async def test_hedge_backpressure_does_not_stop_original_cycle(database):
    t = await application(database)
    entered, release = asyncio.Event(), asyncio.Event()
    original = t.app._cancel

    async def waiting():
        entered.set()
        await release.wait()
        return await original()
    t.app._cancel = waiting
    try:
        pending = asyncio.create_task(t.app.execute('plan1'))
        await asyncio.wait_for(entered.wait(), 5)
        with pytest.raises(StorageError, match='BACKPRESSURE'):
            await t.app.execute('sibling')
        release.set()
        assert (await pending).child_state == 'PREPARED'
        assert len([r for r in t.session.sent if r['method'] == 'POST']) == 1
    finally:
        await t.financial.close()


@pytest.mark.asyncio
async def test_cancellation_failure_denies_collection_and_child(database):
    t = await application(database)

    async def unknown():
        return False
    t.app._cancel = unknown
    try:
        with pytest.raises(StorageError, match='CANCELLATION_UNQUALIFIED'):
            await t.app.execute('plan1')
        assert t.events == ['pause']
        assert await t.repo.lookup('plan1', t.cfg.binding) is None
        assert not await t.values[5].claims(t.cfg.binding)
        assert not t.session.sent
    finally:
        await t.financial.close()
