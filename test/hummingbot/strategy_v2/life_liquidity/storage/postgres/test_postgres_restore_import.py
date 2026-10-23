"""Controlled restore bridges original ownership only to recovery observation."""
import asyncio
import json
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_ownership import managed
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_restore import (
    LIMITS,
    captured,
    isolate,
    request,
)
from test.hummingbot.strategy_v2.life_liquidity.storage.test_generation_exclusion import proof
from uuid import uuid4

import asyncpg
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown, OwnerToken, StorageError
from hummingbot.strategy_v2.life_liquidity.storage.generation_exclusion import CredentialExclusionVerifier
from hummingbot.strategy_v2.life_liquidity.storage.postgres.restore import PgRestoreQuarantine
from hummingbot.strategy_v2.life_liquidity.storage.recovery import SpotFinancialRecoveryFacts


async def prepared(database):
    private = Ed25519PrivateKey.generate()
    authority = CredentialExclusionVerifier('synthetic-provider', private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw))
    f, cfg, owner, _, lease, _, _, keys = await managed(database, handoff_role=True,
                                                        configuration_extra={'credential_authority': authority.policy()})
    head = await lease.claim('original-process')
    await f.close()
    manifest = await captured(database)
    command = request(manifest)
    await isolate(database, drain=True)
    try:
        await PgRestoreQuarantine(database[0], limits=LIMITS).quarantine(command, manifest=manifest)
    except StorageError as error:
        if str(error) == 'PG_RESTORE_TARGET_NOT_ISOLATED':
            # Bounded diagnostics only: no query text, connection secrets,
            # retries or relaxed production isolation predicate.
            rows = await database[0].fetch('''SELECT pid,backend_type,usename,state FROM pg_stat_activity
                WHERE datid=(SELECT oid FROM pg_database WHERE datname=current_database()) AND pid<>pg_backend_pid()''')
            print('restore_isolation_backends=' + json.dumps([dict(r) for r in rows]))
        raise
    target_cfg = replace(cfg, database_lineage=command.target_lineage, principal='life_pg2_reporter',
                         dsn=cfg.dsn.replace('life_pg2_runtime@', 'life_pg2_reporter@'))
    now = await database[0].fetchval('SELECT clock_timestamp()')
    actor_owner = OwnerToken(uuid4(), uuid4(), owner.epoch, owner.session_id, now, now + timedelta(seconds=60))
    _, _, capability, _, _, certificate, _ = proof(cfg=target_cfg, source=owner, target=actor_owner,
                                                   grant=head.grant_id, epoch=head.lease_epoch, private=private)
    # Synthetic assertion is valid for the bounded maintenance operation; this
    # is not an exchange credential revocation or a live threshold.
    from test.hummingbot.strategy_v2.life_liquidity.storage.test_generation_exclusion import sign
    certificate = sign(replace(certificate, expires_at=now + timedelta(seconds=30)), private)
    return cfg, target_cfg, owner, keys, authority, capability, certificate, manifest, command


async def authorization(database, preview, keys, payload):
    binding = preview.cut.checkpoint.binding
    at = await database[0].fetchval('SELECT clock_timestamp()')
    balances = {r['asset']: r['total'] for r in await database[0].fetch('SELECT asset,total FROM life_financial.balances')}
    facts = SpotFinancialRecoveryFacts(binding, at, balances['LIFE'], balances['USDT'], Decimal(0), Decimal(0),
                                       (), (), (), (), False, False, False)
    args = dict(issued_at=at - timedelta(seconds=1), expires_at=at + timedelta(seconds=5))
    recover = keys.sign('operator', binding, 'RECOVER', preview.command.json(), preview.cut, facts, **args)
    handoff = keys.sign('operator', binding, 'HANDOFF', payload, preview.cut, facts, **args)
    return facts, recover, handoff


def assert_aborted_import(before, after):
    # PostgreSQL nextval is not transactional. Keep consumed floors; never
    # reset sequence history to make a rollback image byte-identical.
    assert (after.lineage, after.server_major, after.tables, after.schema.classes) == (
        before.lineage, before.server_major, before.tables, before.schema.classes)
    for old, new in zip(before.schema.sequences, after.schema.sequences, strict=True):
        assert old.name == new.name and new.last_value >= old.last_value
        assert not old.is_called or new.is_called


