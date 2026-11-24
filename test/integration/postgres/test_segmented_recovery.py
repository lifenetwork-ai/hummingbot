"""Durable paged evidence is never authority until both scans commit fully."""
from copy import deepcopy
from datetime import datetime, timezone
from test.hummingbot.core.test_segmented_recovery import Venue
from test.integration.postgres.test_checkpoint_recovery import seed_checkpoint
from test.integration.postgres.test_recovery_store import ADMIN, recovering as _recovering

import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import OperationalError

from hummingbot.core.bot_ownership import OwnershipLost
from hummingbot.core.recovery import RecoveryBlocked
from hummingbot.core.segmented_recovery import verify_segment
from hummingbot.model.admin_command import AdminCommand
from hummingbot.model.bot_state import BotState
from hummingbot.model.bot_state_store import ControlConflict
from hummingbot.model.db_migration.postgres.io_deadline import DatabaseIOTimeout
from hummingbot.model.recovery_proof import RecoveryProofFill

recovering = _recovering


def begin(store, owner, size=101, *, principal=ADMIN):
    anchor = seed_checkpoint(store, orders=size + 1, fills=size + 1)
    store.request('recovery_check', 'check', dict(expected_version=0), principal, 'req')
    row, data = store.claim(owner), store.snapshot()
    venue = Venue(size)
    original = venue.fill

    def fill(identity):
        stamp = str(int(anchor.timestamp() * 1000) + 600000)
        return {**original(identity), 'clOrdId': f'o{identity:05}', 'ts': stamp, 'fillTime': stamp}

    venue.fill = fill
    return row, data, venue


async def segment(store, owner, row, data, venue, *, budget=100, interrupt=None):
    progress = store.get(row['id'])['result']['_private'].get('proof')

    async def lookup(keys, watermark):
        return store.checkpoint_fill_rows(keys, watermark)

    async def save(before, after, entries):
        store.save_proof_progress(owner, row, data, before, after, entries)
        if interrupt:
            interrupt(before, after, entries)

    return await verify_segment(data, {'okx': venue}, lookup, progress, save, page_budget=budget)


async def complete_proof(store, owner, row, data, venue):
    try:
        proof = await segment(store, owner, row, data, venue)
    except OperationalError as error:
        if not isinstance(error.orig, DatabaseIOTimeout):
            raise
        # This fixture drives the store directly. Resume from the durable
        # cursor as the service would after an ambiguous page COMMIT.
        proof = await segment(store, owner, row, data, venue)
    assert proof and proof['verified']
    return proof


def evidence_count(store):
    with store.engine.connect() as db:
        return db.scalar(select(func.count()).select_from(RecoveryProofFill))


@pytest.mark.asyncio
async def test_10001_native_fills_review_and_fresh_reset_across_jobs(recovering, monkeypatch):
    monkeypatch.setenv('OKX_TRADING_ENV', 'demo')
    store, owner = recovering
    row, data, venue = begin(store, owner, 10001)
    before = data['ledger_hash'], data['positions']
    for action in ('recovery_check', 'recovery_reset'):
        jobs, proof = 0, None
        while proof is None:
            proof = await segment(store, owner, row, store.snapshot(), venue)
            jobs += 1
            if proof is None:
                receipt = store.get(row['id'])
                assert not receipt['result']['can_recover']
                assert receipt['result']['confirmation_token'] is None
                assert owner.check_owner()['recovery_state']['halt']
                row = store.claim(owner)  # Recreated coordinator, same durable operation.
                assert jobs < 5
        assert jobs >= 3 and evidence_count(store) == 20002
        if action == 'recovery_check':
            store.finish_check(owner, row, store.snapshot(), [], proof, blockers=[])
            check = store.get(row['id'])
            assert check['result']['can_recover'] and evidence_count(store) == 0
            # The service heartbeat runs independently in production. This
            # direct-store test has no service observer or heartbeat task.
            with store.engine.begin() as db:
                db.execute(update(BotState).values(heartbeat=func.clock_timestamp()))
            store.request('recovery_reset', 'reset', dict(expected_version=0, check_id=row['id'],
                          confirmation_token=check['result']['confirmation_token']), ADMIN, 'reset')
            row = store.claim(owner)
        else:
            store.finish_reset(owner, row, store.snapshot(), [], proof)
    after = store.snapshot()
    assert (after['ledger_hash'], after['positions']) == before
    assert evidence_count(store) == 0 and after['checkpoint']['expected_fills'] == 0
    state = owner.check_owner()
    assert state['desired_state'] == 'stopped' and not state['recovery_state'].get('halt')


