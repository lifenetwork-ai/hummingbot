"""Authenticated maker cancellation and original native terminal accounting."""
import asyncio
import json
from test.hummingbot.core.web_assistant.connections.test_request_guard import Response
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_joint_source import joint
from unittest.mock import AsyncMock

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
from hummingbot.strategy_v2.life_liquidity.storage.postgres.maker_cancellation import PgMakerCancellation


async def makers(database):
    t = await joint(database, spot_fee='USDT')
    t.spot_order['state'] = 'partially_filled'
    t.pending['SPOT'] = [t.spot_order]
    t.connector.exchange_symbol_associated_to_pair = AsyncMock(return_value='LIFE-USDT')
    t.cancel_requests, t.complete_cancel, t.cancel_ack = [], True, '0'
    t.cancel_ack_wire = 'wire1'
    t.paused = True
    original = t.session.request

    async def http(**kwargs):
        if not kwargs['url'].endswith('/cancel-order'):
            return await original(**kwargs)
        t.cancel_requests.append(kwargs)
        assert kwargs['method'] == 'POST' and not kwargs['allow_redirects']
        assert json.loads(kwargs['data']) == {'instId': 'LIFE-USDT', 'clOrdId': 'wire1'}
        native = await t.financial.attempt('intent1', t.cfg.binding)
        assert native.cancel_requested and native.cancel_attempts == 1
        if t.complete_cancel:
            t.spot_order['state'] = 'canceled'
            t.pending['SPOT'] = []
        response = Response()

        async def body():
            return dict(code='0', data=[dict(sCode=t.cancel_ack, clOrdId=t.cancel_ack_wire, ordId='123456789')])
        response.json = body
        return response
    t.session.request = http

    def application():
        return PgMakerCancellation(t.joint, paused=lambda: t.paused, max_cancels=2,
                                   retry_interval_ms=100, timeout_ms=5000)
    t.application = application
    t.cancellation = application()
    return t


@pytest.mark.asyncio
async def test_signed_cancel_reconciles_partial_maker_before_terminal_and_preserves_claims(database):
    t = await makers(database)
    try:
        before = await t.financial.snapshot(t.cfg.binding)
        claims = await t.values[5].claims(t.cfg.binding)
        assert await t.cancellation.cancel_makers('hedge1') is True
        assert len(t.cancel_requests) == 1
        assert (await t.financial.attempt('intent1', t.cfg.binding)).state.value == 'TERMINAL'
        after = await t.financial.snapshot(t.cfg.binding)
        assert (after.balance.total_base_life, after.balance.total_quote_usdt) == (before.balance.total_base_life, before.balance.total_quote_usdt)
        assert after.budget == before.budget
        assert await t.values[5].claims(t.cfg.binding) == claims
        assert await t.cancellation.cancel_makers('hedge1') is True
        assert len(t.cancel_requests) == 1
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.spot_terminal_facts') == 1
        assert await database[0].fetchval('SELECT halted FROM life_financial.accounts')
    finally:
        await t.financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('ack', ['0', '51400', '51401'])
async def test_cancel_ack_without_closed_order_retains_holds_and_cannot_replay_http(database, ack):
    t = await makers(database)
    t.complete_cancel, t.cancel_ack = False, ack
    try:
        before = await t.financial.snapshot(t.cfg.binding)
        with pytest.raises(StorageError, match='MAKER_CANCEL_PENDING'):
            await t.cancellation.cancel_makers('hedge1')
        assert len(t.cancel_requests) == 1
        assert (await t.financial.attempt('intent1', t.cfg.binding)).state.value == 'ACKED'
        after = await t.financial.snapshot(t.cfg.binding)
        assert (after.balance, after.budget) == (before.balance, before.budget)
        with pytest.raises(StorageError, match='RECONCILE_BEFORE_RETRY'):
            await t.application().cancel_makers('hedge1')
        assert len(t.cancel_requests) == 1
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.spot_terminal_facts') == 0
    finally:
        await t.financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['pause', 'uid', 'foreign_pending'])
async def test_unqualified_maker_source_cannot_cancel_or_release(database, fault):
    t = await makers(database)
    if fault == 'pause':
        t.paused = False
    elif fault == 'uid':
        t.config['uid'] = 'other-account'
    else:
        t.pending['SPOT'].append({**t.spot_order, 'clOrdId': 'foreign', 'ordId': '999'})
    try:
        before = await t.financial.snapshot(t.cfg.binding)
        with pytest.raises(StorageError):
            await t.cancellation.cancel_makers('hedge1')
        assert not t.cancel_requests
        after = await t.financial.snapshot(t.cfg.binding)
        assert (after.balance, after.budget) == (before.balance, before.budget)
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.spot_terminal_facts') == 0
    finally:
        await t.financial.close()


