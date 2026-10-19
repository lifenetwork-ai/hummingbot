"""Actual OrderExecutor -> native quote journal -> signed SDK, fake HTTP only.

Synthetic admission sources are explicit; these cases qualify the execution
adapter, not the configured market source or a live service/account.
"""
import asyncio
import json
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal as D
from test.hummingbot.core.web_assistant.connections.test_request_guard import Response, Session, Throttler
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_action_preparation import command
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_runner_journal import assembled
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from hummingbot.connector.exchange.okx.okx_exchange import OkxExchange
from hummingbot.connector.trading_rule import TradingRule
from hummingbot.core.web_assistant.connections.rest_connection import RESTConnection
from hummingbot.core.web_assistant.rest_assistant import RESTAssistant
from hummingbot.strategy_v2.executors.order_executor.order_executor import OrderExecutor
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
from hummingbot.strategy_v2.life_liquidity.storage.postgres.quote_account_source import PgQuoteAccountSource


async def application(database, *, operation_timeout_ms=5000, shutdown_timeout_ms=1000):
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot_application import PgSpotQuoteApplication
    repo, cfg, owner, row, journal, create, runner, dispatch, active, ready = await assembled(database)
    connector = OkxExchange('synthetic-key', 'synthetic-secret', 'synthetic-pass', trading_pairs=[], trading_required=False)
    connector.enable_protected_trading_pair('LIFE-USDT')
    connector._trading_rules['LIFE-USDT'] = TradingRule('LIFE-USDT', min_order_size=D('.01'),
                                                        min_price_increment=D('.01'), min_base_amount_increment=D('.01'))
    connector.exchange_symbol_associated_to_pair = AsyncMock(return_value='LIFE-USDT')
    session, throttle = Session(), Throttler()
    throttle.release.set()
    connector._web_assistants_factory = SimpleNamespace(get_rest_assistant=AsyncMock(return_value=
                                                                                     RESTAssistant(RESTConnection(session), throttle, auth=connector.authenticator)))
    connector._api_request_url = AsyncMock(side_effect=lambda path_url, **_: 'https://www.okx.com' + path_url)
    reply, entered, release = [True], asyncio.Event(), asyncio.Event()
    release.set()

    async def send(**kwargs):
        session.sent.append(kwargs)
        response = Response()
        if kwargs['method'] == 'POST':
            entered.set()
            await release.wait()

        async def body():
            if kwargs['url'].endswith('/account/config'):
                return dict(code='0', data=[dict(uid='12345')])
            if not reply[0]:
                return dict(code='0', data=[])
            wire = json.loads(kwargs['data'])['clOrdId']
            return dict(code='0', data=[dict(sCode='0', ordId='123', clOrdId=wire)])
        response.json = body
        return response
    session.request = send

    async def facts():
        return (await command(database, cfg, owner, row)).admission

    async def prepare(action, wire):
        value = await command(database, cfg, owner, row)
        return replace(value, prepare=replace(value.prepare, client_order_id=wire))

    source = await PgQuoteAccountSource.create(journal, connector=connector, admission_source=facts,
                                               max_uid_age_ms=1000, max_auth_age_ms=1000, max_clock_skew_ms=100,
                                               max_pending=1, collection_timeout_ms=1000)
    app = PgSpotQuoteApplication(source, preparation_source=prepare, max_pending=1,
                                 operation_timeout_ms=operation_timeout_ms, shutdown_timeout_ms=shutdown_timeout_ms)
    strategy = SimpleNamespace(connectors={'okx': connector}, controllers={row.controller_id: journal},
                               current_timestamp=datetime.now(timezone.utc).timestamp())
    executor = OrderExecutor(strategy, create.executor_config)
    executor.register_events()
    original_execute = runner.executor_orchestrator.execute_action.side_effect

    def execute(action):
        original_execute(action)
        # The executor may submit while dispatch acceptance is still awaiting
        # SQL. Its application task must wait for that original acceptance.
        executor.place_open_order()
    runner.executor_orchestrator.execute_action.side_effect = execute

    async def start():
        await journal.publish('claim', (create,))
        dispatch.submit([create])
        await dispatch.drain()

    async def close():
        release.set()
        throttle.release.set()
        if app._physical:
            await asyncio.wait_for(asyncio.shield(asyncio.gather(*app._physical.values())), 3)
        await app.close()
        executor.unregister_events()
        await dispatch.close()
        await repo.close()
    return SimpleNamespace(**locals())


