"""Real foreground process loss preserves native accepted/unknown money."""
import asyncio
import json
import os
import signal
import sys
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.service_process import cold_service_context
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_service_swap_process import joint_money
from time import monotonic
from uuid import UUID

import pytest

from hummingbot.core.data_type.common import TradeType
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError


@pytest.mark.asyncio
@pytest.mark.parametrize('product', ['SPOT', 'SWAP'])
@pytest.mark.parametrize('termination', ['SIGTERM', 'SIGKILL'])
async def test_actual_worker_loss_cannot_refund_or_replay_an_accepted_unknown_send(database, product, termination):
    env = {key: os.environ[key] for key in ('PATH', 'PYTHONPATH') if key in os.environ}
    env.update(LIFE_ENV_FILE='', LIFE_AUTO_MIGRATE='false', LIFE_SERVICE_CONFIG='/tmp/synthetic-injected-native.yml',
               LIFE_SOURCE_FACTORY='trusted:unused', LIFE_TICK_SECONDS='.1', LIFE_STARTUP_TIMEOUT_SECONDS='30',
               LIFE_SHUTDOWN_TIMEOUT_SECONDS='.4', LIFE_STATUS_HOST='127.0.0.1', LIFE_STATUS_PORT='0',
               LIFE_TELEMETRY_INTERVAL_SECONDS='.05')
    process = await asyncio.create_subprocess_exec(
        sys.executable, '-m', 'test.hummingbot.strategy_v2.life_liquidity.storage.postgres.worker_financial_process',
        env=env, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    lines, service = [], None
    try:
        process.stdin.write(json.dumps(dict(migration_dsn=database[2], root_dsn=database[1], product=product)).encode())
        await process.stdin.drain()
        process.stdin.close()
        async with asyncio.timeout(45):
            while True:
                line = await process.stdout.readline()
                assert line, 'worker failed before the original financial boundary'
                lines.append(line)
                entry = json.loads(line)
                if entry['event'] == 'FIXTURE_ACCEPTED_UNKNOWN':
                    manifest = entry['manifest']
                    break
        assert manifest['process_id'] == process.pid != os.getpid()
        context = cold_service_context(manifest)
        before = await joint_money(database[0], context.config.account_id)
        assert len(manifest['orders']) == 1
        attempt, = await database[0].fetch('SELECT state,wire_id,product FROM life_financial.send_attempts WHERE account_id=$1', context.config.account_id)
        assert attempt['state'] == 'SEND_UNKNOWN' and attempt['product'] == product
        assert attempt['wire_id'] == manifest['orders'][0]['clOrdId']
        began = monotonic()
        process.send_signal(getattr(signal, termination))
        stdout, stderr = await asyncio.wait_for(process.communicate(), 3)
        assert monotonic() - began < 2  # Explicit synthetic .4-second supervisor budget.
        assert process.returncode == (2 if termination == 'SIGTERM' else -signal.SIGKILL)
        events = [json.loads(line)['event'] for line in b''.join(lines).splitlines() + stdout.splitlines()]
        assert 'STOPPED' not in events and 'FIXTURE_FINANCIAL_CLOSE_RETURNED' not in events
        assert ('FIXTURE_FINANCIAL_REVOKED' in events) == (termination == 'SIGTERM')
        assert b'private-test-password' not in stderr + stdout
        assert await joint_money(database[0], context.config.account_id) == before
        service = await context.reopen(False)
        assert not service._trading_ready()
        assert len(await service.source.capital.claims(context.config.binding)) == 1
        with pytest.raises(StorageError, match='PG_SERVICE_TRADING_NOT_QUALIFIED' if product == 'SPOT' else 'PG_SERVICE_HEDGE_NOT_ENABLED'):
            if product == 'SPOT':
                await service.quote('whole-service-original', TradeType.BUY, 0)
            else:
                await service.execute_hedge('whole-service-hedge')
        assert all(call['method'] == 'GET' for call in context.calls)
        assert await joint_money(database[0], context.config.account_id) == before
    finally:
        if process.returncode is None:
            process.kill()
        await process.wait()
        if service is not None:
            await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('product', ['SPOT', 'SWAP'])
async def test_actual_signal_cancels_acked_order_while_launch_is_blocked_without_settling_it(database, product):
    env = {key: os.environ[key] for key in ('PATH', 'PYTHONPATH') if key in os.environ}
    env.update(LIFE_ENV_FILE='', LIFE_AUTO_MIGRATE='false', LIFE_SERVICE_CONFIG='/tmp/synthetic-injected-native.yml',
               LIFE_SOURCE_FACTORY='trusted:unused', LIFE_TICK_SECONDS='.1', LIFE_STARTUP_TIMEOUT_SECONDS='30',
               LIFE_SHUTDOWN_TIMEOUT_SECONDS='.4', LIFE_STATUS_HOST='127.0.0.1', LIFE_STATUS_PORT='0',
               LIFE_TELEMETRY_INTERVAL_SECONDS='.05')
    process = await asyncio.create_subprocess_exec(
        sys.executable, '-m', 'test.hummingbot.strategy_v2.life_liquidity.storage.postgres.worker_cancel_process',
        env=env, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    lines = []
    try:
        process.stdin.write(json.dumps(dict(migration_dsn=database[2], root_dsn=database[1], product=product)).encode())
        await process.stdin.drain()
        process.stdin.close()
        async with asyncio.timeout(45):
            while True:
                line = await process.stdout.readline()
                assert line, 'worker failed before its native ACK/blocked SDK boundary'
                lines.append(json.loads(line))
                if lines[-1]['event'] == 'FIXTURE_ACKED_BLOCKED':
                    frame = lines[-1]
                    break
        account_id = UUID(frame['account_id'])
        before = await joint_money(database[0], account_id)
        attempt = dict(await database[0].fetchrow('SELECT * FROM life_financial.send_attempts WHERE account_id=$1', account_id))
        assert attempt['state'] == 'ACKED'
        started = monotonic()
        process.send_signal(signal.SIGTERM)
        stdout, _ = await asyncio.wait_for(process.communicate(), 3)
        assert monotonic() - started < 2 and process.returncode == 2
        lines.extend(json.loads(line) for line in stdout.splitlines())
        cancels = [line['request'] for line in lines if line['event'] == 'FIXTURE_EXACT_CANCEL']
        assert cancels == [dict(instId=frame['order']['instId'], clOrdId=attempt['wire_id'], ordId=attempt['exchange_order_id'])]
        assert not any(line['event'] == 'STOPPED' for line in lines)
        assert dict(await database[0].fetchrow('SELECT * FROM life_financial.send_attempts WHERE account_id=$1', account_id)) == attempt
        assert await joint_money(database[0], account_id) == before
    finally:
        if process.returncode is None:
            process.kill()
        await process.wait()