@pytest.mark.asyncio
async def test_validly_signed_cancel_wire_cannot_target_another_order(database):
    t = await makers(database)
    original = t.connector._api_post

    async def changed(*args, **kwargs):
        if kwargs.get('path_url', '').endswith('/cancel-order'):
            kwargs['data'] = {**kwargs['data'], 'clOrdId': 'foreign'}
        return await original(*args, **kwargs)
    t.connector._api_post = changed
    try:
        before = await t.financial.snapshot(t.cfg.binding)
        with pytest.raises(StorageError):
            await t.cancellation.cancel_makers('hedge1')
        assert not t.cancel_requests
        after = await t.financial.snapshot(t.cfg.binding)
        assert (after.balance, after.budget) == (before.balance, before.budget)
        assert (await t.financial.attempt('intent1', t.cfg.binding)).cancel_attempts == 1
    finally:
        await t.financial.close()


@pytest.mark.asyncio
async def test_sdk_maker_must_close_independently_of_cancel_ack_before_terminal(database):
    from decimal import Decimal as D

    from hummingbot.core.data_type.common import OrderType, TradeType
    from hummingbot.core.data_type.in_flight_order import OrderState, OrderUpdate
    t = await makers(database)
    t.connector.start_tracking_order(order_id='wire1', exchange_order_id='123456789', trading_pair='LIFE-USDT',
                                     trade_type=TradeType.BUY, price=D('1'), amount=D('2'), order_type=OrderType.LIMIT_MAKER)
    try:
        before = await t.financial.snapshot(t.cfg.binding)
        with pytest.raises(StorageError, match='SDK_NOT_QUIESCENT'):
            await t.cancellation.cancel_makers('hedge1')
        assert len(t.cancel_requests) == 1
        assert (await t.financial.attempt('intent1', t.cfg.binding)).state.value == 'ACKED'
        assert (await t.financial.snapshot(t.cfg.binding)).balance == before.balance
        await t.connector._order_tracker._process_order_update(OrderUpdate(trading_pair='LIFE-USDT',
                                                                           update_timestamp=t.journal.clock().timestamp(), new_state=OrderState.CANCELED,
                                                                           client_order_id='wire1', exchange_order_id='123456789'))
        assert await t.application().cancel_makers('hedge1') is True
        assert (await t.financial.attempt('intent1', t.cfg.binding)).state.value == 'TERMINAL'
        assert len(t.cancel_requests) == 1
    finally:
        await t.financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['stop', 'ingress', 'native_cut', 'sdk_scope'])
async def test_cancellation_rechecks_scope_at_actual_http_boundary(database, fault):
    from contextlib import asynccontextmanager

    from hummingbot.strategy_v2.life_liquidity.storage.spot_lifecycle import SpotLifecycleCommand
    t = await makers(database)
    entered, release = asyncio.Event(), asyncio.Event()
    original = t.throttler.execute_task

    @asynccontextmanager
    async def gate(**kwargs):
        if kwargs.get('limit_id', '').endswith('/cancel-order'):
            entered.set()
            await release.wait()
        async with original(**kwargs):
            yield
    t.throttler.execute_task = gate
    try:
        pending = asyncio.create_task(t.cancellation.cancel_makers('hedge1'))
        await asyncio.wait_for(entered.wait(), 5)
        if fault == 'stop':
            t.cancellation.stop()
        elif fault == 'ingress':
            t.financial._runtime._ingress_epoch += 1
        elif fault == 'sdk_scope':
            from decimal import Decimal as D

            from hummingbot.core.data_type.common import OrderType, TradeType
            t.connector.start_tracking_order(order_id='foreign', exchange_order_id='999', trading_pair='LIFE-USDT',
                                             trade_type=TradeType.BUY, price=D('1'), amount=D('2'), order_type=OrderType.LIMIT_MAKER)
        else:
            # A separate native writer need not share this process's ingress
            # counter. Its legitimate command still changes the primary cut.
            before_epoch = t.financial._runtime._ingress_epoch
            await t.financial.apply_lifecycle(SpotLifecycleCommand('other-writer', 'operation1', 'intent1',
                                                                   'wire1', 'REQUEST_CANCEL'), cut=(await t.financial.observation_cut(t.cfg.binding)).cut, owner=t.owner)
            assert t.financial._runtime._ingress_epoch == before_epoch
        release.set()
        with pytest.raises(StorageError):
            await pending
        assert not t.cancel_requests
        assert (await t.financial.attempt('intent1', t.cfg.binding)).cancel_attempts == 1
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.spot_terminal_facts') == 0
    finally:
        release.set()
        await t.financial.close()


