"""Replacement private recovery process over original unresolved native holds."""
from dataclasses import replace
from datetime import timedelta
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_control import evidence, signed
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_financial_commands import order
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_ownership import managed
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_spot_observations import ack, filled
from test.hummingbot.strategy_v2.life_liquidity.storage.test_generation_exclusion import proof
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from hummingbot.strategy_v2.life_liquidity.storage.contracts import OwnerMode, OwnerToken, StorageError
from hummingbot.strategy_v2.life_liquidity.storage.control_plane import OwnerTransition
from hummingbot.strategy_v2.life_liquidity.storage.generation_exclusion import CredentialExclusionVerifier
from hummingbot.strategy_v2.life_liquidity.storage.postgres.control import PgControlRepository
from hummingbot.strategy_v2.life_liquidity.storage.postgres.recovery_actor import PgRecoveryActor, PgRecoveryGrantIssuer
from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository


async def setup(database, *, invalid=None, owner_duration_ms=20000, paired=False):
    private = Ed25519PrivateKey.generate()
    verifier = CredentialExclusionVerifier('synthetic-provider', private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw))
    old, cfg, source, cap, lease, _, _, keys = await managed(
        database, handoff_role=True, lease_ms=4000, owner_duration_ms=owner_duration_ms, configuration_extra={'credential_authority': verifier.policy()})
    receipt = await lease.claim('original-risk-process')
    if paired:
        await database[0].execute('INSERT INTO life_financial.spot_settlement_policies VALUES($1,$2,1000)', cfg.account_id, cfg.binding.policy_hash)
    await database[0].execute("INSERT INTO life_financial.spot_capital_policies VALUES($1,$2,5,10,1,$3,'100',4096)", cfg.account_id, cfg.binding.policy_hash, source.valid_from)
    await database[0].execute('INSERT INTO life_financial.spot_fill_policies VALUES($1,$2,1000,4096)', cfg.account_id, cfg.binding.policy_hash)
    await old.prepare(order(source), cut=(await old.snapshot(cfg.binding)).cut, owner=source)
    await old.arm('operation1', cut=(await old.snapshot(cfg.binding)).cut, owner=source)
    if paired:
        at = await database[0].fetchval('SELECT clock_timestamp()')
        await old.acknowledge(ack(source, at), cut=(await old.snapshot(cfg.binding)).cut, owner=source)
        await old.prepare(order(source, operation_id='operation2', intent_id='intent2', client_order_id='wire2', side='SELL'), cut=(await old.snapshot(cfg.binding)).cut, owner=source)
        await old.arm('operation2', cut=(await old.snapshot(cfg.binding)).cut, owner=source)
    lease.stop()
    f = PgSpotRepository(await PgRuntime.open(cfg))
    control = PgControlRepository(database[0], f, verifier=keys.verifier())
    command = OwnerTransition('recover-old-logical-owner', OwnerMode.RECOVERY_ONLY, source)
    await control.transition_owner(command, **await signed(database, f, cfg, keys, command))
    now = await database[0].fetchval('SELECT clock_timestamp()')
    actor_owner = OwnerToken(uuid4(), uuid4(), source.epoch, source.session_id, now, now + timedelta(seconds=20))
    issuer = PgRecoveryGrantIssuer(control, credential_verifier=verifier)
    grant = uuid4()
    record = dict(keys=keys, private=private, issuer=issuer, verifier=verifier, control=control, grant=grant)

    async def register(registration):
        _, _, _, _, _, cert, _ = proof(cfg=cfg, source=source, target=actor_owner, grant=receipt.grant_id,
                                       epoch=receipt.lease_epoch, private=private)
        from test.hummingbot.strategy_v2.life_liquidity.storage.test_generation_exclusion import sign
        observed = await database[0].fetchval('SELECT clock_timestamp()')
        cert = sign(replace(cert, revocation_requested_at=observed - timedelta(seconds=1), revocation_effective_at=observed - timedelta(milliseconds=500),
                            observed_at=observed, expires_at=observed + timedelta(seconds=5), target_capability_hash=registration.capability_hash,
                            inflight_reconciled=invalid != 'inflight'), private)
        cut, facts = await evidence(database, f, cfg)
        facts = replace(facts, accepted_wire_ids=('wire1',), order_scope_complete=False, runner_scope_complete=False)
        request = issuer.request('recovery-delegation', grant, registration, cert, source)
        approval = keys.sign('operator', cfg.binding, 'HANDOFF', request, cut, facts,
                             issued_at=facts.observed_at - timedelta(seconds=1), expires_at=facts.observed_at + timedelta(seconds=5))
        if invalid == 'signature':
            approval = replace(approval, signature='0' * 128)
        await issuer.register('recovery-delegation', grant, registration, source_owner=source,
                              certificate=cert, approval=approval, facts=facts, cut=cut)
        record.update(registration=registration, certificate=cert, approval=approval, cut=cut, facts=facts)
    return old, cfg, source, lease, f, actor_owner, register, record


