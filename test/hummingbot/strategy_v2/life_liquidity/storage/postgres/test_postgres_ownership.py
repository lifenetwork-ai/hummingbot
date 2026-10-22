"""First managed process grant/lease; real DB and original signed control.

Synthetic offline policies only. This does not model external credential
exclusion or permit a successor to take over an expired lease.
"""
import asyncio
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_control import signed
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_financial_commands import order, spot
from uuid import uuid4

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import OwnerMode, OwnerToken, StorageError
from hummingbot.strategy_v2.life_liquidity.storage.control_plane import OperatorKeys, OwnerTransition
from hummingbot.strategy_v2.life_liquidity.storage.postgres.ownership import (
    PgLeaseGrantIssuer,
    PgProcessLease,
    ProcessCapability,
)
from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository


async def managed(database, *, lease_ms=2000, register=True, configuration_extra=None, handoff_role=False, owner_duration_ms=20000):
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.control import PgControlRepository
    roles = ('ACTIVATE', 'DRAIN', 'REVOKE', 'RECOVER') + (('HANDOFF',) if handoff_role else ())
    keys = OperatorKeys({'operator': b'x' * 32}, {'operator': frozenset(roles)})
    policy = dict(lease_duration_ms=lease_ms, max_registrations=8)
    financial, cfg, old = await spot(database, configuration_body=lambda _: {
        'operator_verifier': keys.verifier().policy(), 'process_lease': policy, **(configuration_extra or {})})
    c = database[0]
    await c.execute('INSERT INTO life_financial.access_bindings VALUES(current_user,$1,true)', cfg.account_id)
    await c.execute('INSERT INTO life_financial.control_policies VALUES($1,$2,1000,32,1048576,60000)', cfg.account_id, cfg.binding.policy_hash)
    control = PgControlRepository(c, financial, verifier=keys.verifier())
    drain = OwnerTransition('lease-drain', OwnerMode.DRAINING, old)
    await control.transition_owner(drain, **await signed(database, financial, cfg, keys, drain))
    now = await c.fetchval('SELECT clock_timestamp()')
    owner = OwnerToken(uuid4(), uuid4(), old.epoch, old.session_id, now, now + timedelta(milliseconds=owner_duration_ms))
    activate = OwnerTransition('lease-activate', OwnerMode.ACTIVE, owner)
    await control.transition_owner(activate, **await signed(database, financial, cfg, keys, activate))
    await c.execute('INSERT INTO life_financial.process_lease_policies VALUES($1,$2,$3,8)', cfg.account_id, cfg.binding.policy_hash, lease_ms)
    grant = await c.fetchval('SELECT grant_id FROM life_financial.grant_consumptions WHERE account_id=$1 AND operation_id=$2', cfg.account_id, activate.operation_id)
    capability = ProcessCapability.create(cfg, owner)
    issuer = PgLeaseGrantIssuer(control)
    if register:
        await issuer.register_first_generation(grant, capability.registration)
    # Signed control revokes its old runtime; only a new original process
    # assembly can claim the new grant, never a control result/readback.
    await financial.close()
    financial = PgSpotRepository(await PgRuntime.open(cfg))
    lease = PgProcessLease(financial._runtime, capability)
    return financial, cfg, owner, capability, lease, issuer, control, keys


@pytest.mark.asyncio
async def test_original_signed_grant_and_process_secret_claim_once_without_changing_money(database):
    f, cfg, owner, cap, lease, *_ = await managed(database)
    try:
        before = await f.snapshot(cfg.binding)
        with pytest.raises(StorageError):
            await f.prepare(order(owner), cut=before.cut, owner=owner)
        receipt = await lease.claim('claim-first')
        assert receipt.lease_epoch == 1 and not receipt.replayed and not receipt.trading_permission
        assert lease.ready and receipt.expires_at > receipt.observed_at
        assert (await lease.claim('claim-first')).replayed
        after = await f.snapshot(cfg.binding)
        assert after.balance == before.balance and after.budget == before.budget and after.cut == before.cut
        assert await database[0].fetchval("SELECT count(*) FROM life_financial.process_lease_commands WHERE kind='CLAIM'") == 1
        await f.prepare(order(owner), cut=after.cut, owner=owner)
        assert (await f.balance(cfg.binding)).reserved_quote_usdt == D('2.2')
        assert cap._secret not in repr(cap) and cap._secret not in repr(receipt)
    finally:
        await f.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['missing', 'wrong_secret', 'foreign_process', 'replayed_grant'])
