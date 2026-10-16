"""Accepted ACK capture survives unavailable/unqualified financial application."""
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_quote_transport import transport

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError, StorageUnavailable
from hummingbot.strategy_v2.life_liquidity.storage.postgres.ack_ingress import PgAckIngress


@pytest.mark.asyncio
async def test_received_ack_is_durable_before_financial_application_failure(database, monkeypatch):
    t = await transport(database)

    async def failed(*args, **kwargs):
        raise StorageUnavailable('PG_SYNTHETIC_ACCOUNTING_FAILURE')
    monkeypatch.setattr(t.repo, 'acknowledge', failed)
    try:
        with pytest.raises(StorageError, match='PG_SYNTHETIC_ACCOUNTING_FAILURE'):
            await t.place()
        assert len(t.session.sent) == 1
        row = await database[0].fetchrow('SELECT ingress_id,body,captured_at FROM life_financial.spot_ack_ingress')
        assert row is not None
        received = await PgAckIngress(t.repo).lookup(row['ingress_id'])
        assert received.command.exchange_order_id == '123' and received.command.observed_at == row['captured_at']
        assert not received.trading_permission
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.spot_ack_ingress_receipts') == 0
        assert await database[0].fetchval('SELECT state FROM life_financial.send_attempts') == 'SEND_UNKNOWN'
        assert (await t.repo.snapshot(t.cfg.binding)).recovery_required
        with pytest.raises(StorageError, match='PG_REQUEST_GUARD_ALREADY_USED'):
            await t.place()
        assert len(t.session.sent) == 1
    finally:
        await t.close()


@pytest.mark.asyncio
async def test_successful_ack_application_is_receipted_without_releasing_money_or_rearming(database):
    t = await transport(database)
    try:
        assert (await t.place())[0] == '123'
        row = await database[0].fetchrow('SELECT ingress_id FROM life_financial.spot_ack_ingress')
        received = await PgAckIngress(t.repo).lookup(row['ingress_id'])
        assert not received.trading_permission and received.replayed
        replay = await PgAckIngress(t.repo).mark_applied(received, owner=t.owner)
        assert replay.replayed and not replay.trading_permission
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.spot_ack_ingress_receipts') == 1
        assert await database[0].fetchval('SELECT state FROM life_financial.send_attempts') == 'ACKED'
        assert await database[0].fetchval("SELECT reserved FROM life_financial.balances WHERE asset='USDT'") > 0
        assert not (await t.repo.snapshot(t.cfg.binding)).recovery_required
        assert len(t.session.sent) == 1
    finally:
        await t.close()


@pytest.mark.asyncio
async def test_received_ack_capture_remains_available_when_financial_history_is_corrupt(database):
    t = await transport(database)
    original = t.session.request

    async def corrupted(**kwargs):
        response = await original(**kwargs)
        # A privileged accidental inconsistency after the accepted fake HTTP;
        # raw receipt capture must not run financial invariant guards.
        await database[0].execute("ALTER TABLE life_financial.spot_preparations DISABLE TRIGGER USER")
        await database[0].execute("UPDATE life_financial.spot_preparations SET fee_quote=fee_quote+1")
        await database[0].execute("ALTER TABLE life_financial.spot_preparations ENABLE TRIGGER USER")
        return response
    t.session.request = corrupted
    try:
        with pytest.raises(StorageError):
            await t.place()
        assert len(t.session.sent) == 1
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.spot_ack_ingress') == 1
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.spot_ack_ingress_receipts') == 0
        assert await database[0].fetchval('SELECT state FROM life_financial.send_attempts') == 'SEND_UNKNOWN'
    finally:
        await t.close()


async def pending_ack(database, monkeypatch):
    t = await transport(database)

    async def failed(*args, **kwargs):
        raise StorageUnavailable('PG_SYNTHETIC_ACCOUNTING_FAILURE')
    monkeypatch.setattr(t.repo, 'acknowledge', failed)
    with pytest.raises(StorageError, match='PG_SYNTHETIC_ACCOUNTING_FAILURE'):
        await t.place()
    ingress_id = await database[0].fetchval('SELECT ingress_id FROM life_financial.spot_ack_ingress')
    received = await PgAckIngress(t.repo).lookup(ingress_id)
    return t, received