@pytest.mark.asyncio
async def test_signed_restore_import_keeps_money_and_original_owner_without_activation(database):
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.restore_import import PgRestoreRecoveryImporter
    cfg, target, owner, keys, authority, cap, certificate, manifest, command = await prepared(database)
    c = database[0]
    economic = await c.fetch('SELECT * FROM life_financial.balances ORDER BY asset')
    grants = await c.fetch('SELECT * FROM life_financial.activation_grants')
    importer = PgRestoreRecoveryImporter(c, limits=LIMITS, operator_verifier=keys.verifier(), credential_verifier=authority)

    async def authorize(preview):
        payload = importer.request(preview, cap.registration, certificate)
        return await authorization(database, preview, keys, payload)
    result = await importer.import_recovery(command, manifest=manifest, account_id=cfg.account_id,
                                            registration=cap.registration, certificate=certificate, authorize=authorize)
    assert not result.trading_permission and not result.replayed
    row = await c.fetchrow('SELECT * FROM life_financial.accounts')
    assert row['mode'] == 'RECOVERY_ONLY' and row['owner_process'] == owner.process_id
    assert row['current_session'] == owner.session_id and row['database_lineage'] == command.target_lineage
    assert await c.fetch('SELECT * FROM life_financial.balances ORDER BY asset') == economic
    assert await c.fetch('SELECT * FROM life_financial.activation_grants') == grants
    assert not await c.fetchval("SELECT can_write FROM life_financial.access_bindings WHERE principal='life_pg2_runtime'")
    assert await c.fetchval("SELECT life_financial.control_verified($1)", cfg.account_id)
    assert await c.fetchval('SELECT restore_operation_id FROM life_storage.storage_metadata') is None
    cold = await importer.lookup(command, manifest=manifest, account_id=cfg.account_id)
    assert cold.replayed and not cold.trading_permission
    audit = json.loads(await c.fetchval('SELECT body FROM life_storage.restore_recovery_imports'))
    assert audit['context']['restore'] == command.json()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['signature', 'handoff', 'credential', 'callback', 'old-principal', 'foreign-account', 'stale-facts', 'policy'])
async def test_failed_signed_import_retains_exact_quarantine(database, fault):
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.restore_import import PgRestoreRecoveryImporter
    cfg, target, owner, keys, authority, cap, certificate, manifest, command = await prepared(database)
    before = await captured(database)
    importer = PgRestoreRecoveryImporter(database[0], limits=LIMITS, operator_verifier=keys.verifier(), credential_verifier=authority)
    if fault == 'credential':
        certificate = replace(certificate, rest_denied=False)
    registration = cap.registration
    if fault == 'old-principal':
        registration = replace(registration, principal=cfg.principal)
    elif fault == 'foreign-account':
        registration = replace(registration, account_id=uuid4())
    elif fault == 'policy':
        importer.credential_verifier = CredentialExclusionVerifier('foreign-provider', authority.public_key)

    async def authorize(preview):
        if fault == 'callback':
            raise StorageError('SYNTHETIC_OPERATOR_UNAVAILABLE')
        facts, recover, handoff = await authorization(database, preview, keys, importer.request(preview, registration, certificate))
        if fault == 'stale-facts':
            facts = replace(facts, observed_at=facts.observed_at - timedelta(seconds=2))
        if fault == 'signature':
            recover = replace(recover, signature='0' * 128)
        if fault == 'handoff':
            handoff = replace(handoff, signature='0' * 128)
        return facts, recover, handoff
    with pytest.raises(StorageError):
        await importer.import_recovery(command, manifest=manifest, account_id=cfg.account_id,
                                       registration=registration, certificate=certificate, authorize=authorize)
    assert await captured(database) == before