async def test_public_owner_token_and_cold_readback_cannot_recreate_process_authority(database, fault):
    f, cfg, owner, cap, lease, *_ = await managed(database)
    cold = None
    try:
        assert not (await lease.claim('claim-first')).replayed
        cold = PgSpotRepository(await PgRuntime.open(cfg))
        if fault != 'missing':
            other = cap if fault == 'replayed_grant' else ProcessCapability.create(cfg, owner if fault == 'wrong_secret' else replace(owner, process_id=uuid4()))
            other_lease = PgProcessLease(cold._runtime, other)
            if fault == 'replayed_grant':
                assert (await other_lease.claim('claim-first')).replayed
                assert not other_lease.ready
            else:
                with pytest.raises(StorageError):
                    await other_lease.claim('foreign-claim')
        with pytest.raises(StorageError):
            await cold.prepare(order(owner), cut=(await cold.snapshot(cfg.binding)).cut, owner=owner)
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.spot_preparations') == 0
        assert (await f.balance(cfg.binding)).reserved_quote_usdt == 0
    finally:
        if cold is not None:
            await cold.close()
        await f.close()


@pytest.mark.asyncio
async def test_expired_lease_cannot_renew_or_claim_a_successor_and_preserves_existing_holds(database):
    f, cfg, owner, cap, lease, *_ = await managed(database, lease_ms=1000)
    try:
        receipt = await lease.claim('claim-first')
        await f.prepare(order(owner), cut=(await f.snapshot(cfg.binding)).cut, owner=owner)
        before = await f.snapshot(cfg.binding)
        now = await database[0].fetchval('SELECT clock_timestamp()')
        await asyncio.sleep(max(0, (receipt.expires_at - now).total_seconds()) + .02)
        with pytest.raises(StorageError, match='LEASE'):
            await lease.heartbeat('late-heartbeat')
        assert not lease.ready
        with pytest.raises(StorageError):
            await lease.claim('new-claim')
        after = await f.snapshot(cfg.binding)
        assert after.balance == before.balance and after.budget == before.budget
        assert (await f.attempt('intent1', cfg.binding)).state.value == 'PREPARED'
        assert await database[0].fetchval('SELECT lease_epoch FROM life_financial.process_leases') == 1
    finally:
        await f.close()


@pytest.mark.asyncio
async def test_lease_is_checked_again_at_physical_financial_commit(database, monkeypatch):
    f, cfg, owner, cap, lease, *_ = await managed(database, lease_ms=1000)
    original = f._runtime._commit
    reached = []
    try:
        receipt = await lease.claim('claim-first')
        before = await f.snapshot(cfg.binding)

        async def expire(transaction):
            reached.append(True)
            now = await database[0].fetchval('SELECT clock_timestamp()')
            await asyncio.sleep(max(0, (receipt.expires_at - now).total_seconds()) + .02)
            await original(transaction)
        monkeypatch.setattr(f._runtime, '_commit', expire)
        with pytest.raises(StorageError, match='LEASE'):
            await f.prepare(order(owner), cut=before.cut, owner=owner)
        assert reached == [True]
        after = await f.snapshot(cfg.binding)
        assert after.balance == before.balance and after.budget == before.budget
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.spot_preparations') == 0
    finally:
        await f.close()


@pytest.mark.asyncio
async def test_old_process_cannot_adopt_public_successor_owner_for_late_financial_writes(database):
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.control import PgControlRepository
    from hummingbot.strategy_v2.life_liquidity.storage.session import SessionTick
    f, cfg, owner, cap, lease, issuer, previous_control, keys = await managed(database, lease_ms=10000)
    try:
        await lease.claim('claim-first')
        control = PgControlRepository(database[0], f, verifier=keys.verifier())
        drain = OwnerTransition('handoff-drain', OwnerMode.DRAINING, owner)
        await control.transition_owner(drain, **await signed(database, f, cfg, keys, drain))
        now = await database[0].fetchval('SELECT clock_timestamp()')
        replacement = OwnerToken(uuid4(), uuid4(), owner.epoch, owner.session_id, now, now + timedelta(seconds=10))
        activate = OwnerTransition('handoff-activate', OwnerMode.ACTIVE, replacement)
        await control.transition_owner(activate, **await signed(database, f, cfg, keys, activate))
        before = await f.snapshot(cfg.binding)
        tick = SessionTick('old-adopts-new-owner', str(replacement.session_id), replacement.epoch, False, False, False, 1000)
        with pytest.raises(StorageError, match='PROCESS_LEASE'):
            await f.tick_session(tick, cut=before.cut, owner=replacement)
        after = await f.snapshot(cfg.binding)
        assert after.cut == before.cut and after.balance == before.balance
        assert not await f.lookup_session_tick(tick.operation_id, cfg.binding)
    finally:
        await f.close()


