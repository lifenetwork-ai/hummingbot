"""Real isolated archive recovery fixture; no File data or exchange requests."""
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_backup import tools
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_control import evidence
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_restore import LIMITS, request
from test.hummingbot.strategy_v2.life_liquidity.storage.test_generation_exclusion import proof, sign
from time import perf_counter
from types import SimpleNamespace
from uuid import uuid4

import asyncpg

from hummingbot.strategy_v2.life_liquidity.storage.contracts import OwnerToken
from hummingbot.strategy_v2.life_liquidity.storage.postgres.archive_tools import PgLogicalBackup, restore_archive
from hummingbot.strategy_v2.life_liquidity.storage.postgres.backup import capture_database
from hummingbot.strategy_v2.life_liquidity.storage.postgres.control import PgControlRepository
from hummingbot.strategy_v2.life_liquidity.storage.postgres.recovery_actor import PgRecoveryActor, PgRecoveryGrantIssuer
from hummingbot.strategy_v2.life_liquidity.storage.postgres.restore import PgRestoreQuarantine
from hummingbot.strategy_v2.life_liquidity.storage.postgres.restore_import import PgRestoreRecoveryImporter
from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
from hummingbot.strategy_v2.life_liquidity.storage.recovery import SpotFinancialRecoveryFacts