@pytest.mark.asyncio
async def test_unapplied_ack_denies_even_generic_allocation_and_cold_repair_never_sends(database, monkeypatch):
    import json

    import asyncpg

    from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
    t, received = await pending_ack(database, monkeypatch)
    financial = PgSpotRepository(await PgRuntime.open(t.cfg))
    c = await asyncpg.connect(t.cfg.dsn)
    try:
        version = await database[0].fetchval('SELECT version FROM life_financial.accounts')
        with pytest.raises(asyncpg.RaiseError, match='ACK_INGRESS_RECOVERY_REQUIRED'):
            await c.fetchval('SELECT life_financial.allocate_balance($1,$2::jsonb,$3::jsonb,$4,$5,$6,$7,$8)',
                             t.cfg.account_id, json.dumps(t.cfg.binding.json()), json.dumps(t.owner.json()), version,
                             'allocation-after-unapplied-ack', 'f' * 64, 'USDT', 1)
        ingress = PgAckIngress(financial)
        assert await ingress.pending(t.cfg.binding, max_rows=8, max_bytes=1048576) == (received,)
        observed = await financial.observation_cut(t.cfg.binding)
        await financial.acknowledge(received.command, cut=observed.cut, owner=received.owner)
        await ingress.mark_applied(received, owner=received.owner)
        assert await ingress.pending(t.cfg.binding, max_rows=8, max_bytes=1048576) == ()
        assert not (await financial.snapshot(t.cfg.binding)).recovery_required
        assert (await financial.balance(t.cfg.binding)).reserved_quote_usdt > 0
        assert len(t.session.sent) == 1
    finally:
        await c.close()
        await financial.close()
        await t.close()


@pytest.mark.asyncio
async def test_untrusted_ingress_exception_cannot_skip_guards_for_financial_sql(database):
    t = await transport(database)
    try:
        with pytest.raises(StorageError, match='PG_UNTRUSTED_INGRESS_QUERY_DENIED'):
            await t.repo._runtime._transaction('SELECT life_financial.spot_arm($1,$2,$3,$4,$5,$6,$7)', (),
                                               t.owner, lambda value: value, observation=True, untrusted_ingress=True)
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.spot_ack_ingress') == 0
        assert await database[0].fetchval('SELECT state FROM life_financial.send_attempts') == 'PREPARED'
        assert not t.session.sent
    finally:
        await t.close()


@pytest.mark.asyncio
async def test_untrusted_capture_does_not_qualify_an_unrelated_accounting_operation(database, monkeypatch):
    t, received = await pending_ack(database, monkeypatch)
    try:
        # Nothing can clear this fact merely because its captured exchange ID is
        # syntactically valid; the original financial ACK receipt is mandatory.
        with pytest.raises(StorageError, match='PG_ACK_INGRESS_APPLICATION_UNQUALIFIED'):
            await PgAckIngress(t.repo).mark_applied(received, owner=t.owner)
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.spot_ack_ingress_receipts') == 0
        assert len(t.session.sent) == 1
    finally:
        await t.close()


@pytest.mark.asyncio
async def test_new_ingress_invalidates_cut_even_when_account_version_and_pending_flag_are_unchanged(database, monkeypatch):
    from dataclasses import replace
    t, received = await pending_ack(database, monkeypatch)
    try:
        before = await t.repo.snapshot(t.cfg.binding)
        another = replace(t.preparation.prepare, operation_id='unqualified-original-2', intent_id='unknown-intent-2', client_order_id='unknownwire2')
        extra = await PgAckIngress(t.repo).capture(another, '999', owner=t.owner, request_sha256='f' * 64)
        after = await t.repo.snapshot(t.cfg.binding)
        assert before.cut.checkpoint.revision == after.cut.checkpoint.revision
        assert before.recovery_required and after.recovery_required and before.cut != after.cut
        with pytest.raises(StorageError, match='PG_ACK_INGRESS_APPLICATION_UNQUALIFIED'):
            await PgAckIngress(t.repo).mark_applied(extra, owner=t.owner)
        assert len(await PgAckIngress(t.repo).pending(t.cfg.binding, max_rows=8, max_bytes=1048576)) == 2
    finally:
        await t.close()


