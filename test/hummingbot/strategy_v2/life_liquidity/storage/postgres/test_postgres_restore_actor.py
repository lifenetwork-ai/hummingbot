"""Real archive restore, signed observation recovery and a distinct risk owner."""
import json
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_backup import tools
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_control import evidence, signed
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_financial_commands import order
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_lifecycle import terminal
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_recovery_actor import setup
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_restore import LIMITS, request
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_restore_import import authorization
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_spot_observations import ack, filled
from test.hummingbot.strategy_v2.life_liquidity.storage.test_generation_exclusion import proof, sign
from uuid import uuid4

import asyncpg
import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import OwnerMode, OwnerToken, StorageError
from hummingbot.strategy_v2.life_liquidity.storage.control_plane import OwnerTransition
from hummingbot.strategy_v2.life_liquidity.storage.postgres.archive_tools import PgLogicalBackup, restore_archive
from hummingbot.strategy_v2.life_liquidity.storage.postgres.control import PgControlRepository
from hummingbot.strategy_v2.life_liquidity.storage.postgres.ownership import PgProcessLease
from hummingbot.strategy_v2.life_liquidity.storage.postgres.process_handoff import PgProcessHandoffIssuer
from hummingbot.strategy_v2.life_liquidity.storage.postgres.recovery_actor import PgRecoveryActor, PgRecoveryGrantIssuer
from hummingbot.strategy_v2.life_liquidity.storage.postgres.restore import PgRestoreQuarantine
from hummingbot.strategy_v2.life_liquidity.storage.postgres.restore_import import PgRestoreRecoveryImporter
from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
from hummingbot.strategy_v2.life_liquidity.storage.postgres.settlement import PgInventorySettlementRepository
from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
from hummingbot.strategy_v2.life_liquidity.storage.settlement import SettleSpotInventory