@pytest.mark.asyncio
async def test_heartbeat_is_bounded_by_original_grant_and_replay_does_not_extend_a_lease(database):
    f, cfg, owner, cap, lease, *_ = await managed(database)
    try:
        first = await lease.claim('claim-first')
        beat = await lease.heartbeat('beat-one')
        assert beat.lease_epoch == first.lease_epoch == 1
        assert first.observed_at < beat.observed_at < beat.expires_at <= owner.expires_at
        assert not beat.replayed and lease.ready
        current = await database[0].fetchrow('SELECT * FROM life_financial.process_leases')
        assert (await lease.heartbeat('beat-one')).replayed
        assert await database[0].fetchrow('SELECT * FROM life_financial.process_leases') == current
        # Pool cleanup removes the private GUC before the next borrower.
        async with f._runtime._pools[True].acquire() as c:
            assert await c.fetchval("SELECT current_setting('life.process_capability',true)") in (None, '')
        lease.stop()
        assert not lease.ready
        with pytest.raises(StorageError, match='LOCAL_UNARMED'):
            await lease.heartbeat('after-stop')
        assert (await lease.lookup('beat-one')).replayed and not lease.ready
        assert await database[0].fetchrow('SELECT * FROM life_financial.process_leases') == current
    finally:
        await f.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['CLAIM', 'HEARTBEAT'])
async def test_lost_lease_commit_retains_one_original_command_and_readback_never_rearms(database, monkeypatch, kind):
    from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown
    f, cfg, owner, cap, lease, *_ = await managed(database)
    original = f._runtime._commit
    try:
        if kind == 'HEARTBEAT':
            await lease.claim('claim-first')
        before = await f.snapshot(cfg.binding)
        reached = []

        async def lost(transaction):
            await original(transaction)
            reached.append(True)
            raise ConnectionError('synthetic lost confirmed lease reply')
        monkeypatch.setattr(f._runtime, '_commit', lost)
        with pytest.raises(CommitUnknown):
            await (lease.claim('unknown-lease') if kind == 'CLAIM' else lease.heartbeat('unknown-lease'))
        assert reached == [True] and not lease.ready and not f._runtime.healthy
        diagnostic = next(d for d in reversed(f._runtime.diagnostics) if d.operation == 'process_lease_' + kind.lower())
        assert diagnostic.commit_attempted and not diagnostic.commit_confirmed
        assert diagnostic.outcome == 'commit_unknown'
        receipt = await lease.lookup('unknown-lease')
        assert receipt.replayed and not receipt.trading_permission and not lease.ready
        assert await database[0].fetchval("SELECT count(*) FROM life_financial.process_lease_commands WHERE operation_id='unknown-lease'") == 1
        after = await f.snapshot(cfg.binding)
        assert after.cut == before.cut and after.balance == before.balance
        with pytest.raises(StorageError):
            await lease.heartbeat('cannot-renew-unknown')
    finally:
        await f.close()


@pytest.mark.asyncio
async def test_concurrent_same_generation_claim_has_one_winner_and_never_creates_another_epoch(database):
    f, cfg, owner, cap, lease, *_ = await managed(database)
    cold = PgSpotRepository(await PgRuntime.open(cfg))
    try:
        other = PgProcessLease(cold._runtime, cap)
        results = await asyncio.gather(lease.claim('first-racer'), other.claim('second-racer'), return_exceptions=True)
        assert sum(isinstance(r, StorageError) for r in results) == 1
        assert sum(not isinstance(r, BaseException) for r in results) == 1
        assert sum((lease.ready, other.ready)) == 1
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.process_lease_commands') == 1
        assert await database[0].fetchval('SELECT lease_epoch FROM life_financial.process_leases') == 1
        assert (await f.balance(cfg.binding)).reserved_quote_usdt == 0
    finally:
        await cold.close()
        await f.close()


