"""Signed clean cold handoff; real PostgreSQL, synthetic credential authority."""
import asyncio
from dataclasses import replace
from datetime import timedelta
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_control import evidence, signed
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_financial_commands import order
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_ownership import managed
from test.hummingbot.strategy_v2.life_liquidity.storage.test_generation_exclusion import proof, sign
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from hummingbot.strategy_v2.life_liquidity.storage.contracts import OwnerMode, OwnerToken, StorageError
from hummingbot.strategy_v2.life_liquidity.storage.control_plane import OwnerTransition
from hummingbot.strategy_v2.life_liquidity.storage.generation_exclusion import CredentialExclusionVerifier
from hummingbot.strategy_v2.life_liquidity.storage.postgres.control import PgControlRepository
from hummingbot.strategy_v2.life_liquidity.storage.postgres.ownership import PgProcessLease
from hummingbot.strategy_v2.life_liquidity.storage.postgres.process_handoff import PgProcessHandoffIssuer
from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository


async def successor(database):
    private = Ed25519PrivateKey.generate()
    verifier = CredentialExclusionVerifier('synthetic-provider', private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw))
    old, cfg, owner, cap, lease, _, _, keys = await managed(
        database, lease_ms=4000, handoff_role=True, configuration_extra={'credential_authority': verifier.policy()})
    first = await lease.claim('first-process')
    f = PgSpotRepository(await PgRuntime.open(cfg))
    control = PgControlRepository(database[0], f, verifier=keys.verifier())
    try:
        drain = OwnerTransition('cold-drain', OwnerMode.DRAINING, owner)
        await control.transition_owner(drain, **await signed(database, f, cfg, keys, drain))
        now = await database[0].fetchval('SELECT clock_timestamp()')
        target = OwnerToken(uuid4(), uuid4(), owner.epoch, owner.session_id, now, now + timedelta(seconds=20))
        activate = OwnerTransition('cold-activate', OwnerMode.ACTIVE, target)
        await control.transition_owner(activate, **await signed(database, f, cfg, keys, activate))
        grant = await database[0].fetchval("SELECT grant_id FROM life_financial.grant_consumptions WHERE operation_id='cold-activate'")
        _, _, capability, _, _, cert, _ = proof(cfg=cfg, source=owner, target=target, grant=first.grant_id, epoch=1, private=private)
        issuer = PgProcessHandoffIssuer(control, credential_verifier=verifier)
        return old, cfg, owner, lease, f, keys, capability, cert, private, issuer, grant
    except BaseException:
        await f.close()
        await old.close()
        raise


async def authorization(database, values, *, cert=None, operation='cold-handoff'):
    old, cfg, owner, lease, f, keys, cap, certificate, private, issuer, grant = values
    cert = certificate if cert is None else cert
    cut, facts = await evidence(database, f, cfg)
    request = issuer.request(operation, grant, cap.registration, cert)
    approval = keys.sign('operator', cfg.binding, 'HANDOFF', request, cut, facts,
                         issued_at=facts.observed_at - timedelta(seconds=1), expires_at=facts.observed_at + timedelta(seconds=5))
    return dict(certificate=cert, approval=approval, facts=facts, cut=cut)


@pytest.mark.asyncio
async def test_explicit_exclusion_and_signed_handoff_advance_epoch_once_without_resetting_money(database):
    values = await successor(database)
    old, cfg, owner, lease, f, keys, cap, cert, _, issuer, grant = values
    target = None
    try:
        before = await f.snapshot(cfg.binding)
        await issuer.register_successor('cold-handoff', grant, cap.registration, **await authorization(database, values))
        assert await database[0].fetchval('SELECT lease_epoch FROM life_financial.process_leases') == 1
        target = PgSpotRepository(await PgRuntime.open(cfg))
        new_lease = PgProcessLease(target._runtime, cap)
        assert not new_lease.ready
        result = await new_lease.claim('second-process')
        assert result.lease_epoch == 2 and not result.replayed and not result.trading_permission
        assert new_lease.ready and (await new_lease.claim('second-process')).replayed
        after = await target.snapshot(cfg.binding)
        assert (after.balance, after.budget, after.cut) == (before.balance, before.budget, before.cut)
        with pytest.raises(StorageError):
            await lease.heartbeat('stale-old-process')
        with pytest.raises(StorageError):
            await old.prepare(order(owner), cut=after.cut, owner=owner)
        await target.prepare(order(cap._owner), cut=after.cut, owner=cap._owner)
        assert await database[0].fetchval("SELECT count(*) FROM life_financial.process_lease_commands WHERE kind='CLAIM'") == 2
    finally:
        if target is not None:
            await target.close()
        await f.close()
        await old.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['rest', 'ws', 'caa', 'inflight', 'signature', 'operator', 'stale_cut', 'expired', 'lineage', 'old_facts'])