@pytest.mark.asyncio
async def test_replacement_recovers_original_unknown_attempt_without_risk_permission_or_hold_reset(database):
    old, cfg, source, lease, f, owner, register, record = await setup(database)
    actor = None
    try:
        before = await f.snapshot(cfg.binding)
        actor = await PgRecoveryActor.open(cfg, owner=owner, claim_operation_id='replacement-recovery',
                                           heartbeat_interval_ms=250, register=register)
        assert actor.mode == 'RECOVERY_ONLY' and actor.ready and actor.source_owner == source
        assert (await actor.lease.lookup('replacement-recovery')).lease_epoch == 2
        after = await actor.financial.snapshot(cfg.binding)
        assert (after.balance, after.budget, after.cut) == (before.balance, before.budget, before.cut)
        assert after.recovery_required
        with pytest.raises(StorageError):
            await lease.heartbeat('old-after-recovery')
        at = await database[0].fetchval('SELECT clock_timestamp()')
        await actor.apply_received(ack(source, at))
        await actor.apply_received(filled(source, at))
        after = await actor.financial.snapshot(cfg.binding)
        assert (await actor.financial.attempt('intent1', cfg.binding)).exchange_order_id == '123456789'
        assert after.balance.reserved_quote_usdt > 0 and after.budget.session_execution_loss_usdt > 0
        assert await database[0].fetchval('SELECT mode FROM life_financial.accounts') == 'RECOVERY_ONLY'
        for logical in (source, owner):
            with pytest.raises(StorageError):
                await actor.financial.prepare(order(logical, operation_id='forbidden', intent_id='forbidden', client_order_id='wire2'),
                                              cut=(await actor.financial.snapshot(cfg.binding)).cut, owner=logical)
        assert not await database[0].fetchval("SELECT EXISTS(SELECT 1 FROM life_financial.send_attempts WHERE intent_id='forbidden')")
    finally:
        if actor is not None:
            await actor.close()
        await f.close()
        await old.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('invalid', ['inflight', 'signature'])
async def test_replacement_denies_unqualified_exclusion_or_operator_and_retains_old_holds(database, invalid):
    old, cfg, source, lease, f, owner, register, record = await setup(database, invalid=invalid)
    try:
        before = await f.snapshot(cfg.binding)
        expected = 'CREDENTIAL_EXCLUSION_UNQUALIFIED' if invalid == 'inflight' else 'OPERATOR_APPROVAL_SIGNATURE_INVALID'
        with pytest.raises(StorageError, match=expected):
            await PgRecoveryActor.open(cfg, owner=owner, claim_operation_id='bad-recovery', heartbeat_interval_ms=250, register=register)
        assert await database[0].fetchval('SELECT lease_epoch FROM life_financial.process_leases') == 1
        assert not await database[0].fetchval('SELECT EXISTS(SELECT 1 FROM life_financial.process_recovery_grants)')
        after = await f.snapshot(cfg.binding)
        assert (after.balance, after.budget, after.cut) == (before.balance, before.budget, before.cut)
    finally:
        await f.close()
        await old.close()


