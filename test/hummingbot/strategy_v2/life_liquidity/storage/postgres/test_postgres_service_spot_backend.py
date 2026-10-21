"""Configured SPOT physical backend loss keeps original money and request identity."""
import asyncio
from dataclasses import asdict
from datetime import timedelta
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.service_process import (
    cold_service_context,
    encoded_config,
)
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_service_cycle import cancellation_feed
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_service_process import monetary_rows
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_service_trading import configured

import asyncpg
import pytest

from hummingbot.core.data_type.common import TradeType
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
from hummingbot.strategy_v2.life_liquidity.storage.postgres.ack_ingress import PgAckIngress
from hummingbot.strategy_v2.life_liquidity.storage.postgres.diagnostics import _current_trace
from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
from hummingbot.strategy_v2.models.base import RunnableStatus


async def spot_money(connection, account_id):
    values = await monetary_rows(connection, account_id)
    for table in ('spot_preparations', 'spot_ack_ingress', 'spot_ack_ingress_receipts',
                  'spot_terminal_facts', 'shared_claim_releases'):
        values[table] = await connection.fetchval(
            f"SELECT coalesce(jsonb_agg(to_jsonb(v) ORDER BY to_jsonb(v)::text),'[]'::jsonb) FROM life_financial.{table} v WHERE account_id=$1", account_id)
    return values


async def deliver_original_ack(database, cfg, context, before, *, already_applied):
    # Pending original SPOT responses intentionally block ordinary configured
    # startup. Repair uses only the typed durable response, without a sender.
    with pytest.raises(StorageError, match='PG_JOINT_OBSERVER_RECOVERY_REQUIRED'):
        await context.reopen(False)
    assert await spot_money(database[0], cfg.account_id) == before
    financial = PgSpotRepository(await PgRuntime.open(cfg))
    try:
        balance = await financial.balance(cfg.binding)
        original = await database[0].fetchrow('SELECT * FROM life_financial.send_attempts WHERE account_id=$1', cfg.account_id)
        assert original['state'] == ('ACKED' if already_applied else 'SEND_UNKNOWN')
        ingress = PgAckIngress(financial)
        received, = await ingress.pending(cfg.binding, max_rows=64, max_bytes=1048576)
        assert (await financial.apply_received(received.command, owner=received.owner)).state == 'ACKED'
        assert (await financial.apply_received(received.command, owner=received.owner)).replayed
        await ingress.mark_applied(received, owner=received.owner)
        assert (await ingress.mark_applied(received, owner=received.owner)).replayed
        assert not await ingress.pending(cfg.binding, max_rows=64, max_bytes=1048576)
        assert await financial.balance(cfg.binding) == balance
        repaired = await spot_money(database[0], cfg.account_id)
        for table in before.keys() - {'send_attempts', 'spot_ack_ingress_receipts'}:
            assert repaired[table] == before[table], table
        saved = await database[0].fetchrow('SELECT * FROM life_financial.send_attempts WHERE account_id=$1', cfg.account_id)
        for key in set(original.keys()) - {'state', 'exchange_order_id', 'version'}:
            assert saved[key] == original[key], key
        assert saved['state'] == 'ACKED' and saved['exchange_order_id'] == received.command.exchange_order_id
        assert saved['version'] >= original['version']
        assert all(call['method'] == 'GET' for call in context.calls)
        return repaired
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('phase,committed,side', [
    pytest.param(phase, committed, TradeType.BUY, id=phase + ('_after_buy' if committed else '_before_buy'))
    for phase in ('source', 'publication', 'prepare', 'arm', 'capture', 'apply', 'delivery', 'terminal', 'release', 'projection')
    for committed in (False, True)] + [
    pytest.param('prepare', True, TradeType.SELL, id='prepare_after_sell'),
    pytest.param('apply', True, TradeType.SELL, id='apply_after_sell')])
