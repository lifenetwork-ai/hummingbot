"""Native SWAP post-auth bytes; synthetic account and fake HTTP only."""
import hashlib
import json
from dataclasses import replace
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_swap import setup

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
from hummingbot.strategy_v2.life_liquidity.storage.postgres.swap_send import ArmSwapRequest, PgSwapBoundary


async def prepared(database):
    values, swap, command = await setup(database, carry_options=dict(refresh_initial_facts=True))
    financial, cfg, owner = values[:3]
    await database[0].execute('INSERT INTO life_financial.swap_boundary_policies VALUES($1,$2,1000,64,1048576)', cfg.account_id, cfg.binding.policy_hash)
    assert (await swap.prepare(command, cut=await swap.cut(cfg.binding), owner=owner)).state == 'PREPARED'
    wire = json.dumps(dict(instId='LIFE-USDT-SWAP', tdMode='cross', ordType='post_only', clOrdId='SwapWire1',
                           side='sell', sz='4', px='1', posSide='short'))
    return values, swap, command, ArmSwapRequest(command.operation_id, wire)


@pytest.mark.asyncio
async def test_exact_swap_wire_and_arm_publish_atomically_and_readback_cannot_send_again(database):
    values, swap, command, captured = await prepared(database)
    financial, cfg, owner = values[:3]
    try:
        repo = PgSwapBoundary(swap)
        before = await financial.balance(cfg.binding)
        result = await repo.arm(captured, cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        assert result.state == 'SEND_UNKNOWN' and not result.replayed and not result.trading_permission
        assert result.wire_sha256 == hashlib.sha256(captured.wire_json.encode()).hexdigest()
        assert result.checked_at_ms < result.valid_until_ms
        assert (await repo.lookup(command.operation_id, cfg.binding)).replayed
        with pytest.raises(StorageError, match='RECONCILE_BEFORE_RETRY'):
            await repo.arm(captured, cut=(await financial.observation_cut(cfg.binding)).cut, owner=owner)
        assert await financial.balance(cfg.binding) == before
        assert len(await values[5].claims(cfg.binding)) == 1
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.swap_boundaries') == 1
        assert await database[0].fetchval('SELECT state FROM life_financial.send_attempts') == 'SEND_UNKNOWN'
        await swap.acknowledge(command.operation_id, exchange_order_id='123456', owner=owner)
        assert (await repo.lookup(command.operation_id, cfg.binding)).replayed
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('change', [dict(sz='1'), dict(px='2'), dict(posSide='long'), dict(reduceOnly=True), dict(tdMode='cash')])
async def test_swap_wire_cannot_change_contract_units_side_or_mode(database, change):
    values, swap, command, captured = await prepared(database)
    financial, cfg, owner = values[:3]
    try:
        wire = {**json.loads(captured.wire_json), **change}
        with pytest.raises(StorageError, match='SWAP_WIRE'):
            await PgSwapBoundary(swap).arm(replace(captured, wire_json=json.dumps(wire)), cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        assert await database[0].fetchval('SELECT state FROM life_financial.send_attempts') == 'PREPARED'
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.swap_boundaries') == 0
    finally:
        await financial.close()


async def transport(database):
    from datetime import datetime, timezone
    from decimal import Decimal
    from test.hummingbot.core.web_assistant.connections.test_request_guard import Response, Session, Throttler
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from hummingbot.connector.derivative.okx_perpetual.okx_perpetual_derivative import OkxPerpetualDerivative
    from hummingbot.core.data_type.common import OrderType, PositionAction, PositionMode, TradeType
    from hummingbot.core.web_assistant.connections.rest_connection import RESTConnection
    from hummingbot.core.web_assistant.rest_assistant import RESTAssistant
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.swap_transport import PgSwapAccountSource

    values, swap, command, captured = await prepared(database)
    financial, cfg, owner = values[:3]
    connector = OkxPerpetualDerivative(okx_perpetual_api_key='synthetic-key', okx_perpetual_secret_key='synthetic-secret',
                                       okx_perpetual_passphrase='synthetic-pass', trading_pairs=[], trading_required=False)
    connector.enable_protected_trading_pair('LIFE-USDT')
    connector._contract_sizes['LIFE-USDT'] = Decimal('.25')
    connector._perpetual_trading.set_position_mode(PositionMode.HEDGE)
    connector.exchange_symbol_associated_to_pair = AsyncMock(return_value='LIFE-USDT-SWAP')
    session, throttle = Session(), Throttler()
    throttle.release.set()
    assistant = RESTAssistant(RESTConnection(session), throttle, auth=connector.authenticator)
    connector._web_assistants_factory = SimpleNamespace(get_rest_assistant=AsyncMock(return_value=assistant))
    response_body = [dict(code='0', data=[dict(sCode='0', ordId='123456', clOrdId='SwapWire1')])]

    async def send(**kwargs):
        session.sent.append(kwargs)
        response = Response()

        async def body():
            return dict(code='0', data=[dict(uid='12345')]) if kwargs['url'].endswith('/account/config') else response_body[0]
        response.json = body
        return response
    session.request = send
    ready = [True]
    source = await PgSwapAccountSource.create(PgSwapBoundary(swap), connector=connector,
                                              clock=lambda: datetime.now(timezone.utc), local_fence=lambda: ready[0],
                                              max_uid_age_ms=1000, max_auth_age_ms=1000, max_clock_skew_ms=250,
                                              collection_timeout_ms=1000, max_pending=2)
    guard = source.guard(command.operation_id)

    async def place(**kwargs):
        selected = kwargs.pop('guard', guard)
        return await connector._place_order(order_id='SwapWire1', trading_pair='LIFE-USDT', amount=Decimal('1'),
                                            trade_type=TradeType.SELL, order_type=OrderType.LIMIT_MAKER, price=Decimal('1'),
                                            position_action=PositionAction.OPEN, request_send_guard=selected,
                                            async_on_ack=selected.acknowledge, **kwargs)
    return SimpleNamespace(**locals())


@pytest.mark.asyncio
async def test_signed_derivative_connector_arms_exact_contract_bytes_and_acks_once(database):
    t = await transport(database)
    try:
        assert (await t.place())[0] == '123456'
        orders = [s for s in t.session.sent if s['method'] == 'POST']
        assert len(orders) == 1 and json.loads(orders[0]['data'])['sz'] == '4'
        assert orders[0]['allow_redirects'] is False
        receipt = await t.source.boundary.lookup(t.command.operation_id, t.cfg.binding)
        assert receipt.wire_sha256 == hashlib.sha256(orders[0]['data']).hexdigest()
        assert await database[0].fetchval('SELECT state FROM life_financial.send_attempts') == 'ACKED'
        with pytest.raises(StorageError, match='ALREADY_USED'):
            await t.place()
        with pytest.raises(StorageError, match='ALREADY_USED'):
            await t.guard.acknowledge('123456')
        with pytest.raises(StorageError, match='RECONCILE_BEFORE_RETRY'):
            await t.place(guard=t.source.guard(t.command.operation_id))
        assert len([s for s in t.session.sent if s['method'] == 'POST']) == 1
    finally:
        await t.financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('phase', ['throttle', 'arm_return', 'final_check', 'expiry_return', 'source_revoke'])
async def test_stop_or_expiry_never_sends_but_arm_is_not_refunded(database, monkeypatch, phase):
    import asyncio
    from datetime import timedelta
    t = await transport(database)
    try:
        kwargs = {}
        if phase == 'throttle':
            t.throttle.release.clear()
        elif phase in ('arm_return', 'expiry_return', 'source_revoke'):
            original = t.source.boundary.arm

            async def stop(*args, **kw):
                result = await original(*args, **kw)
                if phase == 'arm_return':
                    t.ready[0] = False
                elif phase == 'source_revoke':
                    t.source.revoke()
                else:
                    old = t.source.clock()
                    t.source.clock = lambda: old + timedelta(seconds=2)
                return result
            monkeypatch.setattr(t.source.boundary, 'arm', stop)
        else:
            kwargs['pre_send_check'] = lambda _: t.ready.__setitem__(0, False)
        task = asyncio.create_task(t.place(**kwargs))
        if phase == 'throttle':
            await asyncio.wait_for(t.throttle.entered.wait(), 2)
            t.ready[0] = False
            t.throttle.release.set()
        with pytest.raises((StorageError, PermissionError)):
            await task
        assert not any(s['method'] == 'POST' for s in t.session.sent)
        assert await database[0].fetchval('SELECT state FROM life_financial.send_attempts') == ('PREPARED' if phase == 'throttle' else 'SEND_UNKNOWN')
        assert len(await t.values[5].claims(t.cfg.binding)) == 1
        with pytest.raises(StorageError, match='ACK_BEFORE_SEND'):
            await t.guard.acknowledge('123456')
    finally:
        await t.financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('body', [dict(code='1', data=[]), dict(code='0', data=[dict(sCode='0', ordId='123456', clOrdId='wrong')]),
                                  dict(code='0', data=[dict(sCode='0', ordId='not-numeric', clOrdId='SwapWire1')]),
                                  dict(code='0', data=[dict(sCode='0', ordId='123456', clOrdId='SwapWire1')] * 2)])
async def test_malformed_exchange_ack_remains_unknown_without_retry(database, body):
    t = await transport(database)
    try:
        t.response_body[0] = body
        with pytest.raises(IOError, match='ORDER_ACK_UNAVAILABLE'):
            await t.place()
        assert await database[0].fetchval('SELECT state FROM life_financial.send_attempts') == 'SEND_UNKNOWN'
        assert len([s for s in t.session.sent if s['method'] == 'POST']) == 1
    finally:
        await t.financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['deadline', 'generation'])
async def test_swap_boundary_rechecks_deadline_and_owner_at_physical_commit(database, monkeypatch, fault):
    import asyncio
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.helpers import install_generation_fault
    values, swap, command, captured = await prepared(database)
    financial, cfg, owner = values[:3]
    repo = PgSwapBoundary(swap)
    if fault == 'generation':
        await install_generation_fault(database)
    original = repo.runtime._commit
    reached = []

    async def changed(tx):
        row = await tx._connection.fetchrow('SELECT checked_at_ms,valid_until_ms FROM life_financial.swap_boundaries')
        assert row is not None
        reached.append(True)
        if fault == 'deadline':
            now = await tx._connection.fetchval('SELECT floor(extract(epoch FROM clock_timestamp())*1000)::bigint')
            await asyncio.sleep(max(0, (row['valid_until_ms'] - now) / 1000) + .05)
        else:
            await tx._connection.execute('SELECT life_financial.test_change_generation($1)', cfg.account_id)
        await original(tx)
    monkeypatch.setattr(repo.runtime, '_commit', changed)
    try:
        with pytest.raises(StorageError):
            await repo.arm(captured, cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        assert reached == [True]
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.swap_boundaries') == 0
        assert await database[0].fetchval('SELECT state FROM life_financial.send_attempts') == 'PREPARED'
        assert len(await values[5].claims(cfg.binding)) == 1
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['raw_body', 'receipt'])
async def test_corrupted_wire_or_original_receipt_blocks_readback_and_finance(database, fault):
    values, swap, command, captured = await prepared(database)
    financial, cfg, owner = values[:3]
    repo = PgSwapBoundary(swap)
    try:
        await repo.arm(captured, cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        if fault == 'raw_body':
            await database[0].execute('ALTER TABLE life_financial.swap_boundaries DISABLE TRIGGER immutable_history')
            await database[0].execute("UPDATE life_financial.swap_boundaries SET wire_json=wire_json||' ',wire_sha256=encode(sha256(convert_to(wire_json||' ','UTF8')),'hex')")
        else:
            await database[0].execute('ALTER TABLE life_financial.operations DISABLE TRIGGER immutable_history')
            await database[0].execute("UPDATE life_financial.operations SET receipt=jsonb_set(receipt,'{checked_at_ms}','0') WHERE command->>'kind'='SWAP_BOUNDARY'")
        with pytest.raises(StorageError, match='UNVERIFIED'):
            await repo.lookup(command.operation_id, cfg.binding)
        assert (await financial.snapshot(cfg.binding)).recovery_required
        assert await database[0].fetchval('SELECT state FROM life_financial.send_attempts') == 'SEND_UNKNOWN'
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('committed', [False, True])
async def test_backend_termination_keeps_exact_wire_and_arm_atomic_without_replay_grant(database, monkeypatch, committed):
    import asyncpg

    from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.shared_capital import PgSharedCapitalRepository
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.swap import PgSwapRepository

    values, swap, command, captured = await prepared(database)
    financial, cfg, owner = values[:3]
    before = await financial.balance(cfg.binding)
    original = swap.runtime._commit
    admin = await asyncpg.connect(database[1])

    async def lost(tx):
        pid = tx._connection.get_server_pid()
        if committed:
            await original(tx)
        assert await admin.fetchval('SELECT pg_terminate_backend($1)', pid)
        raise ConnectionError('synthetic boundary lost reply')
    monkeypatch.setattr(swap.runtime, '_commit', lost)
    try:
        with pytest.raises(CommitUnknown):
            await PgSwapBoundary(swap).arm(captured, cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        assert not swap.runtime.healthy
    finally:
        await admin.close()
        await financial.close()
    cold = PgSpotRepository(await PgRuntime.open(cfg))
    try:
        boundary = PgSwapBoundary(PgSwapRepository(PgSharedCapitalRepository(cold, owner=owner, observation=values[5].observation, max_rows=64, max_bytes=1048576)))
        saved = await boundary.lookup(command.operation_id, cfg.binding)
        assert (saved is not None) is committed
        assert await database[0].fetchval('SELECT state FROM life_financial.send_attempts') == ('SEND_UNKNOWN' if committed else 'PREPARED')
        assert await cold.balance(cfg.binding) == before
        if saved:
            assert saved.replayed and not saved.trading_permission
            with pytest.raises(StorageError, match='RECONCILE_BEFORE_RETRY'):
                await boundary.arm(captured, cut=(await cold.observation_cut(cfg.binding)).cut, owner=owner)
    finally:
        await cold.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['wrong_uid', 'bad_signature', 'wrong_environment', 'changed_body'])
async def test_signed_uid_auth_and_exact_bytes_cannot_be_replaced(database, fault):
    t = await transport(database)
    try:
        if fault == 'wrong_uid':
            original = t.session.request

            async def changed(**kwargs):
                response = await original(**kwargs)
                if kwargs['url'].endswith('/account/config'):
                    async def body():
                        return dict(code='0', data=[dict(uid='99999')])
                    response.json = body
                return response
            t.session.request = changed
        else:
            original_auth = t.assistant._auth.rest_authenticate

            async def corrupt(request):
                request = await original_auth(request)
                if request.method.value == 'POST':
                    if fault == 'bad_signature':
                        request.headers['OK-ACCESS-SIGN'] = 'invalid'
                    elif fault == 'wrong_environment':
                        request.headers['x-simulated-trading'] = '1'
                    else:
                        body = json.loads(request.data)
                        body['sz'] = '1'
                        request.data = json.dumps(body)
                        request = await original_auth(request)
                return request
            t.assistant._auth.rest_authenticate = corrupt
        with pytest.raises((StorageError, PermissionError)):
            await t.place()
        assert not any(s['method'] == 'POST' for s in t.session.sent)
        assert await database[0].fetchval('SELECT state FROM life_financial.send_attempts') == 'PREPARED'
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.swap_boundaries') == 0
    finally:
        await t.financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('committed', [False, True])
@pytest.mark.parametrize('phase', ['arm', 'capture', 'apply', 'delivery'])
async def test_real_process_exit_preserves_boundary_without_cold_send_authority(database, committed, phase):
    import asyncio
    import sys
    from dataclasses import fields

    from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.shared_capital import PgSharedCapitalRepository
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.swap import PgSwapRepository

    child = r'''
import asyncio,json,os,sys
from uuid import UUID
from hummingbot.strategy_v2.life_liquidity.storage.contracts import OwnerToken,StateBinding
from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgConfig,PgRuntime
from hummingbot.strategy_v2.life_liquidity.storage.postgres.shared_capital import PgSharedCapitalRepository
from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
from hummingbot.strategy_v2.life_liquidity.storage.postgres.swap import PgSwapRepository
from hummingbot.strategy_v2.life_liquidity.storage.postgres.swap_send import ArmSwapRequest,PgSwapBoundary
from hummingbot.strategy_v2.life_liquidity.storage.postgres.swap_ack import PgSwapAckIngress
print('READY',flush=True)
data=json.load(sys.stdin)
async def run():
    cfg=data['config']; cfg['binding']=StateBinding.from_json(cfg['binding'])
    for key in ('account_id','database_lineage'): cfg[key]=UUID(cfg[key])
    owner=OwnerToken.from_json(data['owner']); financial=PgSpotRepository(await PgRuntime.open(PgConfig(**cfg)))
    async def never_observe(): raise AssertionError('readback/arm does not collect new source facts')
    capital=PgSharedCapitalRepository(financial,owner=owner,observation=never_observe,max_rows=64,max_bytes=1048576)
    boundary=PgSwapBoundary(PgSwapRepository(capital)); original=boundary.runtime._commit; count=0
    target={'arm':1,'capture':1,'apply':2,'delivery':3}[data['phase']]
    async def die(tx):
        nonlocal count
        count+=1
        if count!=target: return await original(tx)
        if data['committed']: await original(tx)
        os._exit(73)
    boundary.runtime._commit=die
    command=ArmSwapRequest.from_json(data['command'])
    if data['phase']=='arm':
        await boundary.arm(command,cut=(await financial.snapshot(cfg['binding'])).cut,owner=owner)
    else:
        ingress=PgSwapAckIngress(boundary)
        received=await ingress.capture(command.prepare_operation_id,'123456',wire_sha256=data['wire_sha256'])
        await ingress.deliver(received)
asyncio.run(run())
'''
    process = await asyncio.create_subprocess_exec(sys.executable, '-c', child, stdin=asyncio.subprocess.PIPE,
                                                   stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    financial = None
    try:
        # Start the interpreter before capturing the original financial sources.
        assert await asyncio.wait_for(process.stdout.readline(), 15) == b'READY\n'
        values, swap, command, captured = await prepared(database)
        financial, cfg, owner = values[:3]
        config = {f.name: getattr(cfg, f.name) for f in fields(cfg)}
        config.update(account_id=str(cfg.account_id), database_lineage=str(cfg.database_lineage), binding=cfg.binding.json())
        data = dict(config=config, owner=owner.json(), command=captured.json(), committed=committed, phase=phase,
                    wire_sha256=hashlib.sha256(captured.wire_json.encode()).hexdigest())
        if phase != 'arm':
            await PgSwapBoundary(swap).arm(captured, cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        _, stderr = await asyncio.wait_for(process.communicate(json.dumps(data).encode()), 15)
        assert process.returncode == 73, stderr.decode()
        await financial.close()
        financial = None
        cold = PgSpotRepository(await PgRuntime.open(cfg))
        try:
            boundary = PgSwapBoundary(PgSwapRepository(PgSharedCapitalRepository(cold, owner=owner, observation=values[5].observation,
                                                                                 max_rows=64, max_bytes=1048576)))
            receipt = await boundary.lookup(command.operation_id, cfg.binding)
            assert (receipt is not None) is (committed or phase != 'arm')
            if receipt:
                assert receipt.replayed and not receipt.trading_permission
                assert receipt.wire_sha256 == hashlib.sha256(captured.wire_json.encode()).hexdigest()
                with pytest.raises(StorageError, match='RECONCILE_BEFORE_RETRY'):
                    await boundary.arm(captured, cut=(await cold.observation_cut(cfg.binding)).cut, owner=owner)
            from hummingbot.strategy_v2.life_liquidity.storage.postgres.swap_ack import PgSwapAckIngress
            ingress = PgSwapAckIngress(boundary)
            pending = await ingress.pending()
            if phase != 'arm':
                assert len(pending) == (0 if (phase == 'capture' and not committed or phase == 'delivery' and committed) else 1)
                for row in pending:
                    assert (await ingress.deliver(row)).state == 'ACKED'
                assert not await ingress.pending()
                expected = 'SEND_UNKNOWN' if phase == 'capture' and not committed else 'ACKED'
            else:
                expected = 'SEND_UNKNOWN' if committed else 'PREPARED'
            assert await database[0].fetchval('SELECT state FROM life_financial.send_attempts') == expected
            assert len(await boundary.swap.capital.claims(cfg.binding)) == 1
            assert (await cold.balance(cfg.binding)).total_quote_usdt == 10
        finally:
            await cold.close()
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
        if financial is not None:
            await financial.close()


@pytest.mark.asyncio
async def test_ack_capture_survives_native_application_failure_and_repairs_without_resend(database, monkeypatch):
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.swap_ack import PgSwapAckIngress
    t = await transport(database)
    ingress = PgSwapAckIngress(t.source.boundary)
    original = t.swap.acknowledge

    async def fail(*args, **kwargs):
        raise StorageError('SYNTHETIC_APPLICATION_FAILURE')
    monkeypatch.setattr(t.swap, 'acknowledge', fail)
    try:
        with pytest.raises(StorageError, match='SYNTHETIC_APPLICATION_FAILURE'):
            await t.place()
        assert await database[0].fetchval('SELECT state FROM life_financial.send_attempts') == 'SEND_UNKNOWN'
        pending = await ingress.pending()
        assert len(pending) == 1 and pending[0].exchange_order_id == '123456'
        assert not t.swap.runtime.healthy
        monkeypatch.setattr(t.swap, 'acknowledge', original)
        assert (await ingress.deliver(pending[0])).state == 'ACKED'
        assert not await ingress.pending()
        assert (await ingress.deliver(pending[0])).replayed
        assert not t.swap.runtime.healthy
        assert len([s for s in t.session.sent if s['method'] == 'POST']) == 1
        assert len(await t.values[5].claims(t.cfg.binding)) == 1
    finally:
        await t.financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('phase', ['capture', 'apply', 'delivery'])
@pytest.mark.parametrize('committed', [False, True])
async def test_received_ack_backend_loss_repairs_original_response_once(database, monkeypatch, phase, committed):
    import asyncpg

    from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.shared_capital import PgSharedCapitalRepository
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.swap import PgSwapRepository
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.swap_ack import PgSwapAckIngress

    t = await transport(database)
    original, count = t.swap.runtime._commit, 0
    target = dict(capture=2, apply=3, delivery=4)[phase]  # ARM commits first.
    admin = await asyncpg.connect(database[1])

    async def lost(tx):
        nonlocal count
        count += 1
        if count != target:
            return await original(tx)
        pid = tx._connection.get_server_pid()
        if committed:
            await original(tx)
        assert await admin.fetchval('SELECT pg_terminate_backend($1)', pid)
        raise ConnectionError('synthetic accepted-response lost COMMIT reply')
    monkeypatch.setattr(t.swap.runtime, '_commit', lost)
    try:
        with pytest.raises(CommitUnknown):
            await t.place()
        assert count == target and not t.swap.runtime.healthy
        assert len([s for s in t.session.sent if s['method'] == 'POST']) == 1
    finally:
        await admin.close()
        await t.financial.close()
    cold = PgSpotRepository(await PgRuntime.open(t.cfg))
    try:
        capital = PgSharedCapitalRepository(cold, owner=t.owner, observation=t.values[5].observation, max_rows=64, max_bytes=1048576)
        boundary = PgSwapBoundary(PgSwapRepository(capital))
        ingress = PgSwapAckIngress(boundary)
        pending = await ingress.pending()
        assert len(pending) == (0 if (phase == 'capture' and not committed or phase == 'delivery' and committed) else 1)
        for row in pending:
            assert (await ingress.deliver(row)).state == 'ACKED'
            assert (await ingress.deliver(row)).replayed
        assert not await ingress.pending()
        expected = 'SEND_UNKNOWN' if phase == 'capture' and not committed else 'ACKED'
        assert await database[0].fetchval('SELECT state FROM life_financial.send_attempts') == expected
        assert (await cold.balance(t.cfg.binding)).total_quote_usdt == 10
        assert len(await capital.claims(t.cfg.binding)) == 1
        assert (await boundary.lookup(t.command.operation_id, t.cfg.binding)).replayed
        assert len([s for s in t.session.sent if s['method'] == 'POST']) == 1
    finally:
        await cold.close()


@pytest.mark.asyncio
async def test_concurrent_boundary_attempts_publish_once_and_cannot_refund_or_resend(database):
    import asyncio
    values, swap, command, captured = await prepared(database)
    financial, cfg, owner = values[:3]
    try:
        repo = PgSwapBoundary(swap)
        cut = (await financial.snapshot(cfg.binding)).cut
        results = await asyncio.gather(*(repo.arm(captured, cut=cut, owner=owner) for _ in range(2)), return_exceptions=True)
        assert sum(not isinstance(r, BaseException) for r in results) == 1
        assert sum(isinstance(r, StorageError) for r in results) == 1
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.swap_boundaries') == 1
        assert await database[0].fetchval('SELECT state FROM life_financial.send_attempts') == 'SEND_UNKNOWN'
        assert len(await values[5].claims(cfg.binding)) == 1
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_boundary_wire_retention_budget_refuses_before_arming(database):
    values, swap, command, captured = await prepared(database)
    financial, cfg, owner = values[:3]
    try:
        await database[0].execute('ALTER TABLE life_financial.swap_boundary_policies DISABLE TRIGGER immutable_history')
        await database[0].execute('UPDATE life_financial.swap_boundary_policies SET max_total_bytes=1')
        with pytest.raises(StorageError, match='BOUNDARY_BOUND'):
            await PgSwapBoundary(swap).arm(captured, cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.swap_boundaries') == 0
        assert await database[0].fetchval('SELECT state FROM life_financial.send_attempts') == 'PREPARED'
    finally:
        await financial.close()


@pytest.mark.parametrize('wire', ['{}', '[]', 'null', '{"sz":"1","sz":"2"}', '{"px":NaN}', '\ud800', 'x' * 4097])
def test_wire_decoder_refuses_ambiguous_or_unbounded_payloads(wire):
    with pytest.raises(StorageError, match='SWAP_WIRE_INVALID'):
        ArmSwapRequest('prepare1', wire)


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['wire', 'exchange_id'])
async def test_unqualified_received_ack_cannot_commit_financial_identity(database, fault):
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.swap_ack import PgSwapAckIngress
    values, swap, command, captured = await prepared(database)
    financial, cfg, owner = values[:3]
    boundary = PgSwapBoundary(swap)
    try:
        result = await boundary.arm(captured, cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        ingress = PgSwapAckIngress(boundary)
        await ingress.capture(command.operation_id, '123456', wire_sha256='0' * 64 if fault == 'wire' else result.wire_sha256)
        # Even a direct native ACK must not conflict with retained raw evidence.
        with pytest.raises(StorageError):
            await swap.acknowledge(command.operation_id, exchange_order_id='999999' if fault == 'exchange_id' else '123456', owner=owner)
        assert await database[0].fetchval('SELECT state FROM life_financial.send_attempts') == 'SEND_UNKNOWN'
        assert len(await ingress.pending()) == 1
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_local_stop_cannot_be_undone_by_new_guard_or_fence_flip(database):
    t = await transport(database)
    try:
        t.ready[0] = False
        with pytest.raises((StorageError, PermissionError)):
            await t.place()
        t.ready[0] = True
        with pytest.raises((StorageError, PermissionError)):
            await t.place(guard=t.source.guard(t.command.operation_id))
        assert not any(s['method'] == 'POST' for s in t.session.sent)
        assert await database[0].fetchval('SELECT state FROM life_financial.send_attempts') == 'PREPARED'
    finally:
        await t.financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('phase', ['ack', 'delivery'])
async def test_primary_ack_time_before_received_capture_cannot_publish_identity(database, phase):
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.swap_ack import PgSwapAckIngress
    values, swap, command, captured = await prepared(database)
    financial, cfg, owner = values[:3]
    boundary = PgSwapBoundary(swap)
    try:
        result = await boundary.arm(captured, cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        ingress = PgSwapAckIngress(boundary)
        await ingress.capture(command.operation_id, '123456', wire_sha256=result.wire_sha256)
        if phase == 'delivery':
            await swap.acknowledge(command.operation_id, exchange_order_id='123456', owner=owner)
        # Privileged fault seam: represent a capture made before a primary clock
        # rollback, without changing the host/system clock or any time budget.
        await database[0].execute('ALTER TABLE life_financial.swap_ack_ingress DISABLE TRIGGER immutable_history')
        await database[0].execute("""WITH future AS (SELECT clock_timestamp()+interval '5 seconds' AS at),changed AS (
            SELECT account_id,prepare_operation_id,at,body||jsonb_build_object('captured_at',at) AS b
            FROM life_financial.swap_ack_ingress CROSS JOIN future)
            UPDATE life_financial.swap_ack_ingress r SET captured_at=c.at,body=c.b,
                source_hash=encode(sha256(convert_to(c.b::text,'UTF8')),'hex') FROM changed c
            WHERE r.account_id=c.account_id AND r.prepare_operation_id=c.prepare_operation_id""")
        if phase == 'delivery':
            await database[0].execute('ALTER TABLE life_financial.operations DISABLE TRIGGER immutable_history')
            await database[0].execute("UPDATE life_financial.operations SET recorded_at=(SELECT captured_at+interval '1 millisecond' FROM life_financial.swap_ack_ingress) WHERE command->>'kind'='SWAP_ACK'")
        with pytest.raises(StorageError):
            if phase == 'ack':
                await swap.acknowledge(command.operation_id, exchange_order_id='123456', owner=owner)
            else:
                await ingress.deliver((await ingress.pending())[0])
        assert await database[0].fetchval('SELECT state FROM life_financial.send_attempts') == ('SEND_UNKNOWN' if phase == 'ack' else 'ACKED')
        assert len(await ingress.pending()) == 1
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('signature', [
    'life_financial.swap_boundary_arm(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb)',
    'life_financial.swap_capture_ack(uuid,jsonb)', 'life_financial.swap_mark_ack(uuid,text)',
])
async def test_admission_detects_new_mutating_endpoint_grant_drift(database, signature):
    values, swap, _, _ = await prepared(database)
    financial, cfg = values[:2]
    try:
        await database[0].execute('REVOKE EXECUTE ON FUNCTION ' + signature + ' FROM life_pg2_runtime')
        with pytest.raises(StorageError, match='PG_ROLE_PROFILE_INVALID'):
            await financial.snapshot(cfg.binding)
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_reporter_cannot_gain_an_unchecked_swap_capture_entrypoint(database):
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
    values, _, _, _ = await prepared(database)
    financial, cfg = values[:2]
    reader = None
    try:
        readonly = replace(cfg, principal='life_pg2_reporter', dsn=cfg.dsn.replace('life_pg2_runtime@', 'life_pg2_reporter@'), write=False)
        reader = PgSpotRepository(await PgRuntime.open(readonly))
        await reader.snapshot(cfg.binding)
        await database[0].execute('GRANT EXECUTE ON FUNCTION life_financial.swap_capture_ack(uuid,jsonb) TO life_pg2_reporter')
        with pytest.raises(StorageError, match='PG_ROLE_PROFILE_INVALID'):
            await reader.snapshot(cfg.binding)
        assert not reader._runtime.healthy
    finally:
        if reader is not None:
            await reader.close()
        await financial.close()