@pytest.mark.asyncio
async def test_read_only_role_can_read_original_ack_but_not_capture_or_deliver(database, monkeypatch):
    from dataclasses import replace

    from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
    t, received = await pending_ack(database, monkeypatch)
    cfg = replace(t.cfg, dsn=t.cfg.dsn.replace('life_pg2_runtime@', 'life_pg2_reporter@'), principal='life_pg2_reporter', write=False)
    financial = PgSpotRepository(await PgRuntime.open(cfg))
    try:
        reader = PgAckIngress(financial)
        assert await reader.lookup(received.ingress_id) == received
        with pytest.raises(StorageError, match='PG_ACK_INGRESS_WRITE_REQUIRED'):
            await reader.capture(t.preparation.prepare, '123', owner=t.owner, request_sha256='f' * 64)
        with pytest.raises(StorageError, match='PG_ACK_INGRESS_WRITE_REQUIRED'):
            await reader.mark_applied(received, owner=t.owner)
    finally:
        await financial.close()
        await t.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('phase', ['capture', 'delivery'])
@pytest.mark.parametrize('committed', [False, True])
@pytest.mark.parametrize('fault', ['backend', 'process'])
async def test_real_ack_commit_faults_repair_original_without_resend_or_refund(database, monkeypatch, phase, committed, fault):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.ack_process import exit_ack

    import asyncpg

    from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown, fingerprint
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository

    t = await transport(database)
    admin = await asyncpg.connect(database[1])
    cold = None
    original_commit = t.repo._runtime._commit
    ingress_id = 'rawack:' + __import__('hashlib').sha256(('ack:' + fingerprint(t.preparation.prepare.json())).encode()).hexdigest()

    async def lost(tx):
        pid = tx._connection.get_server_pid()
        if committed:
            await original_commit(tx)
        assert await admin.fetchval('SELECT pg_terminate_backend($1)', pid)
        raise ConnectionError('synthetic ACK COMMIT reply loss')

    if fault == 'backend':
        target = t.guard._ingress.capture if phase == 'capture' else t.guard._ingress.mark_applied

        async def boundary(*args, **kwargs):
            monkeypatch.setattr(t.repo._runtime, '_commit', lost)
            return await target(*args, **kwargs)
    else:
        async def boundary(*args, **kwargs):
            await exit_ack(t.cfg, t.preparation.prepare, t.owner, t.guard._request_sha256, ingress_id, phase, committed)
            raise CommitUnknown('PG_COMMIT_UNKNOWN')
    monkeypatch.setattr(t.guard._ingress, 'capture' if phase == 'capture' else 'mark_applied', boundary)
    try:
        with pytest.raises(CommitUnknown, match='PG_COMMIT_UNKNOWN'):
            await t.place()
        assert len(t.session.sent) == 1
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.spot_ack_ingress') == (int(committed) if phase == 'capture' else 1)
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.spot_ack_ingress_receipts') == (int(committed) if phase == 'delivery' else 0)
        assert await database[0].fetchval('SELECT state FROM life_financial.send_attempts') == ('ACKED' if phase == 'delivery' else 'SEND_UNKNOWN')
        cold = PgSpotRepository(await PgRuntime.open(t.cfg))
        ingress = PgAckIngress(cold)
        received = await ingress.lookup(ingress_id)
        if received is None:
            # An uncommitted raw capture is recaptured with its SAME original
            # request/response identity, never by repeating the HTTP request.
            received = await ingress.capture(t.preparation.prepare, '123', owner=t.owner, request_sha256=t.guard._request_sha256)
        if phase == 'capture':
            observed = await cold.observation_cut(t.cfg.binding)
            await cold.acknowledge(received.command, cut=observed.cut, owner=received.owner)
        await ingress.mark_applied(received, owner=received.owner)
        await ingress.mark_applied(received, owner=received.owner)
        assert await ingress.pending(t.cfg.binding, max_rows=8, max_bytes=1048576) == ()
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.spot_ack_ingress_receipts') == 1
        assert (await cold.balance(t.cfg.binding)).reserved_quote_usdt > 0
        with pytest.raises(StorageError, match='PG_REQUEST_GUARD_ALREADY_USED'):
            await t.place()
        assert len(t.session.sent) == 1
    finally:
        if cold is not None:
            await cold.close()
        await admin.close()
        await t.close()