@pytest.mark.asyncio
async def test_actual_executor_waits_for_dispatch_then_native_prepare_arm_and_sdk_ack_once(database, monkeypatch):
    t = await application(database)
    waiting, proceed = asyncio.Event(), asyncio.Event()
    original = t.repo.transition_quote_action

    async def held(*args, **kwargs):
        waiting.set()
        await proceed.wait()
        return await original(*args, **kwargs)
    monkeypatch.setattr(t.repo, 'transition_quote_action', held)
    try:
        await t.journal.publish('claim', (t.create,))
        t.dispatch.submit([t.create])
        await asyncio.wait_for(waiting.wait(), 3)
        assert t.executor._order is not None and t.app.pending_count == 1
        assert not t.session.sent and await t.repo.attempt('intent1', t.cfg.binding) is None
        proceed.set()
        await t.dispatch.drain()
        await t.app.drain()
        posts = [row for row in t.session.sent if row['method'] == 'POST']
        assert len(posts) == 1
        wire = json.loads(posts[0]['data'])['clOrdId']
        assert wire == t.executor._order.order_id and len(wire) == 32
        assert t.executor._order.order.exchange_order_id == '123'
        assert (await t.repo.attempt('intent1', t.cfg.binding)).state.value == 'ACKED'
        with pytest.raises(StorageError, match='PG_SPOT_SUBMISSION_ALREADY_STARTED'):
            t.executor.place_open_order()
        assert len([row for row in t.session.sent if row['method'] == 'POST']) == 1
    finally:
        proceed.set()
        await t.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('phase', ['acceptance', 'prepare_return', 'throttle', 'final'])
async def test_stop_at_application_boundaries_never_sends_or_releases_original_holds(database, monkeypatch, phase):
    t = await application(database)
    original = t.repo.prepare_quote_action
    if phase == 'prepare_return':
        async def held(*args, **kwargs):
            result = await original(*args, **kwargs)
            t.app.stop()
            return result
        monkeypatch.setattr(t.repo, 'prepare_quote_action', held)
    elif phase == 'throttle':
        t.throttle.release.clear()
    elif phase == 'final':
        original_check = t.app._sdk_check

        def stopped(*args):
            t.app.stop()
            return original_check(*args)
        monkeypatch.setattr(t.app, '_sdk_check', stopped)
    else:
        original_dispatch = t.repo.transition_quote_action

        async def stopped(*args, **kwargs):
            result = await original_dispatch(*args, **kwargs)
            t.app.stop()
            return result
        monkeypatch.setattr(t.repo, 'transition_quote_action', stopped)
    try:
        await t.start()
        if phase == 'throttle':
            await asyncio.wait_for(t.throttle.entered.wait(), 3)
            t.app.stop()
            t.throttle.release.set()
        with pytest.raises(StorageError, match='PG_SPOT_SUBMISSION_UNKNOWN'):
            await t.app.drain()
        assert not [row for row in t.session.sent if row['method'] == 'POST']
        state = await database[0].fetchval('SELECT state FROM life_financial.send_attempts')
        assert state == (None if phase == 'acceptance' else 'SEND_UNKNOWN' if phase == 'final' else 'PREPARED')
        if state is not None:
            assert await database[0].fetchval("SELECT reserved FROM life_financial.balances WHERE asset='USDT'") > 0
    finally:
        await t.close()