async def test_missing_exclusion_or_exact_operator_authority_retains_original_head(database, fault):
    values = await successor(database)
    old, cfg, owner, lease, f, keys, cap, cert, private, issuer, grant = values
    try:
        if fault in ('rest', 'ws', 'caa', 'inflight'):
            cert = sign(replace(cert, **{dict(rest='rest_denied', ws='ws_denied', caa='caa_disabled', inflight='inflight_reconciled')[fault]: False}), private)
        elif fault == 'signature':
            cert = replace(cert, signature='0' * 128)
        elif fault == 'expired':
            cert = sign(replace(cert, expires_at=cert.observed_at + timedelta(microseconds=1)), private)
        elif fault == 'lineage':
            cert = sign(replace(cert, database_lineage=uuid4()), private)
        args = await authorization(database, values, cert=cert)
        if fault == 'operator':
            args['approval'] = replace(args['approval'], signature='0' * 128)
        elif fault == 'stale_cut':
            args['cut'] = replace(args['cut'], checkpoint=replace(args['cut'].checkpoint, revision=0))
        elif fault == 'old_facts':
            args['facts'] = replace(args['facts'], observed_at=cert.observed_at - timedelta(microseconds=1))
            args['approval'] = keys.sign('operator', cfg.binding, 'HANDOFF', issuer.request('cold-handoff', grant, cap.registration, cert),
                                         args['cut'], args['facts'], issued_at=cert.observed_at - timedelta(seconds=1),
                                         expires_at=cert.observed_at + timedelta(seconds=5))
        before = await f.snapshot(cfg.binding)
        with pytest.raises(StorageError):
            await issuer.register_successor('cold-handoff', grant, cap.registration, **args)
        assert await database[0].fetchval('SELECT lease_epoch FROM life_financial.process_leases') == 1
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.process_handoffs') == 0
        after = await f.snapshot(cfg.binding)
        assert (after.balance, after.budget, after.cut) == (before.balance, before.budget, before.cut)
    finally:
        await f.close()
        await old.close()


@pytest.mark.asyncio
async def test_handoff_rechecks_exclusion_expiry_at_physical_commit(database, monkeypatch):
    values = await successor(database)
    old, cfg, owner, lease, f, keys, cap, cert, private, issuer, grant = values
    target = None
    try:
        cert = sign(replace(cert, expires_at=cert.observed_at + timedelta(milliseconds=500)), private)
        await issuer.register_successor('cold-handoff', grant, cap.registration, **await authorization(database, values, cert=cert))
        target = PgSpotRepository(await PgRuntime.open(cfg))
        new_lease = PgProcessLease(target._runtime, cap)
        original = target._runtime._commit

        async def delayed(transaction):
            now = await database[0].fetchval('SELECT clock_timestamp()')
            await asyncio.sleep(max(0, (cert.expires_at - now).total_seconds()) + .02)
            await original(transaction)
        monkeypatch.setattr(target._runtime, '_commit', delayed)
        with pytest.raises(StorageError, match='HANDOFF'):
            await new_lease.claim('expired-physical-claim')
        assert not new_lease.ready
        assert await database[0].fetchval('SELECT lease_epoch FROM life_financial.process_leases') == 1
        assert await new_lease.lookup('expired-physical-claim') is None
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.process_handoffs') == 1
    finally:
        if target is not None:
            await target.close()
        await f.close()
        await old.close()


@pytest.mark.asyncio
async def test_unknown_successor_commit_and_cold_readback_never_recreate_readiness(database, monkeypatch):
    from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown
    values = await successor(database)
    old, cfg, owner, lease, f, keys, cap, cert, private, issuer, grant = values
    target = cold = None
    try:
        await issuer.register_successor('cold-handoff', grant, cap.registration, **await authorization(database, values))
        target = PgSpotRepository(await PgRuntime.open(cfg))
        new_lease = PgProcessLease(target._runtime, cap)
        original = target._runtime._commit

        async def lost(transaction):
            await original(transaction)
            raise ConnectionError('synthetic lost handoff reply')
        monkeypatch.setattr(target._runtime, '_commit', lost)
        with pytest.raises(CommitUnknown):
            await new_lease.claim('unknown-successor')
        assert not new_lease.ready
        assert (await new_lease.lookup('unknown-successor')).replayed
        assert not new_lease.ready
        assert await database[0].fetchval('SELECT lease_epoch FROM life_financial.process_leases') == 2
        cold = PgSpotRepository(await PgRuntime.open(cfg))
        cold_lease = PgProcessLease(cold._runtime, cap)
        assert (await cold_lease.claim('unknown-successor')).replayed and not cold_lease.ready
        assert target._runtime.diagnostics[-2].commit_attempted
        with pytest.raises(StorageError):
            await cold.prepare(order(cap._owner), cut=(await cold.snapshot(cfg.binding)).cut, owner=cap._owner)
    finally:
        if cold is not None:
            await cold.close()
        if target is not None:
            await target.close()
        await f.close()
        await old.close()