@pytest.mark.asyncio
async def test_partial_proof_cannot_be_promoted_by_forged_verified_result(recovering, monkeypatch):
    monkeypatch.setenv('OKX_TRADING_ENV', 'demo')
    store, owner = recovering
    row, data, venue = begin(store, owner)
    await segment(store, owner, row, data, venue, budget=1)
    with pytest.raises(ControlConflict):
        store.finish_check(owner, row, data, [], dict(verified=True, scope='verified_checkpoint'), blockers=[])
    assert owner.check_owner()['recovery_state']['halt'] and evidence_count(store) == 100


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['after_commit', 'before_commit'])
async def test_page_cursor_and_identity_commit_are_atomic_and_retry_idempotent(recovering, monkeypatch, fault):
    monkeypatch.setenv('OKX_TRADING_ENV', 'demo')
    store, owner = recovering
    row, data, venue = begin(store, owner)
    original = store.save_proof_progress
    calls = []

    def interrupted(owner, row, data, before, after, entries):
        if entries and not calls:
            calls.append((deepcopy(before), deepcopy(after), entries))
            if fault == 'after_commit':
                original(owner, row, data, before, after, entries)
            raise OSError('lost worker or commit acknowledgement')
        return original(owner, row, data, before, after, entries)

    monkeypatch.setattr(store, 'save_proof_progress', interrupted)
    with pytest.raises(OSError):
        await segment(store, owner, row, data, venue)
    assert evidence_count(store) == (100 if fault == 'after_commit' else 0)
    before, after, entries = calls[0]
    # Repeating the exact committed page is a no-op; no partial insert survives.
    original(owner, row, data, before, after, entries)
    original(owner, row, data, before, after, entries)
    assert evidence_count(store) == 100
    monkeypatch.setattr(store, 'save_proof_progress', original)
    proof = await segment(store, owner, store.claim(owner), store.snapshot(), venue)
    assert proof['verified'] and evidence_count(store) == 202


@pytest.mark.asyncio
async def test_owner_loss_after_identity_insert_rolls_back_page_and_cursor(recovering, monkeypatch):
    from unittest.mock import MagicMock
    monkeypatch.setenv('OKX_TRADING_ENV', 'demo')
    store, owner = recovering
    row, data, venue = begin(store, owner)
    await segment(store, owner, row, data, venue, budget=1)
    before = store.get(row['id'])['result']['_private']['proof']
    state = owner.check_owner()
    with monkeypatch.context() as local:
        local.setattr(owner, 'check_owner', MagicMock(side_effect=[state, OwnershipLost('injected before commit')]))
        with pytest.raises(OwnershipLost):
            await segment(store, owner, row, data, venue)
    assert evidence_count(store) == 100
    assert store.get(row['id'])['result']['_private']['proof'] == before
    assert owner.check_owner()['recovery_state']['halt']