async def test_configured_spot_backend_loss_preserves_original_state_without_resend(database, monkeypatch, phase, committed, side):
    closing = phase in ('terminal', 'release', 'projection')
    pending_ack = phase in ('capture', 'apply', 'delivery') and not (
        phase == 'capture' and not committed or phase == 'delivery' and committed)
    t, service = await configured(database, maker_cycle=closing,
                                  spot_overrides=dict(owner_duration=timedelta(seconds=300), session_duration=timedelta(seconds=360)))
    service.controller.start()
    stream, canceled = None, []
    if closing:
        await service.quote('backend-spot-original', side, 0)
        stream, canceled = await cancellation_feed(t)
    admin = await asyncpg.connect(database[1])
    original, original_transaction = service.runtime._commit, service.runtime._transaction
    killed, order_kind = [], None
    target = dict(source='spot_capture_provider_collection', publication='spot_capture_quote_action',
                  prepare='spot_action_prepare', arm='spot_arm_quote', capture='spot_capture_ack_ingress',
                  apply='spot_observe', delivery='spot_mark_ack_ingress', terminal='spot_order_command',
                  release='shared_release_command', projection='recorder_apply_executor')[phase]

    async def submitted(query, args, *values, **options):
        nonlocal order_kind
        if phase == 'terminal' and query.startswith('SELECT life_financial.spot_order_command('):
            order_kind = args[6]
            try:
                return await original_transaction(query, args, *values, **options)
            finally:
                order_kind = None
        return await original_transaction(query, args, *values, **options)
    monkeypatch.setattr(service.runtime, '_transaction', submitted)

    async def lost(transaction):
        if _current_trace.get().operation != target or phase == 'terminal' and order_kind != 'SPOT_TERMINAL':
            return await original(transaction)
        if phase == 'projection' and not await admin.fetchval(
                'SELECT EXISTS(SELECT 1 FROM life_financial.shared_claim_releases WHERE account_id=$1)', t.cfg.account_id):
            return await original(transaction)
        assert not killed, 'Configured SPOT retried an uncertain COMMIT'
        pid = _current_trace.get().backend_pid
        assert type(pid) is int and pid > 0
        if committed:
            await original(transaction)  # Retain any original source-fenced wrapper.
        assert await admin.fetchval('SELECT pg_terminate_backend($1)', pid)
        killed.append(pid)
        raise ConnectionError('synthetic configured-SPOT lost COMMIT reply')
    monkeypatch.setattr(service.runtime, '_commit', lost)
    try:
        with pytest.raises(StorageError):
            if closing:
                await service.cancel_makers(('backend-spot-original',))
            else:
                await service.quote('backend-spot-original', side, 0)
        assert len(killed) == 1 and not service.runtime.healthy and service._revoked
        physical = [d for d in service.runtime.diagnostics
                    if d.operation == target and d.backend_pid == killed[0] and d.outcome == 'commit_unknown']
        assert len(physical) == 1 and physical[0].error_phase == 'commit'
        assert physical[0].commit_attempted and not physical[0].commit_confirmed
        wire_count = int(phase not in ('source', 'publication', 'prepare', 'arm'))
        assert len(t.service_orders) == wire_count
        assert len(canceled) == int(closing)
        manifest = dict(config=encoded_config(t.cfg), owner=t.owner.json(), controller=service.config.model_dump_json(),
                        sources=[asdict(r.source) for r in service.reporting.events], account_config=t.config,
                        balance=t.balance, positions=t.positions, orders=t.service_orders)
    finally:
        await admin.close()
        try:
            try:
                await service.close()
            except StorageError as error:
                expected = {'PG_RECORDER_DEFERRED_BACKPRESSURE', 'RECORDER_APPLICATION_UNAVAILABLE'}
                if phase == 'arm' and committed:
                    expected.add('PG_JOINT_SOURCE_ORDER_SCOPE')
                if pending_ack:
                    expected.add('PG_JOINT_OBSERVER_RECOVERY_REQUIRED')
                assert str(error) in expected
        finally:
            if stream is not None:
                stream.cancel()
                await asyncio.gather(stream, return_exceptions=True)
        assert service._close_task.done() and service.runtime._closing and not service.runtime._tasks
        assert not service.events._registered
        assert service.events._task is None or service.events._task.done()
        assert all(task.done() for task in (*service.quote_application._pending.values(), *service.quote_application._physical.values()))
        assert service.controller.status is RunnableStatus.TERMINATED and service.controller.terminated.is_set()
    before = await spot_money(database[0], t.cfg.account_id)
    original_arms = await database[0].fetch("SELECT * FROM life_financial.operations WHERE account_id=$1 AND command->>'kind'='SPOT_QUOTE_ARM'", t.cfg.account_id)
    context, cold = cold_service_context(manifest), None
    try:
        if phase == 'arm' and committed:
            with pytest.raises(StorageError, match='PG_JOINT_SOURCE_ORDER_SCOPE'):
                await context.reopen(False)
            assert await spot_money(database[0], t.cfg.account_id) == before
            assert await database[0].fetchval('SELECT state FROM life_financial.send_attempts WHERE account_id=$1', t.cfg.account_id) == 'SEND_UNKNOWN'
            assert context.calls and all(call['method'] == 'GET' for call in context.calls)
            return
        if pending_ack:
            before = await deliver_original_ack(
                database, t.cfg, context, before,
                already_applied=phase == 'delivery' or phase == 'apply' and committed)
        cold = await context.reopen(False)
        assert await spot_money(database[0], t.cfg.account_id) == before
        prepared = phase not in ('source', 'publication') and (phase != 'prepare' or committed)
        terminal = phase in ('release', 'projection') or phase == 'terminal' and committed
        released = phase == 'projection' or phase == 'release' and committed
        attempts = await database[0].fetch('SELECT * FROM life_financial.send_attempts WHERE account_id=$1', t.cfg.account_id)
        if prepared:
            attempt, = attempts
            expected = ('TERMINAL' if terminal else 'ACKED' if closing or pending_ack else
                        'PREPARED' if phase == 'prepare' or phase == 'arm' and not committed else
                        'ACKED' if phase == 'delivery' or phase == 'apply' and committed else 'SEND_UNKNOWN')
            assert attempt['state'] == expected
        else:
            assert not attempts
        claim_count = int(phase != 'source') - int(released)
        assert len(await cold.source.capital.claims(t.cfg.binding)) == claim_count
        for table, count in (('spot_terminal_facts', int(terminal)), ('shared_claim_releases', int(released))):
            assert await database[0].fetchval('SELECT count(*) FROM life_financial.' + table + ' WHERE account_id=$1', t.cfg.account_id) == count
        balance = await cold.financial.balance(t.cfg.binding)
        assert balance.total_base_life == 5 and balance.total_quote_usdt == 10
        assert not cold._owned_executors and not cold.journal._entries
        assert not cold.runner.executor_orchestrator.active_executors['main']
        assert all(not sdk._order_tracker.active_orders for sdk in context.connectors.values())
        ingress = PgAckIngress(cold.financial)
        assert not await ingress.pending(t.cfg.binding, max_rows=64, max_bytes=1048576)
        assert await cold.financial.balance(t.cfg.binding) == balance
        repaired = await spot_money(database[0], t.cfg.account_id)
        for table in before.keys() - {'send_attempts', 'spot_ack_ingress_receipts'}:
            assert repaired[table] == before[table], table
        assert await database[0].fetch("SELECT * FROM life_financial.operations WHERE account_id=$1 AND command->>'kind'='SPOT_QUOTE_ARM'", t.cfg.account_id) == original_arms
        if attempts:
            saved = await database[0].fetchrow('SELECT * FROM life_financial.send_attempts WHERE account_id=$1', t.cfg.account_id)
            for key in set(attempt.keys()) - {'state', 'exchange_order_id', 'version'}:
                assert saved[key] == attempt[key], key
            assert saved['version'] >= attempt['version']
            if pending_ack:
                assert saved['state'] == 'ACKED' and saved['exchange_order_id'] == manifest['orders'][0]['ordId']
        with pytest.raises(StorageError, match='PG_SERVICE_TRADING_NOT_QUALIFIED'):
            await cold.quote('backend-spot-original', side, 0)
        assert all(call['method'] == 'GET' for call in context.calls)
        assert len(t.service_orders) == wire_count
    finally:
        if cold is not None:
            await cold.close()