@pytest.mark.asyncio
async def test_competing_successor_claims_consume_one_epoch_and_one_original_command(database):
    values = await successor(database)
    old, cfg, owner, lease, f, keys, cap, cert, private, issuer, grant = values
    targets = []
    try:
        await issuer.register_successor('cold-handoff', grant, cap.registration, **await authorization(database, values))
        targets = [PgSpotRepository(await PgRuntime.open(cfg)) for _ in range(2)]
        leases = [PgProcessLease(v._runtime, cap) for v in targets]
        results = await asyncio.gather(*(v.claim('candidate-' + str(i)) for i, v in enumerate(leases)), return_exceptions=True)
        assert sum(isinstance(r, StorageError) for r in results) == 1
        assert sum(v.ready for v in leases) == 1
        assert await database[0].fetchval('SELECT lease_epoch FROM life_financial.process_leases') == 2
        assert await database[0].fetchval("SELECT count(*) FROM life_financial.process_lease_commands WHERE kind='CLAIM'") == 2
    finally:
        for target in targets:
            await target.close()
        await f.close()
        await old.close()


@pytest.mark.asyncio
async def test_consumed_handoff_assertion_expiry_does_not_reauthorize_or_expire_ongoing_lease(database):
    values = await successor(database)
    old, cfg, owner, lease, f, keys, cap, cert, private, issuer, grant = values
    target = None
    try:
        cert = sign(replace(cert, expires_at=cert.observed_at + timedelta(milliseconds=700)), private)
        await issuer.register_successor('cold-handoff', grant, cap.registration, **await authorization(database, values, cert=cert))
        target = PgSpotRepository(await PgRuntime.open(cfg))
        new_lease = PgProcessLease(target._runtime, cap)
        receipt = await new_lease.claim('assertion-consumed')
        now = await database[0].fetchval('SELECT clock_timestamp()')
        await asyncio.sleep(max(0, (cert.expires_at - now).total_seconds()) + .02)
        beat = await new_lease.heartbeat('after-assertion-expiry')
        assert beat.lease_epoch == receipt.lease_epoch == 2 and new_lease.ready
        await target.prepare(order(cap._owner), cut=(await target.snapshot(cfg.binding)).cut, owner=cap._owner)
    finally:
        if target is not None:
            await target.close()
        await f.close()
        await old.close()


@pytest.mark.asyncio
async def test_successor_write_rechecks_immutable_handoff_origin(database):
    values = await successor(database)
    old, cfg, owner, lease, f, keys, cap, cert, private, issuer, grant = values
    target = None
    try:
        await issuer.register_successor('cold-handoff', grant, cap.registration, **await authorization(database, values))
        target = PgSpotRepository(await PgRuntime.open(cfg))
        new_lease = PgProcessLease(target._runtime, cap)
        await new_lease.claim('original-successor')
        before = await target.snapshot(cfg.binding)
        # Disposable-owner fault injection. Runtime has no such DML/DDL rights.
        await database[0].execute('ALTER TABLE life_financial.process_handoffs DISABLE TRIGGER immutable_history')
        await database[0].execute("UPDATE life_financial.process_handoffs SET body=jsonb_set(body,'{payload,principal}',to_jsonb('foreign'::text))")
        with pytest.raises(StorageError, match='HANDOFF_ORIGIN'):
            await new_lease.heartbeat('corrupt-origin')
        assert not new_lease.ready
        after = await target.snapshot(cfg.binding)
        assert (after.cut, after.balance, after.budget) == (before.cut, before.balance, before.budget)
    finally:
        if target is not None:
            await target.close()
        await f.close()
        await old.close()