@pytest.mark.asyncio
@pytest.mark.parametrize('committed', [False, True])
async def test_unknown_restore_import_commit_resolves_only_by_cold_readback(database, monkeypatch, committed):
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.restore_import import PgRestoreRecoveryImporter
    cfg, target, owner, keys, authority, cap, certificate, manifest, command = await prepared(database)
    before = await captured(database)
    importer = PgRestoreRecoveryImporter(database[0], limits=LIMITS, operator_verifier=keys.verifier(), credential_verifier=authority)

    async def authorize(preview):
        return await authorization(database, preview, keys, importer.request(preview, cap.registration, certificate))

    async def lost(transaction):
        if committed:
            await transaction.commit()
        else:
            await transaction.rollback()
        raise ConnectionError('synthetic lost response')
    monkeypatch.setattr(importer, '_commit', lost)
    with pytest.raises(CommitUnknown):
        await importer.import_recovery(command, manifest=manifest, account_id=cfg.account_id,
                                       registration=cap.registration, certificate=certificate, authorize=authorize)
    cold = PgRestoreRecoveryImporter(database[0], limits=LIMITS, operator_verifier=keys.verifier(), credential_verifier=authority)
    result = await cold.lookup(command, manifest=manifest, account_id=cfg.account_id)
    if committed:
        assert result.replayed and not result.trading_permission
        assert await database[0].fetchval('SELECT mode FROM life_financial.accounts') == 'RECOVERY_ONLY'
    else:
        assert result is None
        assert_aborted_import(before, await captured(database))


@pytest.mark.asyncio
async def test_restore_import_rechecks_approval_freshness_at_physical_commit(database, monkeypatch):
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.restore_import import PgRestoreRecoveryImporter
    cfg, target, owner, keys, authority, cap, certificate, manifest, command = await prepared(database)
    before = await captured(database)
    importer = PgRestoreRecoveryImporter(database[0], limits=LIMITS, operator_verifier=keys.verifier(), credential_verifier=authority)

    async def authorize(preview):
        return await authorization(database, preview, keys, importer.request(preview, cap.registration, certificate))

    async def expired(transaction):
        # Original fixture's 1-s fact age is unchanged. The deferred native
        # and restore guards, not a Python pre-check, must refuse this COMMIT.
        await asyncio.sleep(1.05)
        await transaction.commit()
    monkeypatch.setattr(importer, '_commit', expired)
    with pytest.raises(StorageError, match='COMMIT|EXPIRED'):
        await importer.import_recovery(command, manifest=manifest, account_id=cfg.account_id,
                                       registration=cap.registration, certificate=certificate, authorize=authorize)
    assert_aborted_import(before, await captured(database))


@pytest.mark.asyncio
async def test_restore_import_outer_handoff_expiry_is_checked_separately_at_commit(database, monkeypatch):
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.restore_import import PgRestoreRecoveryImporter
    cfg, target, owner, keys, authority, cap, certificate, manifest, command = await prepared(database)
    before = await captured(database)
    importer = PgRestoreRecoveryImporter(database[0], limits=LIMITS, operator_verifier=keys.verifier(), credential_verifier=authority)

    async def authorize(preview):
        payload = importer.request(preview, cap.registration, certificate)
        facts, recover, _ = await authorization(database, preview, keys, payload)
        short = keys.sign('operator', cfg.binding, 'HANDOFF', payload, preview.cut, facts,
                          issued_at=facts.observed_at - timedelta(seconds=1), expires_at=facts.observed_at + timedelta(milliseconds=200))
        return facts, recover, short

    async def expired(transaction):
        await asyncio.sleep(.25)
        await transaction.commit()
    monkeypatch.setattr(importer, '_commit', expired)
    with pytest.raises(StorageError, match='RESTORE_RECOVERY_COMMIT_UNQUALIFIED'):
        await importer.import_recovery(command, manifest=manifest, account_id=cfg.account_id,
                                       registration=cap.registration, certificate=certificate, authorize=authorize)
    assert_aborted_import(before, await captured(database))