@pytest.mark.asyncio
async def test_unknown_committed_cancel_attempt_cannot_be_replayed_into_http(database, monkeypatch):
    from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown
    t = await makers(database)
    original, fired = t.financial._runtime._commit, []

    async def interrupted(tx):
        action = await tx._connection.fetchval("""SELECT command#>>'{command,action}'
            FROM life_financial.operations WHERE account_id=$1 ORDER BY committed_version DESC LIMIT 1""", t.cfg.account_id)
        await original(tx)
        if action == 'RECORD_CANCEL_ATTEMPT' and not fired:
            fired.append(True)
            raise ConnectionError('synthetic connection loss after physical COMMIT')
    monkeypatch.setattr(t.financial._runtime, '_commit', interrupted)
    try:
        with pytest.raises(CommitUnknown):
            await t.cancellation.cancel_makers('hedge1')
        assert fired == [True] and not t.cancel_requests
        assert (await t.financial.attempt('intent1', t.cfg.binding)).cancel_attempts == 1
        with pytest.raises(StorageError, match='RECONCILE_BEFORE_RETRY'):
            await t.application().cancel_makers('hedge1')
        assert not t.cancel_requests
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.spot_terminal_facts') == 0
    finally:
        await t.financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('closed', [True, False])
async def test_malformed_cancel_response_requires_independent_terminal_proof(database, closed):
    t = await makers(database)
    t.cancel_ack_wire, t.complete_cancel = 'foreign', closed
    try:
        before = await t.financial.snapshot(t.cfg.binding)
        claims = await t.values[5].claims(t.cfg.binding)
        with pytest.raises(StorageError, match='CANCEL_UNAVAILABLE'):
            await t.cancellation.cancel_makers('hedge1')
        assert len(t.cancel_requests) == 1
        assert (await t.financial.attempt('intent1', t.cfg.binding)).state.value == 'ACKED'
        assert (await t.financial.snapshot(t.cfg.binding)).balance == before.balance
        if closed:
            assert await t.application().cancel_makers('hedge1') is True
            assert (await t.financial.attempt('intent1', t.cfg.binding)).state.value == 'TERMINAL'
        else:
            with pytest.raises(StorageError, match='RECONCILE_BEFORE_RETRY'):
                await t.application().cancel_makers('hedge1')
        assert len(t.cancel_requests) == 1
        assert await t.values[5].claims(t.cfg.binding) == claims
        after = await t.financial.snapshot(t.cfg.binding)
        assert (after.balance.total_base_life, after.balance.total_quote_usdt) == (before.balance.total_base_life, before.balance.total_quote_usdt)
        assert after.budget == before.budget
    finally:
        await t.financial.close()


@pytest.mark.asyncio
async def test_native_hedge_installs_authenticated_cancellation_before_recapturing_market(database):
    from datetime import datetime, timezone

    from hummingbot.connector.derivative.okx_perpetual.okx_perpetual_derivative import OkxPerpetualDerivative
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.hedge import PgHedgeRepository
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.hedge_application import PgHedgeApplication
    t = await makers(database)
    await database[0].execute('INSERT INTO life_financial.swap_boundary_policies VALUES($1,$2,1000,64,1048576)', t.cfg.account_id, t.cfg.binding.policy_hash)
    connector = OkxPerpetualDerivative(okx_perpetual_api_key='synthetic-key', okx_perpetual_secret_key='synthetic-secret',
                                       okx_perpetual_passphrase='synthetic-pass', trading_pairs=[], trading_required=False)
    captured = []

    def pause():
        t.paused = True
        return True

    async def collect(operation_id):
        assert operation_id == 'hedge1' and len(t.cancel_requests) == 1
        assert (await t.financial.attempt('intent1', t.cfg.binding)).state.value == 'TERMINAL'
        captured.append(True)
        raise StorageError('TEST_MARKET_NOT_AVAILABLE')
    t.paused = False
    try:
        app = await PgHedgeApplication.create(PgHedgeRepository(t.batch.swap), connector=connector,
                                              pause_makers=pause, maker_cancellation=t.cancellation, collect=collect, local_fence=lambda: True,
                                              clock=lambda: datetime.now(timezone.utc), max_uid_age_ms=1000, max_auth_age_ms=1000, max_clock_skew_ms=100,
                                              collection_timeout_ms=1000, cycle_timeout_ms=5000)
        with pytest.raises(StorageError, match='TEST_MARKET_NOT_AVAILABLE'):
            await app.execute('hedge1')
        assert captured == [True]
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.send_attempts') == 2
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.hedge_plan_facts') == 0
    finally:
        await t.financial.close()