@pytest.mark.asyncio
async def test_late_original_ack_survives_stop_and_cancelled_waiter(database):
    t = await application(database)
    try:
        t.release.clear()
        await t.start()
        await asyncio.wait_for(t.entered.wait(), 3)
        waiter = asyncio.create_task(t.app.drain())
        await asyncio.sleep(0)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        t.app.stop()
        t.release.set()
        await t.app.drain()
        assert (await t.repo.attempt('intent1', t.cfg.binding)).state.value == 'ACKED'
        assert t.app.pending_count == 0 and not t.journal.local_runner_create_fence(t.create)
        assert len([row for row in t.session.sent if row['method'] == 'POST']) == 1
    finally:
        await t.close()


@pytest.mark.asyncio
async def test_unknown_sdk_response_retains_send_unknown_without_an_executor_retry_grant(database):
    t = await application(database)
    try:
        t.reply[0] = False
        await t.start()
        with pytest.raises(StorageError, match='PG_SPOT_SUBMISSION_UNKNOWN'):
            await t.app.drain()
        assert await database[0].fetchval('SELECT state FROM life_financial.send_attempts') == 'SEND_UNKNOWN'
        with pytest.raises(StorageError):
            t.executor.place_open_order()
        assert len([row for row in t.session.sent if row['method'] == 'POST']) == 1
        assert await database[0].fetchval("SELECT reserved FROM life_financial.balances WHERE asset='USDT'") > 0
    finally:
        await t.close()


@pytest.mark.asyncio
async def test_operation_timeout_does_not_hide_still_running_sdk_or_erase_late_ack(database):
    t = await application(database, operation_timeout_ms=1000, shutdown_timeout_ms=50)
    try:
        t.release.clear()
        await t.start()
        await asyncio.wait_for(t.entered.wait(), 3)
        with pytest.raises(StorageError, match='PG_SPOT_SUBMISSION_UNKNOWN'):
            await t.app.drain()
        assert await database[0].fetchval('SELECT state FROM life_financial.send_attempts') == 'SEND_UNKNOWN'
        # The application timeout cannot claim physical quiescence while the
        # SDK still owns the POST/late ACK. Storage must remain open.
        with pytest.raises(StorageError, match='PG_SPOT_SHUTDOWN_READBACK_REQUIRED'):
            await t.app.close()
        t.release.set()
        await asyncio.wait_for(asyncio.shield(asyncio.gather(*t.app._physical.values())), 3)
        await t.app.close()
        assert await database[0].fetchval('SELECT state FROM life_financial.send_attempts') == 'ACKED'
    finally:
        t.release.set()
        await t.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('phase', ['dispatch', 'prepare', 'arm', 'ack'])
async def test_lost_committed_reply_never_retries_or_releases_a_claim(database, monkeypatch, phase):
    t = await application(database)
    method = {'dispatch': 'transition_quote_action', 'prepare': 'prepare_quote_action',
              'arm': 'arm_quote_action', 'ack': 'acknowledge'}[phase]
    original = getattr(t.repo, method)

    async def lost(*args, **kwargs):
        await original(*args, **kwargs)
        raise OSError('synthetic lost committed reply')
    monkeypatch.setattr(t.repo, method, lost)
    try:
        await t.start()
        with pytest.raises(StorageError, match='PG_SPOT_SUBMISSION_UNKNOWN'):
            await t.app.drain()
        state = await database[0].fetchval('SELECT state FROM life_financial.send_attempts')
        assert state == {'dispatch': None, 'prepare': 'PREPARED', 'arm': 'SEND_UNKNOWN', 'ack': 'ACKED'}[phase]
        assert len([r for r in t.session.sent if r['method'] == 'POST']) == (1 if phase == 'ack' else 0)
        if phase != 'dispatch':
            assert await database[0].fetchval("SELECT reserved FROM life_financial.balances WHERE asset='USDT'") > 0
        with pytest.raises(StorageError):
            t.executor.place_open_order()
        assert len(t.journal._entries) == 1
    finally:
        await t.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['amount', 'price', 'type', 'foreign_config', 'sdk_quantization', 'missing_tracking'])