@pytest.mark.asyncio
async def test_spawned_successor_creates_its_own_secret_and_old_process_stays_fenced(database):
    import multiprocessing
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.ownership_process import run
    values = await successor(database)
    old, cfg, owner, lease, f, keys, cap, cert, private, issuer, grant = values
    parent, child = multiprocessing.get_context('spawn').Pipe()
    process = multiprocessing.get_context('spawn').Process(target=run, args=(cfg, cap._owner, child, 'write-accepted'))
    process.start()
    child.close()
    try:
        kind, registration = await asyncio.wait_for(asyncio.to_thread(parent.recv), 15)
        assert kind == 'registration' and registration.capability_hash != cap.registration.capability_hash
        cert = sign(replace(cert, target_capability_hash=registration.capability_hash), private)
        args = await authorization(database, values, cert=cert)
        request = issuer.request('child-handoff', grant, registration, cert)
        args['approval'] = keys.sign('operator', cfg.binding, 'HANDOFF', request, args['cut'], args['facts'],
                                     issued_at=args['facts'].observed_at - timedelta(seconds=1),
                                     expires_at=args['facts'].observed_at + timedelta(seconds=5))
        await issuer.register_successor('child-handoff', grant, registration, **args)
        parent.send('claim')
        kind, receipt = await asyncio.wait_for(asyncio.to_thread(parent.recv), 15)
        assert kind == 'claimed' and receipt.lease_epoch == 2
        assert not receipt.trading_permission
        with pytest.raises(StorageError):
            await lease.heartbeat('old-after-child-claim')
        parent.send('resume')
        kind, code = await asyncio.wait_for(asyncio.to_thread(parent.recv), 15)
        assert kind == 'write-accepted'  # Original child authority can tick its own session.
        await asyncio.to_thread(process.join, 5)
        assert process.exitcode == 0
    finally:
        parent.close()
        await asyncio.to_thread(process.join, 5)
        if process.is_alive():
            process.terminate()
            await asyncio.to_thread(process.join, 5)
        await f.close()
        await old.close()


@pytest.mark.asyncio
async def test_configured_successor_service_claims_then_waits_for_explicit_arm_before_sdk_order(database):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_managed_service import (
        managed_service,
    )

    from hummingbot.core.data_type.common import TradeType
    from hummingbot.data_feed.market_data_provider import MarketDataProvider
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.managed_process import PgManagedProcessConfig
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.service import PgLiquidityService
    private = Ed25519PrivateKey.generate()
    verifier = CredentialExclusionVerifier('synthetic-provider', private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw))
    t, original, state = await managed_service(database, handoff_role=True,
                                               configuration_extra={'credential_authority': verifier.policy()})
    replacement = control_financial = None
    try:
        source_owner = t.owner
        first = await original.process_lease.lookup('managed-claim')
        original.drain_process()
        await original.close()  # Explicit stopped, clean native account; no outstanding orders.
        control_financial = PgSpotRepository(await PgRuntime.open(t.cfg))
        control = PgControlRepository(database[0], control_financial, verifier=state.keys.verifier())
        drain = OwnerTransition('service-cold-drain', OwnerMode.DRAINING, source_owner)
        await control.transition_owner(drain, **await signed(database, control_financial, t.cfg, state.keys, drain))
        now = await database[0].fetchval('SELECT clock_timestamp()')
        owner = OwnerToken(uuid4(), uuid4(), source_owner.epoch, source_owner.session_id, now, now + timedelta(seconds=30))
        activate = OwnerTransition('service-cold-activate', OwnerMode.ACTIVE, owner)
        await control.transition_owner(activate, **await signed(database, control_financial, t.cfg, state.keys, activate))
        grant = await database[0].fetchval("SELECT grant_id FROM life_financial.grant_consumptions WHERE operation_id='service-cold-activate'")
        issuer = PgProcessHandoffIssuer(control, credential_verifier=verifier)

        async def register(registration):
            _, _, _, _, _, cert, _ = proof(cfg=t.cfg, source=source_owner, target=owner, grant=first.grant_id, private=private)
            cert = sign(replace(cert, target_capability_hash=registration.capability_hash), private)
            cut, facts = await evidence(database, control_financial, t.cfg)
            request = issuer.request('service-handoff', grant, registration, cert)
            approval = state.keys.sign('operator', t.cfg.binding, 'HANDOFF', request, cut, facts,
                                       issued_at=facts.observed_at - timedelta(seconds=1), expires_at=facts.observed_at + timedelta(seconds=5))
            await issuer.register_successor('service-handoff', grant, registration, certificate=cert, approval=approval, facts=facts, cut=cut)
        replacement = await PgLiquidityService.open(
            original.config, runtime_config=t.cfg, owner=owner, connectors=original.connectors,
            market_data_provider=MarketDataProvider(original.connectors), sources=tuple(e.source for e in original.reporting.events),
            limits=original.limits, clock=original.clock, independent_fill_value=lambda *args: None,
            independent_cashflow_value=lambda *args: None, native_trading=True,
            managed_process=PgManagedProcessConfig(claim_operation_id='successor-service-claim', heartbeat_interval_ms=250),
            register_process=register)
        assert replacement.process_mode == 'UNARMED' and replacement.process_lease.ready
        assert (await replacement.process_lease.lookup('successor-service-claim')).lease_epoch == 2
        with pytest.raises(StorageError):
            await replacement.quote('successor-unarmed', TradeType.BUY, 0)
        assert not t.service_orders
        await replacement.arm_process()
        await replacement.quote('successor-armed', TradeType.BUY, 0)
        assert len(t.service_orders) == 1
        with pytest.raises(StorageError):
            await original.quote('old-rearmed', TradeType.BUY, 0)
        assert original.process_mode == 'STOPPED'
    finally:
        if replacement is not None:
            await replacement.close()
        await original.close()
        if control_financial is not None:
            await control_financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('wrong_source', [False, True])