@pytest.mark.asyncio
async def test_pending_ack_reads_bound_payload_before_decoding_and_identity_replay_conflicts(database, monkeypatch):
    from dataclasses import replace
    t, received = await pending_ack(database, monkeypatch)
    ingress = PgAckIngress(t.repo)
    try:
        with pytest.raises(StorageError, match='PG_ACK_INGRESS_READ_BOUND$'):
            await ingress.pending(t.cfg.binding, max_rows=8, max_bytes=8)
        replay = await ingress.capture(t.preparation.prepare, '123', owner=t.owner, request_sha256=t.guard._request_sha256)
        assert replay == received and not replay.trading_permission
        with pytest.raises(StorageError, match='PG_ACK_INGRESS_IDENTITY_CONFLICT'):
            await ingress.capture(t.preparation.prepare, '456', owner=t.owner, request_sha256=t.guard._request_sha256)
        another = replace(t.preparation.prepare, operation_id='unqualified-2', intent_id='unknown-intent-2', client_order_id='unknownwire2')
        await ingress.capture(another, '999', owner=t.owner, request_sha256='f' * 64)
        with pytest.raises(StorageError, match='PG_ACK_INGRESS_READ_BOUND$'):
            await ingress.pending(t.cfg.binding, max_rows=1, max_bytes=1048576)
        assert len(t.session.sent) == 1
    finally:
        await t.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('phase', ['arm_return', 'final_check'])
async def test_received_fact_epoch_invalidates_earlier_authorization_before_http(database, monkeypatch, phase):
    t = await transport(database)
    try:
        kwargs = {}
        if phase == 'arm_return':
            original = t.repo.arm_quote_action

            async def captured(*args, **kw):
                result = await original(*args, **kw)
                await PgAckIngress(t.repo).capture(t.preparation.prepare, '123', owner=t.owner, request_sha256=result.wire_sha256)
                return result
            monkeypatch.setattr(t.repo, 'arm_quote_action', captured)
        else:
            def received_fact(_):
                t.repo._runtime._ingress_epoch += 1
            kwargs['pre_send_check'] = received_fact
        with pytest.raises((StorageError, PermissionError)):
            await t.place(**kwargs)
        assert not t.session.sent
        assert (await t.repo.balance(t.cfg.binding)).reserved_quote_usdt > 0
        assert (await t.repo.attempt('intent1', t.cfg.binding)).state.value == 'SEND_UNKNOWN'
    finally:
        await t.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['raw', 'delivery', 'request_sha'])
async def test_changed_ingress_or_delivery_provenance_denies_readback_and_new_risk(database, monkeypatch, fault):
    import json

    from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
    t, received = await pending_ack(database, monkeypatch)
    try:
        if fault == 'raw':
            await database[0].execute('ALTER TABLE life_financial.spot_ack_ingress DISABLE TRIGGER USER')
            await database[0].execute("UPDATE life_financial.spot_ack_ingress SET body=jsonb_set(body,'{command,exchange_order_id}','\"999\"')")
            await database[0].execute('ALTER TABLE life_financial.spot_ack_ingress ENABLE TRIGGER USER')
        else:
            observed = await t.repo.observation_cut(t.cfg.binding)
            await PgSpotRepository.acknowledge(t.repo, received.command, cut=observed.cut, owner=t.owner)
            if fault == 'request_sha':
                # A correctly hashed but unrelated diagnostic capture cannot
                # become delivery proof for the accepted wire body.
                await database[0].execute('ALTER TABLE life_financial.spot_ack_ingress DISABLE TRIGGER USER')
                await database[0].execute("UPDATE life_financial.spot_ack_ingress SET body=jsonb_set(body,'{request_sha256}',$1::jsonb)", json.dumps('f' * 64))
                await database[0].execute("UPDATE life_financial.spot_ack_ingress SET source_hash=encode(sha256(convert_to(body::text,'UTF8')),'hex')")
                await database[0].execute('ALTER TABLE life_financial.spot_ack_ingress ENABLE TRIGGER USER')
                with pytest.raises(StorageError, match='PG_ACK_INGRESS_APPLICATION_UNQUALIFIED'):
                    await PgAckIngress(t.repo).mark_applied(received, owner=t.owner)
                assert await database[0].fetchval('SELECT count(*) FROM life_financial.spot_ack_ingress_receipts') == 0
                return
            await PgAckIngress(t.repo).mark_applied(received, owner=t.owner)
            await database[0].execute('ALTER TABLE life_financial.spot_ack_ingress_receipts DISABLE TRIGGER USER')
            await database[0].execute("UPDATE life_financial.spot_ack_ingress_receipts SET source_hash=$1", 'f' * 64)
            await database[0].execute('ALTER TABLE life_financial.spot_ack_ingress_receipts ENABLE TRIGGER USER')
        with pytest.raises(StorageError, match='PG_ACK_INGRESS_UNVERIFIED'):
            await PgAckIngress(t.repo).lookup(received.ingress_id)
        assert (await t.repo.snapshot(t.cfg.binding)).recovery_required
        assert len(t.session.sent) == 1
    finally:
        await t.close()