@pytest.mark.asyncio
async def test_manifest_exports_only_committed_ack_identity_and_never_releases_unknown_holds(database, tmp_path):
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.cancellation_manifest import (
        PgCancellationManifestRepository,
    )
    old, cfg, source, lease, f, owner, register, record = await setup(database)
    actor = None
    try:
        exporter = PgCancellationManifestRepository(f, max_rows=8, max_bytes=65536)
        before = await f.snapshot(cfg.binding)
        assert not (await exporter.capture(source_owner=source)).identities
        actor = await PgRecoveryActor.open(cfg, owner=owner, claim_operation_id='manifest-recovery', heartbeat_interval_ms=250, register=register)
        at = await database[0].fetchval('SELECT clock_timestamp()')
        await actor.apply_received(ack(source, at))
        manifest = await exporter.capture(source_owner=source)
        assert len(manifest.identities) == 1
        identity = manifest.identities[0]
        assert (identity.wire_id, identity.exchange_order_id, identity.generation) == ('wire1', '123456789', source.generation)
        manifest.save(tmp_path / 'cancel.json')
        (tmp_path / 'cancel.json').unlink()
        after = await f.snapshot(cfg.binding)
        assert after.balance == before.balance and after.budget == before.budget
        assert (await actor.financial.attempt('intent1', cfg.binding)).state.value == 'ACKED'
    finally:
        if actor is not None:
            await actor.close()
        await f.close()
        await old.close()


@pytest.mark.asyncio
async def test_recovery_can_outlive_old_owner_deadline_without_extending_original_session(database):
    import asyncio
    old, cfg, source, lease, f, owner, register, record = await setup(database, owner_duration_ms=2000)
    actor = None
    try:
        now = await database[0].fetchval('SELECT clock_timestamp()')
        await asyncio.sleep(max(0, (source.expires_at - now).total_seconds()) + .02)
        actor = await PgRecoveryActor.open(cfg, owner=owner, claim_operation_id='late-recovery', heartbeat_interval_ms=250, register=register)
        assert actor.ready
        at = await database[0].fetchval('SELECT clock_timestamp()')
        await actor.apply_received(ack(source, at))
        assert await database[0].fetchval('SELECT owner_until FROM life_financial.accounts') == source.expires_at
        assert (await actor.financial.attempt('intent1', cfg.binding)).state.value == 'ACKED'
    finally:
        if actor is not None:
            await actor.close()
        await f.close()
        await old.close()


@pytest.mark.asyncio
async def test_recovery_unknown_claim_commit_never_rearms_or_releases_original_money(database, monkeypatch):
    from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown
    old, cfg, source, lease, f, owner, register, record = await setup(database)
    original = PgRuntime._commit
    try:
        before = await f.snapshot(cfg.binding)

        async def lost(self, tx):
            await original(self, tx)
            raise ConnectionError('synthetic lost recovery claim')
        monkeypatch.setattr(PgRuntime, '_commit', lost)
        with pytest.raises(CommitUnknown):
            await PgRecoveryActor.open(cfg, owner=owner, claim_operation_id='unknown-recovery', heartbeat_interval_ms=250, register=register)
        assert await database[0].fetchval('SELECT lease_epoch FROM life_financial.process_leases') == 2
        assert await database[0].fetchval("SELECT count(*) FROM life_financial.process_lease_commands WHERE kind='CLAIM'") == 2
        after = await f.snapshot(cfg.binding)
        assert (after.balance, after.budget, after.cut) == (before.balance, before.budget, before.cut)
    finally:
        await f.close()
        await old.close()