@pytest.mark.asyncio
async def test_cancelled_import_retains_physical_slot_and_cannot_admit_a_second_import(database):
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.restore_import import PgRestoreRecoveryImporter
    cfg, target, owner, keys, authority, cap, certificate, manifest, command = await prepared(database)
    importer = PgRestoreRecoveryImporter(database[0], limits=LIMITS, operator_verifier=keys.verifier(), credential_verifier=authority)
    entered, release = asyncio.Event(), asyncio.Event()

    async def authorize(preview):
        entered.set()
        await release.wait()
        return await authorization(database, preview, keys, importer.request(preview, cap.registration, certificate))
    task = asyncio.create_task(importer.import_recovery(command, manifest=manifest, account_id=cfg.account_id,
                                                        registration=cap.registration, certificate=certificate, authorize=authorize))
    await asyncio.wait_for(entered.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    try:
        with pytest.raises(StorageError, match='BACKPRESSURE'):
            await importer.lookup(command, manifest=manifest, account_id=cfg.account_id)
    finally:
        release.set()
        await importer.close()
    assert (await importer.lookup(command, manifest=manifest, account_id=cfg.account_id)).replayed
    assert await database[0].fetchval('SELECT mode FROM life_financial.accounts') == 'RECOVERY_ONLY'


@pytest.mark.asyncio
async def test_killed_import_backend_rolls_back_principal_owner_marker_audit_and_money(database, monkeypatch):
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.backup import capture_database
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.restore_import import PgRestoreRecoveryImporter
    cfg, target, owner, keys, authority, cap, certificate, manifest, command = await prepared(database)
    c = database[0]
    before = await captured(database)
    root, name = database[1].rsplit('/', 1)
    admin = await asyncpg.connect(root + '/life_pg2_test')
    cold = None
    importer = PgRestoreRecoveryImporter(c, limits=LIMITS, operator_verifier=keys.verifier(), credential_verifier=authority)

    async def authorize(preview):
        return await authorization(database, preview, keys, importer.request(preview, cap.registration, certificate))

    async def killed(transaction):
        assert await c.fetchval('SELECT count(*) FROM life_storage.restore_recovery_imports') == 1
        assert await admin.fetchval('SELECT pg_terminate_backend($1)', c.get_server_pid())
        await transaction.commit()
    monkeypatch.setattr(importer, '_commit', killed)
    try:
        with pytest.raises(CommitUnknown):
            await importer.import_recovery(command, manifest=manifest, account_id=cfg.account_id,
                                           registration=cap.registration, certificate=certificate, authorize=authorize)
        await admin.execute(f'ALTER DATABASE "{name}" ALLOW_CONNECTIONS true')
        cold = await asyncpg.connect(database[2])
        await admin.execute(f'ALTER DATABASE "{name}" ALLOW_CONNECTIONS false')
        assert_aborted_import(before, await capture_database(cold, limits=LIMITS))
        assert await cold.fetchval('SELECT restore_operation_id FROM life_storage.storage_metadata') == command.operation_id
        assert await cold.fetchval('SELECT count(*) FROM life_storage.restore_recovery_imports') == 0
    finally:
        if cold is not None:
            await cold.close()
        await admin.close()


@pytest.mark.asyncio
async def test_manual_marker_clear_still_cannot_give_the_old_runtime_write_access(database):
    cfg, *_ = await prepared(database)
    c = database[0]
    await c.execute('UPDATE life_storage.storage_metadata SET restore_operation_id=NULL')
    # Owner-side raw edits cannot manufacture the signed import origin. This
    # check runs as the original runtime login on its own fresh DB connection.
    root, name = database[1].rsplit('/', 1)
    admin = await asyncpg.connect(root + '/life_pg2_test')
    runtime = None
    try:
        await admin.execute(f'ALTER DATABASE "{name}" ALLOW_CONNECTIONS true')
        runtime = await asyncpg.connect(cfg.dsn)
        assert not await runtime.fetchval('SELECT life_financial.can_access($1,true)', cfg.account_id)
        assert not await runtime.fetchval('SELECT life_financial.can_access($1,false)', cfg.account_id)
    finally:
        if runtime is not None:
            await runtime.close()
        await admin.execute(f'ALTER DATABASE "{name}" ALLOW_CONNECTIONS false')
        await admin.close()


@pytest.mark.asyncio
async def test_restore_recovery_cannot_pin_the_operator_as_its_own_credential_authority(database):
    from hummingbot.strategy_v2.life_liquidity.storage.control_plane import OperatorKeys
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.restore_import import PgRestoreRecoveryImporter
    keys = OperatorKeys({'operator': b'x' * 32}, {'operator': frozenset(('RECOVER', 'HANDOFF'))}).verifier()
    with pytest.raises(StorageError, match='INDEPENDENT_AUTHORITY_REQUIRED'):
        PgRestoreRecoveryImporter(database[0], limits=LIMITS, operator_verifier=keys,
                                  credential_verifier=CredentialExclusionVerifier('synthetic-provider', keys.keys['operator']))