@pytest.mark.asyncio
async def test_duplicate_bill_in_later_page_rolls_back_its_cursor(recovering, monkeypatch):
    monkeypatch.setenv('OKX_TRADING_ENV', 'demo')
    store, owner = recovering
    row, data, venue = begin(store, owner)
    await segment(store, owner, row, data, venue, budget=1)
    before = store.get(row['id'])['result']['_private']['proof']
    original = venue.fill
    venue.fill = lambda identity: {**original(identity), 'billId': '101' if identity == 1 else str(identity)}
    with pytest.raises((ControlConflict, RecoveryBlocked)):
        await segment(store, owner, row, data, venue)
    assert store.get(row['id'])['result']['_private']['proof'] == before
    assert evidence_count(store) == 100
    store.fail(owner, row['id'], 'recovery_unrecorded_or_duplicate_venue_fill')
    assert evidence_count(store) == 0 and owner.check_owner()['recovery_state']['halt']


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['control', 'owner', 'risk', 'actor'])
async def test_inflight_page_cannot_cross_authority_change(recovering, monkeypatch, change):
    monkeypatch.setenv('OKX_TRADING_ENV', 'demo')
    store, owner = recovering
    actor = ADMIN
    if change == 'actor':
        from test.integration.postgres.test_admin_access import seed_account

        from hummingbot.model.admin_access import AdminAccount
        seed_account(store.access, role='admin')
        actor = store.access.resolve_google(dict(iss='https://accounts.google.com', sub='boss-sub', email='boss@gmail.com'))
    row, data, venue = begin(store, owner, principal=actor)
    await segment(store, owner, row, data, venue, budget=1)
    if change == 'owner':
        owner.release()
        assert owner.try_acquire()
    elif change == 'risk':
        owner._mutate_recovery(lambda state: state.update(policy_changed=True))
    elif change == 'actor':
        with store.engine.begin() as db:
            db.execute(update(AdminAccount).values(version=2))
    else:
        with store.engine.begin() as db:
            db.execute(update(BotState).values(control_version=1))
    with pytest.raises((ControlConflict, RecoveryBlocked)):
        await segment(store, owner, row, data, venue)
    assert evidence_count(store) == 100 and owner.check_owner()['recovery_state']['halt']


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['confirmation_expired', 'proof_expired', 'digest_changed'])
async def test_final_reset_rechecks_review_expiry_and_completed_proof(recovering, monkeypatch, fault):
    monkeypatch.setenv('OKX_TRADING_ENV', 'demo')
    store, owner = recovering
    row, data, venue = begin(store, owner)
    proof = await complete_proof(store, owner, row, data, venue)
    store.finish_check(owner, row, data, [], proof, blockers=[])
    check = store.get(row['id'])
    store.request('recovery_reset', 'reset', dict(expected_version=0, check_id=row['id'],
                  confirmation_token=check['result']['confirmation_token']), ADMIN, 'reset')
    row = store.claim(owner)
    data = store.snapshot()
    timed_out = False
    if fault == 'confirmation_expired':
        persist = store.save_proof_progress

        def lose_page_reply(owner, row, data, before, after, entries):
            nonlocal timed_out
            saved = persist(owner, row, data, before, after, entries)
            if entries and not timed_out:
                timed_out = True
                raise OperationalError(None, None, DatabaseIOTimeout('Test page COMMIT reply lost.'),
                                       connection_invalidated=True)
            return saved

        monkeypatch.setattr(store, 'save_proof_progress', lose_page_reply)
    proof = await complete_proof(store, owner, row, data, venue)
    assert timed_out is (fault == 'confirmation_expired')
    target = check if fault == 'confirmation_expired' else store.get(row['id'])
    report = deepcopy(target['result'])
    if fault == 'confirmation_expired':
        report['expires_at'] = '2000-01-01T00:00:00+00:00'
    elif fault == 'proof_expired':
        report['_private']['proof']['now_ms'] -= 31 * 60000
    else:
        report['_private']['proof']['first']['digest'] = 'a' * 64
    with store.engine.begin() as db:
        db.execute(update(AdminCommand).where(AdminCommand.id == target['id']).values(result=report))
    with pytest.raises((ControlConflict, RecoveryBlocked)):
        store.finish_reset(owner, row, data, [], proof)
    assert owner.check_owner()['recovery_state']['halt']
    assert store.get(row['id'])['status'] == 'applying' and evidence_count(store) == 202
    store.fail(owner, row['id'], 'recovery_check_unavailable')
    assert evidence_count(store) == 0


@pytest.mark.asyncio
async def test_transient_get_backoff_is_bounded_and_preserves_last_durable_cursor(recovering, monkeypatch):
    monkeypatch.setenv('OKX_TRADING_ENV', 'demo')
    store, owner = recovering
    row, data, venue = begin(store, owner)
    await segment(store, owner, row, data, venue, budget=1)
    progress = store.get(row['id'])['result']['_private']['proof']
    for attempt in range(1, 7):
        store.defer_proof(owner, row, 'recovery_exchange_unavailable')
        current = store.get(row['id'])
        if attempt <= 5:
            assert current['result']['stage'] == 'waiting_for_exchange'
            assert current['result']['_private']['proof'] == progress
            assert not current['result']['can_recover'] and store.claim(owner) is None
            result = deepcopy(current['result'])
            result['_private']['retry_at'] = datetime.fromtimestamp(0, timezone.utc).isoformat()
            with store.engine.begin() as db:
                db.execute(update(AdminCommand).where(AdminCommand.id == row['id']).values(result=result))
            row = store.claim(owner)
        else:
            assert current['status'] == 'failed' and evidence_count(store) == 0
    assert owner.check_owner()['recovery_state']['halt']


def test_migration_023_upgrades_v22_and_replays_without_changing_financial_rows(pg_engine):
    from hummingbot.model.db_migration.postgres.runner import PostgreSQLBootstrapError, bootstrap_postgres
    bootstrap_postgres(pg_engine, auto_migrate=True)
    with pg_engine.begin() as db:
        db.execute(text('DROP TABLE recovery_proof_fills'))
        db.execute(text("DELETE FROM hbot_schema_migrations WHERE name='023_create_recovery_proof_fills.sql'"))
    with pytest.raises(PostgreSQLBootstrapError):
        bootstrap_postgres(pg_engine, auto_migrate=False)
    bootstrap_postgres(pg_engine, auto_migrate=True)
    bootstrap_postgres(pg_engine, auto_migrate=True)
    bootstrap_postgres(pg_engine, auto_migrate=False)
    with pg_engine.connect() as db:
        assert db.scalar(text("SELECT count(*) FROM hbot_schema_migrations WHERE name='023_create_recovery_proof_fills.sql'")) == 1
        assert db.scalar(select(func.count()).select_from(RecoveryProofFill)) == 0