@pytest.mark.asyncio
async def test_reconciled_recovery_needs_new_signed_generation_before_trading(database):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_lifecycle import terminal
    from test.hummingbot.strategy_v2.life_liquidity.storage.test_generation_exclusion import sign

    from hummingbot.strategy_v2.life_liquidity.storage.postgres.ownership import PgProcessLease
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.process_handoff import PgProcessHandoffIssuer
    old, cfg, source, lease, f, owner, register, record = await setup(database, paired=True)
    actor = target = None
    try:
        actor = await PgRecoveryActor.open(cfg, owner=owner, claim_operation_id='settling-recovery', heartbeat_interval_ms=250, register=register)
        at = await database[0].fetchval('SELECT clock_timestamp()')
        fill = filled(source, at)
        from decimal import Decimal
        fill = replace(fill, fills=(replace(fill.fills[0], independent_price_usdt=Decimal('1')),))
        await actor.apply_received(fill)
        from hummingbot.strategy_v2.life_liquidity.storage.postgres.settlement import PgInventorySettlementRepository
        from hummingbot.strategy_v2.life_liquidity.storage.settlement import SettleSpotInventory
        second_ack = ack(source, at, operation_id='ack2', prepare_operation_id='operation2', intent_id='intent2', client_order_id='wire2', side='SELL', exchange_order_id='123456790')
        await actor.apply_received(second_ack)
        second_fill = replace(fill, operation_id='fill2', prepare_operation_id='operation2', intent_id='intent2', client_order_id='wire2', side='SELL', exchange_order_id='123456790', fills=(replace(fill.fills[0], trade_id='trade2'),))
        await actor.apply_received(second_fill)
        command = await terminal(actor.financial, cfg, source, fills=fill.fills, pending_wire_ids=('wire2',), pending_exchange_order_ids=('123456790',), runner_wire_ids=('wire1', 'wire2'), account_trade_ids=('trade1', 'trade2'))
        await actor.financial.confirm_terminal(command, cut=(await actor.financial.snapshot(cfg.binding)).cut, owner=source)
        command = await terminal(actor.financial, cfg, source, side='SELL', fills=second_fill.fills, account_trade_ids=('trade1', 'trade2'), runner_wire_ids=('wire1', 'wire2'))
        command = replace(command, operation_id='terminal2', prepare_operation_id='operation2', intent_id='intent2', client_order_id='wire2', exchange_order_id='123456790')
        await actor.financial.confirm_terminal(command, cut=(await actor.financial.snapshot(cfg.binding)).cut, owner=source)
        cut, facts = await evidence(database, actor.financial, cfg)
        await PgInventorySettlementRepository(actor.financial).settle_inventory(SettleSpotInventory('settled-before-handoff', 'cycle1', ('intent1', 'intent2')), facts=facts, cut=cut, owner=source)
        assert not (await actor.financial.snapshot(cfg.binding)).recovery_required
        control = record['control']
        command = OwnerTransition('after-recovery-drain', OwnerMode.DRAINING, source)
        await control.transition_owner(command, **await signed(database, f, cfg, record['keys'], command))
        now = await database[0].fetchval('SELECT clock_timestamp()')
        new_owner = OwnerToken(uuid4(), uuid4(), source.epoch, source.session_id, now, now + timedelta(seconds=20))
        command = OwnerTransition('after-recovery-activate', OwnerMode.ACTIVE, new_owner)
        arguments = await signed(database, f, cfg, record['keys'], command)
        assert not await database[0].fetchval('SELECT halted FROM life_financial.accounts'), 'recovery retains an existing HALT'
        import json
        verified = await database[0].fetchval('SELECT life_financial.control_facts_qualified($1,$2::jsonb)', cfg.account_id, json.dumps(arguments['facts'].json()))
        state = json.loads(await database[0].fetchval('SELECT life_financial.spot_state($1)', cfg.account_id))
        summary = dict(unverified=state['unverified'], financial_recovery=state['financial_recovery_required'], budgets=[dict(v) for v in await database[0].fetch('SELECT family,scope,committed FROM life_financial.budget_buckets WHERE committed<>0')], capital=await database[0].fetchval("SELECT count(*) FROM life_financial.capital_claims WHERE state='HELD'"), balances=state['balances'])
        assert verified, summary
        await control.transition_owner(command, **arguments)
        grant = await database[0].fetchval("SELECT grant_id FROM life_financial.grant_consumptions WHERE operation_id='after-recovery-activate'")
        predecessor = await actor.lease.lookup('settling-recovery')
        _, _, cap, _, _, certificate, _ = proof(cfg=cfg, source=owner, target=new_owner, grant=predecessor.grant_id, epoch=2, private=record['private'])
        certificate = sign(replace(certificate, source=record['certificate'].target,
                                   target=replace(certificate.target, service_identity='service:third', credential_resource='secret:third:1')), record['private'])
        issuer = PgProcessHandoffIssuer(control, credential_verifier=record['verifier'])
        cut, facts = await evidence(database, f, cfg)
        request = issuer.request('after-recovery-handoff', grant, cap.registration, certificate)
        approval = record['keys'].sign('operator', cfg.binding, 'HANDOFF', request, cut, facts,
                                       issued_at=facts.observed_at - timedelta(seconds=1), expires_at=facts.observed_at + timedelta(seconds=5))
        await issuer.register_successor('after-recovery-handoff', grant, cap.registration, certificate=certificate, approval=approval, facts=facts, cut=cut)
        target = PgSpotRepository(await PgRuntime.open(cfg))
        new_lease = PgProcessLease(target._runtime, cap)
        assert (await new_lease.claim('after-recovery-trading-process')).lease_epoch == 3
        after = await target.snapshot(cfg.binding)
        assert after.balance.reserved_quote_usdt == 0 and after.budget.session_execution_loss_usdt > 0
        assert after.budget.campaign_subsidy_committed_usdt > 0  # Original incurred subsidy is never reset.
        await target.prepare(order(new_owner, operation_id='new-risk', intent_id='new-risk', client_order_id='wire3'), cut=after.cut, owner=new_owner)
    finally:
        if target is not None:
            await target.close()
        if actor is not None:
            await actor.close()
        await f.close()
        await old.close()