@asynccontextmanager
async def recovered(database, tmp_path, financial, cfg, source_owner, keys, private, verifier, *, limits=LIMITS):
    """Backup/verify everything, then issue only a fresh observation lease."""
    await financial.close()
    c = database[0]
    start = perf_counter()
    bundle = await PgLogicalBackup(c, tools=tools(await c.fetchval('SELECT current_database()')), limits=limits).create(tmp_path / 'native-backup')
    backup_ms = (perf_counter() - start) * 1000
    root, name = database[1].rsplit('/', 1)[0], 'recovery_matrix_' + uuid4().hex
    admin = await asyncpg.connect(root + '/life_pg2_test')
    created, restored, observer, actor = False, None, None, None
    try:
        await admin.execute(f'CREATE DATABASE "{name}" OWNER life_pg2_migrator')
        created = True
        await admin.execute(f'REVOKE CONNECT ON DATABASE "{name}" FROM PUBLIC')
        owner_dsn = root.replace('postgres@', 'life_pg2_migrator@') + '/' + name
        restored = await asyncpg.connect(owner_dsn)
        start = perf_counter()
        await restore_archive(restored, tools=tools(name), bundle=bundle, limits=limits)
        await admin.execute(f'ALTER DATABASE "{name}" ALLOW_CONNECTIONS false')
        cmd = request(bundle.image)
        await PgRestoreQuarantine(restored, limits=limits).quarantine(cmd, manifest=bundle.image)
        quarantine_ms = (perf_counter() - start) * 1000
        after = await capture_database(restored, limits=limits)
        changed = {'life_financial.accounts', 'life_storage.storage_metadata', 'life_storage.restore_history'}
        assert tuple(t for t in after.tables if t.name not in changed) == tuple(t for t in bundle.image.tables if t.name not in changed)
        target_cfg = replace(cfg, database_lineage=cmd.target_lineage, principal='life_pg2_reporter',
                             dsn=root.replace('postgres@', 'life_pg2_reporter@') + '/' + name)
        db = restored, root + '/' + name, owner_dsn
        now = await restored.fetchval('SELECT clock_timestamp()')
        owner = OwnerToken(uuid4(), uuid4(), source_owner.epoch, source_owner.session_id, now, now + timedelta(seconds=30))
        head = await restored.fetchrow('SELECT grant_id,lease_epoch FROM life_financial.process_leases')
        _, _, cap, _, _, cert, _ = proof(cfg=target_cfg, source=source_owner, target=owner,
                                         grant=head['grant_id'], epoch=head['lease_epoch'], private=private)
        cert = sign(replace(cert, expires_at=owner.expires_at), private)
        importer = PgRestoreRecoveryImporter(restored, limits=limits, operator_verifier=keys.verifier(), credential_verifier=verifier)

        async def authorize(preview):
            at = await restored.fetchval('SELECT clock_timestamp()')
            balances = {r['asset']: r['total'] for r in await restored.fetch('SELECT asset,total FROM life_financial.balances')}
            trades = tuple(r['trade_id'] for r in await restored.fetch('SELECT DISTINCT trade_id FROM life_financial.fills ORDER BY trade_id'))
            bills = tuple(r['bill_id'] for r in await restored.fetch('SELECT bill_id FROM life_financial.cashflows ORDER BY bill_id'))
            facts = SpotFinancialRecoveryFacts(cfg.binding, at, balances['LIFE'], balances['USDT'], Decimal(0), Decimal(0),
                                               trades, bills, (), (), False, False, False)
            expiry = dict(issued_at=at - timedelta(seconds=1), expires_at=at + timedelta(seconds=5))
            return (facts, keys.sign('operator', cfg.binding, 'RECOVER', preview.command.json(), preview.cut, facts, **expiry),
                    keys.sign('operator', cfg.binding, 'HANDOFF', importer.request(preview, cap.registration, cert), preview.cut, facts, **expiry))
        start = perf_counter()
        receipt = await importer.import_recovery(cmd, manifest=bundle.image, account_id=cfg.account_id,
                                                 registration=cap.registration, certificate=cert, authorize=authorize)
        assert not receipt.trading_permission
        import_ms = (perf_counter() - start) * 1000
        await admin.execute(f'GRANT CONNECT ON DATABASE "{name}" TO life_pg2_reporter')
        await admin.execute(f'ALTER DATABASE "{name}" ALLOW_CONNECTIONS true')
        observer = PgSpotRepository(await PgRuntime.open(target_cfg))
        control = PgControlRepository(restored, observer, verifier=keys.verifier())
        issuer = PgRecoveryGrantIssuer(control, credential_verifier=verifier)
        grant = uuid4()

        async def register(registration):
            certificate = sign(replace(cert, target_capability_hash=registration.capability_hash), private)
            cut, facts = await evidence(db, observer, target_cfg)
            facts = replace(facts, order_scope_complete=False, bill_scope_complete=False, runner_scope_complete=False)
            payload = issuer.request('matrix-observer', grant, registration, certificate, source_owner)
            approval = keys.sign('operator', cfg.binding, 'HANDOFF', payload, cut, facts,
                                 issued_at=facts.observed_at - timedelta(seconds=1), expires_at=facts.observed_at + timedelta(seconds=5))
            await issuer.register('matrix-observer', grant, registration, source_owner=source_owner,
                                  certificate=certificate, approval=approval, facts=facts, cut=cut)
        start = perf_counter()
        actor = await PgRecoveryActor.open(target_cfg, owner=owner, claim_operation_id='matrix-observer-claim', heartbeat_interval_ms=250, register=register)
        actor_ms = (perf_counter() - start) * 1000
        result = SimpleNamespace(db=db, c=restored, cfg=target_cfg, actor=actor, control=control, observer=observer, importer=importer,
                                 request=cmd, bundle=bundle, source_owner=source_owner, actor_owner=owner, certificate=cert,
                                 timing=dict(backup_ms=backup_ms, restore_verify_quarantine_ms=quarantine_ms, import_ms=import_ms, actor_ms=actor_ms))

        async def restart_observation():
            nonlocal actor
            await actor.close()
            previous = await restored.fetchrow('SELECT grant_id,lease_epoch FROM life_financial.process_leases')
            now = await restored.fetchval('SELECT clock_timestamp()')
            replacement = OwnerToken(uuid4(), uuid4(), source_owner.epoch, source_owner.session_id, now, now + timedelta(seconds=30))
            next_grant = uuid4()

            async def register_replacement(registration):
                certificate = proof(cfg=target_cfg, source=result.actor_owner, target=replacement,
                                    grant=previous['grant_id'], epoch=previous['lease_epoch'], private=private)[5]
                certificate = sign(replace(certificate, source=result.certificate.target,
                                           target=replace(certificate.target, service_identity='service:replacement', credential_resource='secret:replacement:1'),
                                           target_capability_hash=registration.capability_hash), private)
                cut, facts = await evidence(db, observer, target_cfg)
                facts = replace(facts, order_scope_complete=False, bill_scope_complete=False, runner_scope_complete=False)
                payload = issuer.request('matrix-cold-observer', next_grant, registration, certificate, source_owner)
                approval = keys.sign('operator', cfg.binding, 'HANDOFF', payload, cut, facts,
                                     issued_at=facts.observed_at - timedelta(seconds=1), expires_at=facts.observed_at + timedelta(seconds=5))
                await issuer.register('matrix-cold-observer', next_grant, registration, source_owner=source_owner,
                                      certificate=certificate, approval=approval, facts=facts, cut=cut)
            actor = await PgRecoveryActor.open(target_cfg, owner=replacement, claim_operation_id='matrix-cold-observer-claim', heartbeat_interval_ms=250,
                                               register=register_replacement)
            result.actor = actor
            return actor
        result.restart_observation = restart_observation
        yield result
    finally:
        if actor is not None:
            await actor.close()
        if observer is not None:
            await observer.close()
        if restored is not None:
            await restored.close()
        if created:
            await admin.execute(f'DROP DATABASE "{name}" WITH (FORCE)')
        await admin.close()