async def test_third_generation_requires_exact_predecessor_identity_and_monotone_epoch(database, wrong_source):
    values = await successor(database)
    old, cfg, owner, lease, f, keys, cap, cert, private, issuer, grant = values
    second = third = None
    try:
        await issuer.register_successor('cold-handoff', grant, cap.registration, **await authorization(database, values))
        second = PgSpotRepository(await PgRuntime.open(cfg))
        second_lease = PgProcessLease(second._runtime, cap)
        await second_lease.claim('epoch-two')
        control = issuer.control
        command = OwnerTransition('third-drain', OwnerMode.DRAINING, cap._owner)
        await control.transition_owner(command, **await signed(database, f, cfg, keys, command))
        now = await database[0].fetchval('SELECT clock_timestamp()')
        third_owner = OwnerToken(uuid4(), uuid4(), cap._owner.epoch, cap._owner.session_id, now, now + timedelta(seconds=20))
        command = OwnerTransition('third-activate', OwnerMode.ACTIVE, third_owner)
        await control.transition_owner(command, **await signed(database, f, cfg, keys, command))
        third_grant = await database[0].fetchval("SELECT grant_id FROM life_financial.grant_consumptions WHERE operation_id='third-activate'")
        _, _, third_cap, _, _, assertion, _ = proof(cfg=cfg, source=cap._owner, target=third_owner, grant=grant, epoch=2, private=private)
        source = replace(cert.target, credential_resource='wrong-old-resource') if wrong_source else cert.target
        target = replace(assertion.target, service_identity='service:third', credential_resource='secret:third:1', image_digest='3' * 64)
        assertion = sign(replace(assertion, source=source, target=target), private)
        cut, facts = await evidence(database, f, cfg)
        request = issuer.request('third-handoff', third_grant, third_cap.registration, assertion)
        approval = keys.sign('operator', cfg.binding, 'HANDOFF', request, cut, facts,
                             issued_at=facts.observed_at - timedelta(seconds=1), expires_at=facts.observed_at + timedelta(seconds=5))
        if wrong_source:
            with pytest.raises(StorageError, match='SOURCE_IDENTITY_CHANGED'):
                await issuer.register_successor('third-handoff', third_grant, third_cap.registration,
                                                certificate=assertion, approval=approval, facts=facts, cut=cut)
            assert await database[0].fetchval('SELECT lease_epoch FROM life_financial.process_leases') == 2
        else:
            await issuer.register_successor('third-handoff', third_grant, third_cap.registration,
                                            certificate=assertion, approval=approval, facts=facts, cut=cut)
            third = PgSpotRepository(await PgRuntime.open(cfg))
            third_lease = PgProcessLease(third._runtime, third_cap)
            assert (await third_lease.claim('epoch-three')).lease_epoch == 3
            assert third_lease.ready
            assert not (await third_lease.heartbeat('third-beat')).replayed
            with pytest.raises(StorageError):
                await second_lease.heartbeat('stale-epoch-two')
            after = await third.snapshot(cfg.binding)
            assert (after.cut, after.balance, after.budget) == (cut, (await f.snapshot(cfg.binding)).balance, (await f.snapshot(cfg.binding)).budget)
    finally:
        if third is not None:
            await third.close()
        if second is not None:
            await second.close()
        await f.close()
        await old.close()