async def test_executor_or_sdk_drift_cannot_fall_back_to_an_ordinary_order(database, monkeypatch, fault):
    from hummingbot.core.data_type.common import OrderType
    t = await application(database)
    try:
        if fault in ('amount', 'price', 'type', 'foreign_config'):
            await t.journal.publish('claim', (t.create,))
            config = t.create.executor_config.model_copy(deep=True)
            if fault == 'foreign_config':
                config.controller_id = 'foreign'
            with pytest.raises(StorageError):
                t.journal.submit_executor_spot_order(config, amount=D('1') if fault == 'amount' else config.amount,
                                                     price=D('2') if fault == 'price' else config.price,
                                                     order_type=OrderType.LIMIT if fault == 'type' else OrderType.LIMIT_MAKER)
        else:
            if fault == 'sdk_quantization':
                monkeypatch.setattr(t.connector, 'quantize_order_amount', lambda **_: D('1'))
            else:
                original_check = t.app._sdk_check

                def removed(action, wire, body):
                    t.connector.stop_tracking_order(wire)
                    return original_check(action, wire, body)
                monkeypatch.setattr(t.app, '_sdk_check', removed)
            await t.start()
            with pytest.raises(StorageError, match='PG_SPOT_SUBMISSION_UNKNOWN'):
                await t.app.drain()
            assert await database[0].fetchval('SELECT state FROM life_financial.send_attempts') in ('PREPARED', 'SEND_UNKNOWN')
            assert await database[0].fetchval("SELECT reserved FROM life_financial.balances WHERE asset='USDT'") > 0
        assert not [r for r in t.session.sent if r['method'] == 'POST']
    finally:
        await t.close()


@pytest.mark.asyncio
async def test_native_cold_readback_cannot_reconstruct_local_submit_authority(database):
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.runner_journal import PgQuoteRunnerJournal
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
    t = await application(database)
    cold_repo = None
    try:
        await t.start()
        await t.app.drain()
        t.app.stop()
        cold_repo = PgSpotRepository(await PgRuntime.open(t.cfg))
        cold = PgQuoteRunnerJournal(cold_repo, owner=t.owner, controller_id=t.row.controller_id, config_version=1,
                                    runner_facts=t.journal.runner_facts, local_ready=lambda: True, clock=t.journal.clock, max_actions=8)
        with pytest.raises(StorageError, match='RUNNER_CLAIM_REPLAY_REQUIRES_RECOVERY'):
            await cold.publish('claim', (t.create,))
        with pytest.raises(StorageError, match='PG_RUNNER_TRANSPORT_ASSEMBLY_REQUIRED'):
            cold.submit_executor_spot_order(t.create.executor_config)
        assert await database[0].fetchval('SELECT state FROM life_financial.send_attempts') == 'ACKED'
        assert len([r for r in t.session.sent if r['method'] == 'POST']) == 1
    finally:
        if cold_repo is not None:
            await cold_repo.close()
        await t.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['preparation', 'admission', 'runner_facts', 'controller', 'credentials'])
async def test_replacing_original_application_context_revokes_before_http(database, monkeypatch, fault):
    t = await application(database)
    original = t.repo.transition_quote_action

    async def changed(*args, **kwargs):
        result = await original(*args, **kwargs)
        if fault == 'preparation':
            t.app._prepare = AsyncMock()
        elif fault == 'admission':
            t.source._admission_source = AsyncMock()
        elif fault == 'runner_facts':
            t.journal.runner_facts = AsyncMock()
        elif fault == 'controller':
            t.journal.controller_id = 'foreign'
        else:
            t.connector.okx_api_key = 'foreign'
        return result
    monkeypatch.setattr(t.repo, 'transition_quote_action', changed)
    try:
        await t.start()
        with pytest.raises(StorageError, match='PG_SPOT_SUBMISSION_UNKNOWN'):
            await t.app.drain()
        assert not t.session.sent
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.send_attempts') == 0
    finally:
        await t.close()