@pytest.mark.asyncio
async def test_real_restore_recovers_late_original_fills_then_requires_new_signed_private_generation(database, tmp_path):
    old, cfg, source, lease, original, _, _, record = await setup(database, paired=True)
    c = database[0]
    before = await original.snapshot(cfg.binding)
    await original.close()
    await old.close()
    source_name = await c.fetchval('SELECT current_database()')
    bundle = await PgLogicalBackup(c, tools=tools(source_name), limits=LIMITS).create(tmp_path / 'backup')
    root = database[1].rsplit('/', 1)[0]
    name = 'restore_actor_' + uuid4().hex
    admin = await asyncpg.connect(root + '/life_pg2_test')
    restored = target = actor = observer = None
    created = False
    try:
        await admin.execute(f'CREATE DATABASE "{name}" OWNER life_pg2_migrator')
        created = True
        await admin.execute(f'REVOKE CONNECT ON DATABASE "{name}" FROM PUBLIC')
        owner_dsn = root.replace('postgres@', 'life_pg2_migrator@') + '/' + name
        restored = await asyncpg.connect(owner_dsn)
        await restore_archive(restored, tools=tools(name), bundle=bundle, limits=LIMITS)
        await admin.execute(f'ALTER DATABASE "{name}" ALLOW_CONNECTIONS false')
        cmd = request(bundle.image)
        await PgRestoreQuarantine(restored, limits=LIMITS).quarantine(cmd, manifest=bundle.image)
        target_cfg = replace(cfg, database_lineage=cmd.target_lineage, principal='life_pg2_reporter',
                             dsn=root.replace('postgres@', 'life_pg2_reporter@') + '/' + name)
        db = (restored, root + '/' + name, owner_dsn)
        now = await restored.fetchval('SELECT clock_timestamp()')
        actor_owner = OwnerToken(uuid4(), uuid4(), source.epoch, source.session_id, now, now + timedelta(seconds=20))
        head = await restored.fetchrow('SELECT grant_id,lease_epoch FROM life_financial.process_leases')
        _, _, cap, _, _, certificate, _ = proof(cfg=target_cfg, source=source, target=actor_owner,
                                                grant=head['grant_id'], epoch=head['lease_epoch'], private=record['private'])
        importer = PgRestoreRecoveryImporter(restored, limits=LIMITS, operator_verifier=record['keys'].verifier(),
                                             credential_verifier=record['verifier'])

        async def authorize(preview):
            return await authorization(db, preview, record['keys'], importer.request(preview, cap.registration, certificate))
        result = await importer.import_recovery(cmd, manifest=bundle.image, account_id=cfg.account_id,
                                                registration=cap.registration, certificate=certificate, authorize=authorize)
        assert not result.trading_permission
        await admin.execute(f'GRANT CONNECT ON DATABASE "{name}" TO life_pg2_reporter')
        await admin.execute(f'ALTER DATABASE "{name}" ALLOW_CONNECTIONS true')
        observer = PgSpotRepository(await PgRuntime.open(target_cfg))
        restored_before = await observer.snapshot(cfg.binding)
        assert (restored_before.balance, restored_before.budget) == (before.balance, before.budget)
        assert restored_before.recovery_required
        control = PgControlRepository(restored, observer, verifier=record['keys'].verifier())
        issuer = PgRecoveryGrantIssuer(control, credential_verifier=record['verifier'])
        grant = uuid4()
        delegation = {}

        async def register(registration):
            _, _, _, _, _, cert, _ = proof(cfg=target_cfg, source=source, target=actor_owner,
                                           grant=head['grant_id'], epoch=head['lease_epoch'], private=record['private'])
            cert = sign(replace(cert, target_capability_hash=registration.capability_hash), record['private'])
            cut, facts = await evidence(db, observer, target_cfg)
            facts = replace(facts, accepted_wire_ids=('wire1', 'wire2'), order_scope_complete=False, runner_scope_complete=False)
            payload = issuer.request('restored-recovery-process', grant, registration, cert, source)
            approval = record['keys'].sign('operator', cfg.binding, 'HANDOFF', payload, cut, facts,
                                           issued_at=facts.observed_at - timedelta(seconds=1), expires_at=facts.observed_at + timedelta(seconds=5))
            await issuer.register('restored-recovery-process', grant, registration, source_owner=source,
                                  certificate=cert, approval=approval, facts=facts, cut=cut)
            delegation['certificate'] = cert
        actor = await PgRecoveryActor.open(target_cfg, owner=actor_owner, claim_operation_id='restored-observer',
                                           heartbeat_interval_ms=250, register=register)
        assert actor.ready and actor.mode == 'RECOVERY_ONLY' and actor.source_owner == source
        assert (await actor.lease.lookup('restored-observer')).lease_epoch == 2
        with pytest.raises(StorageError):
            await actor.financial.prepare(order(source, operation_id='premature', intent_id='premature', client_order_id='wire3'),
                                          cut=(await actor.financial.snapshot(cfg.binding)).cut, owner=source)

        # These original trade/fee/ACK facts were outside the backup's cut.
        at = await restored.fetchval('SELECT clock_timestamp()')
        fill = filled(source, at)
        fill = replace(fill, fills=(replace(fill.fills[0], independent_price_usdt=D('1')),))
        await actor.apply_received(fill)
        await actor.apply_received(ack(source, at, operation_id='ack2', prepare_operation_id='operation2', intent_id='intent2',
                                       client_order_id='wire2', side='SELL', exchange_order_id='123456790'))
        second = replace(fill, operation_id='fill2', prepare_operation_id='operation2', intent_id='intent2', client_order_id='wire2',
                         side='SELL', exchange_order_id='123456790', fills=(replace(fill.fills[0], trade_id='trade2'),))
        await actor.apply_received(second)
        first_terminal = await terminal(actor.financial, target_cfg, source, fills=fill.fills, pending_wire_ids=('wire2',),
                                        pending_exchange_order_ids=('123456790',), runner_wire_ids=('wire1', 'wire2'), account_trade_ids=('trade1', 'trade2'))
        await actor.financial.confirm_terminal(first_terminal, cut=(await actor.financial.snapshot(cfg.binding)).cut, owner=source)
        second_terminal = await terminal(actor.financial, target_cfg, source, side='SELL', fills=second.fills,
                                         account_trade_ids=('trade1', 'trade2'), runner_wire_ids=('wire1', 'wire2'))
        second_terminal = replace(second_terminal, operation_id='terminal2', prepare_operation_id='operation2', intent_id='intent2',
                                  client_order_id='wire2', exchange_order_id='123456790')
        await actor.financial.confirm_terminal(second_terminal, cut=(await actor.financial.snapshot(cfg.binding)).cut, owner=source)
        cut, facts = await evidence(db, actor.financial, target_cfg)
        await PgInventorySettlementRepository(actor.financial).settle_inventory(
            SettleSpotInventory('restored-cycle-settlement', 'cycle1', ('intent1', 'intent2')), facts=facts, cut=cut, owner=source)
        recovered = await actor.financial.snapshot(cfg.binding)
        assert not recovered.recovery_required and recovered.balance.reserved_quote_usdt == 0
        assert recovered.budget.campaign_execution_loss_usdt > 0 and recovered.budget.campaign_subsidy_committed_usdt > 0
        assert await restored.fetchval('SELECT count(*) FROM life_financial.fills') == 2
        assert await c.fetchval('SELECT count(*) FROM life_financial.fills') == 0  # Source is untouched.

        # Recovery evidence/cold lookup does not activate trading. An unrelated
        # generation needs new signed logical ownership and private handoff.
        now = await restored.fetchval('SELECT clock_timestamp()')
        new_owner = OwnerToken(uuid4(), uuid4(), source.epoch, source.session_id, now, now + timedelta(seconds=20))
        activate = OwnerTransition('restored-fresh-activate', OwnerMode.ACTIVE, new_owner)
        await control.transition_owner(activate, **await signed(db, observer, target_cfg, record['keys'], activate))
        native_grant = await restored.fetchval('SELECT grant_id FROM life_financial.grant_consumptions WHERE operation_id=$1', activate.operation_id)
        predecessor = await actor.lease.lookup('restored-observer')
        _, _, risk_cap, _, _, cert, _ = proof(cfg=target_cfg, source=actor_owner, target=new_owner,
                                              grant=predecessor.grant_id, epoch=2, private=record['private'])
        cert = sign(replace(cert, source=delegation['certificate'].target,
                            target=replace(cert.target, service_identity='service:third', credential_resource='secret:third:1')), record['private'])
        handoff = PgProcessHandoffIssuer(control, credential_verifier=record['verifier'])
        cut, facts = await evidence(db, observer, target_cfg)
        payload = handoff.request('restored-fresh-handoff', native_grant, risk_cap.registration, cert)
        approval = record['keys'].sign('operator', cfg.binding, 'HANDOFF', payload, cut, facts,
                                       issued_at=facts.observed_at - timedelta(seconds=1), expires_at=facts.observed_at + timedelta(seconds=5))
        await handoff.register_successor('restored-fresh-handoff', native_grant, risk_cap.registration, certificate=cert, approval=approval, facts=facts, cut=cut)
        target = PgSpotRepository(await PgRuntime.open(target_cfg))
        new_lease = PgProcessLease(target._runtime, risk_cap)
        assert (await new_lease.claim('restored-fresh-risk-process')).lease_epoch == 3
        await target.prepare(order(new_owner, operation_id='new-risk', intent_id='new-risk', client_order_id='wire3'),
                             cut=(await target.snapshot(cfg.binding)).cut, owner=new_owner)
        assert await restored.fetchval("SELECT mode FROM life_financial.accounts") == 'ACTIVE'
        assert await restored.fetchval('SELECT risk_epoch FROM life_financial.accounts') == 1
        assert (await importer.lookup(cmd, manifest=bundle.image, account_id=cfg.account_id)).replayed
        assert not await restored.fetchval("SELECT can_write FROM life_financial.access_bindings WHERE principal='life_pg2_runtime'")
        assert json.loads(await restored.fetchval('SELECT body FROM life_storage.restore_recovery_imports'))['receipt']['trading_permission'] is False
    finally:
        for repo in (target, actor, observer):
            if repo is not None:
                await repo.close()
        if restored is not None:
            await restored.close()
        if created:
            await admin.execute(f'DROP DATABASE "{name}" WITH (FORCE)')
        await admin.close()