@pytest.mark.asyncio
async def test_spawned_recovery_owns_private_capability_and_process_death_cannot_rearm(database):
    import asyncio
    import multiprocessing
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.ownership_process import run_recovery
    old, cfg, source, lease, f, owner, register, record = await setup(database)
    parent, child = multiprocessing.get_context('spawn').Pipe()
    process = multiprocessing.get_context('spawn').Process(target=run_recovery, args=(cfg, owner, child))

    async def receive():
        assert await asyncio.to_thread(parent.poll, 10), 'bounded recovery worker wait'
        return parent.recv()
    try:
        before = await f.snapshot(cfg.binding)
        process.start()
        child.close()
        kind, public = await receive()
        assert kind == 'registration'
        await register(public)
        parent.send('registered')
        kind, epoch, logical, mode = await receive()
        assert (kind, epoch, logical, mode) == ('ready', 2, source, 'RECOVERY_ONLY')
        with pytest.raises(StorageError):
            await lease.heartbeat('superseded-native-process')
        parent.send(ack(source, await database[0].fetchval('SELECT clock_timestamp()')))
        assert await receive() == ('applied', 'ACKED')
        process.terminate()
        await asyncio.to_thread(process.join, 5)
        assert not process.is_alive()
        after = await f.snapshot(cfg.binding)
        assert (after.balance, after.budget) == (before.balance, before.budget)
        # Public registration and durable grant can never recreate the dead
        # process's private key or admit a replacement automatically.
        with pytest.raises(StorageError):
            await PgRecoveryActor.open(cfg, owner=owner, claim_operation_id='dead-recovery-adoption', heartbeat_interval_ms=250, register=register)
        assert await database[0].fetchval('SELECT lease_epoch FROM life_financial.process_leases') == 2
        assert await database[0].fetchval("SELECT count(*) FROM life_financial.process_lease_commands WHERE kind='CLAIM'") == 2
    finally:
        if process.is_alive():
            process.terminate()
            await asyncio.to_thread(process.join, 5)
        parent.close()
        child.close()
        await f.close()
        await old.close()