@pytest.mark.asyncio
async def test_process_capability_is_not_a_serializable_configuration_or_a_public_hash(database):
    import os
    import pickle
    f, cfg, owner, cap, lease, *_ = await managed(database)
    try:
        with pytest.raises(StorageError, match='SERIALIZATION_DENIED'):
            pickle.dumps(cap)
        assert cap._secret not in repr(cap.registration)
        # A fork/revision must create its own identity; a copied object cannot
        # accidentally borrow connections/authority from the parent process.
        with pytest.raises(StorageError, match='CONTEXT_CHANGED'):
            replace(cap, _pid=os.getpid() + 1)._validate(cfg)
        grant = (await lease.claim('claim-first')).grant_id
        async with f._runtime._pools[True].acquire() as c:
            await c.execute("SELECT set_config('life.process_capability',$1,false)", cap.registration.capability_hash)
            import asyncpg
            with pytest.raises(asyncpg.RaiseError, match='PROCESS_LEASE'):
                await c.fetchval('SELECT life_financial.process_lease_command($1,$2,$3,$4)', cfg.account_id, 'hash-is-not-secret', 'HEARTBEAT',
                                 grant)
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.process_lease_commands') == 1
    finally:
        await f.close()


@pytest.mark.asyncio
async def test_real_process_expiry_and_replacement_never_adopt_shared_public_owner_or_auto_take_over(database):
    import multiprocessing
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.ownership_process import run

    from hummingbot.strategy_v2.life_liquidity.storage.postgres.control import PgControlRepository
    f, cfg, owner, cap, lease, issuer, previous_control, keys = await managed(database, lease_ms=1000, register=False)
    context = multiprocessing.get_context('spawn')
    processes, pipes = [], []

    async def message(pipe):
        return await asyncio.wait_for(asyncio.to_thread(pipe.recv), 15)

    def start():
        parent, child = context.Pipe()
        process = context.Process(target=run, args=(cfg, owner, child))
        process.start()
        child.close()
        processes.append(process)
        pipes.append(parent)
        return parent
    try:
        before = await f.snapshot(cfg.binding)
        original = start()
        kind, registration = await message(original)
        assert kind == 'registration' and registration.owner == owner
        control = PgControlRepository(database[0], f, verifier=keys.verifier())
        issuer = PgLeaseGrantIssuer(control)
        grant = await database[0].fetchval('SELECT grant_id FROM life_financial.grant_consumptions WHERE account_id=$1', cfg.account_id)
        await issuer.register_first_generation(grant, registration)
        original.send('claim')
        kind, receipt = await message(original)
        assert kind == 'claimed' and receipt.lease_epoch == 1
        now = await database[0].fetchval('SELECT clock_timestamp()')
        await asyncio.sleep(max(0, (receipt.expires_at - now).total_seconds()) + .02)
        replacement = start()
        kind, new_registration = await message(replacement)
        assert kind == 'registration' and new_registration.capability_hash != registration.capability_hash
        with pytest.raises(StorageError, match='HANDOFF_REQUIRED'):
            await issuer.register_first_generation(grant, new_registration)
        replacement.send('claim')
        kind, code = await message(replacement)
        assert kind == 'claim-denied' and code == 'PG_PROCESS_LEASE_CAPABILITY_INVALID'
        original.send('resume')
        kind, code = await message(original)
        assert kind == 'write-denied' and code == 'PG_PROCESS_LEASE_EXPIRED_OR_UNQUALIFIED'
        after = await f.snapshot(cfg.binding)
        assert after.cut == before.cut and after.balance == before.balance and after.budget == before.budget
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.process_lease_commands') == 1
        assert await database[0].fetchval('SELECT lease_epoch FROM life_financial.process_leases') == 1
    finally:
        for pipe in pipes:
            pipe.close()
        for process in processes:
            await asyncio.to_thread(process.join, 5)
            if process.is_alive():
                process.terminate()
                await asyncio.to_thread(process.join, 5)
        await f.close()