@pytest.mark.asyncio
async def test_native_swap_manifest_requires_committed_ack_and_preserves_shared_claims(database):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_swap import setup as swap_setup

    from hummingbot.strategy_v2.life_liquidity.storage.postgres.cancellation_manifest import (
        PgCancellationManifestRepository,
    )
    values, swap, command = await swap_setup(database)
    financial, cfg, owner = values[:3]
    try:
        await swap.prepare(command, cut=await swap.cut(cfg.binding), owner=owner)
        await swap.arm(command.operation_id, cut=await swap.cut(cfg.binding), owner=owner)
        exporter = PgCancellationManifestRepository(financial, max_rows=8, max_bytes=65536)
        assert not (await exporter.capture(source_owner=owner)).identities
        await swap.acknowledge(command.operation_id, exchange_order_id='987654321', owner=owner)
        before = await financial.snapshot(cfg.binding)
        claims = await values[5].claims(cfg.binding)
        manifest = await exporter.capture(source_owner=owner)
        assert len(manifest.identities) == 1
        row = manifest.identities[0]
        assert (row.product, row.instrument, row.wire_id, row.exchange_order_id) == ('SWAP', 'LIFE-USDT-SWAP', 'SwapWire1', '987654321')
        assert not (await exporter.capture(source_owner=replace(owner, generation=uuid4()))).identities
        assert await values[5].claims(cfg.binding) == claims
        after = await financial.snapshot(cfg.binding)
        assert (after.balance, after.budget, after.cut) == (before.balance, before.budget, before.cut)
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['database_clock_regression', 'foreign_lineage', 'readonly_failover', 'backend_loss'])
async def test_recovery_heartbeat_faults_never_rearm_or_reset_original_holds(database, fault, monkeypatch):
    import asyncio

    import asyncpg

    from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageUnavailable
    old, cfg, source, lease, f, owner, register, record = await setup(database)
    actor = None
    admin = None
    try:
        actor = await PgRecoveryActor.open(cfg, owner=owner, claim_operation_id='fault-recovery', heartbeat_interval_ms=250, register=register)
        before = await f.snapshot(cfg.binding)
        # Park the automatic scheduler before its next native command, while
        # retaining original readiness. Exercise the actual native command once.
        entered = asyncio.Event()
        release = asyncio.Event()
        original_heartbeat = actor.lease.heartbeat

        async def parked(operation):
            entered.set()
            await release.wait()
            return await original_heartbeat(operation)
        monkeypatch.setattr(actor.lease, 'heartbeat', parked)
        await asyncio.wait_for(entered.wait(), 1)
        assert actor.ready
        if fault == 'database_clock_regression':
            await database[0].execute('ALTER TABLE life_financial.process_leases DISABLE TRIGGER USER')
            await database[0].execute("UPDATE life_financial.process_leases SET observed_at=clock_timestamp()+interval '1 hour'")
            await database[0].execute('ALTER TABLE life_financial.process_leases ENABLE TRIGGER USER')
        elif fault == 'foreign_lineage':
            # Restore/drift injection is privileged and cannot be performed by
            # a worker. Bypass only the FK trigger inside this synthetic fault.
            admin = await asyncpg.connect(database[1])
            async with admin.transaction():
                await admin.execute("SET LOCAL session_replication_role='replica'")
                await admin.execute('UPDATE life_storage.storage_metadata SET lineage=$1', uuid4())
        elif fault == 'readonly_failover':
            admin = await asyncpg.connect(database[1])
            await admin.execute('ALTER ROLE life_pg2_runtime SET default_transaction_read_only=on')
            for pool in actor.runtime._pools.values():
                await pool.expire_connections()
        else:
            async def lost(tx):
                raise ConnectionError('synthetic failover during heartbeat')
            monkeypatch.setattr(actor.runtime, '_commit', lost)
        with pytest.raises((StorageError, StorageUnavailable)):
            await original_heartbeat('after-native-fault')
        assert not actor.ready
        assert await database[0].fetchval('SELECT lease_epoch FROM life_financial.process_leases') == 2
        assert not await database[0].fetchval("SELECT EXISTS(SELECT 1 FROM life_financial.process_lease_commands WHERE operation_id='after-native-fault')")
        if fault == 'foreign_lineage':
            async with admin.transaction():
                await admin.execute("SET LOCAL session_replication_role='replica'")
                await admin.execute('UPDATE life_storage.storage_metadata SET lineage=$1', cfg.database_lineage)
        after = await f.snapshot(cfg.binding)
        assert (after.balance, after.budget, after.cut) == (before.balance, before.budget, before.cut)
    finally:
        if admin is not None:
            if fault == 'readonly_failover':
                await admin.execute('ALTER ROLE life_pg2_runtime RESET default_transaction_read_only')
            await admin.close()
        if actor is not None:
            await actor.close()
        await f.close()
        await old.close()
