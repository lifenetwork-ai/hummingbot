"""Slow final ledger proof must not starve the stopped owner's heartbeat."""
import threading
from concurrent.futures import ThreadPoolExecutor
from test.integration.postgres.test_checkpoint_recovery import seed_checkpoint
from test.integration.postgres.test_recovery_store import ADMIN, recovering as _recovering

import pytest

recovering = _recovering


class WorkerOwner:
    """Physical authority checked on fixture thread; DB fence still runs for real."""

    def __init__(self, owner):
        self.owner_uuid, self.generation = owner.owner_uuid, owner.generation

    def check_owner(self):
        pass


@pytest.mark.parametrize('phase', ['finish_check', 'request_reset', 'finish_reset'])
def test_slow_checkpoint_fingerprint_does_not_lock_out_status_publication(recovering, monkeypatch, phase):
    monkeypatch.setenv('OKX_TRADING_ENV', 'demo')
    monkeypatch.setenv('OKX_SITE', 'www')
    store, owner = recovering
    seed_checkpoint(store, orders=12, fills=3)
    store.request('recovery_check', 'check', dict(expected_version=0), ADMIN, 'check')
    row, data = store.claim(owner), store.snapshot()
    proof = dict(verified=True, scope='verified_checkpoint', venue_heads={'okx': '3'})
    if phase != 'finish_check':
        store.finish_check(owner, row, data, [], proof, blockers=[])
        check = store.get(row['id'])
        payload = dict(expected_version=0, check_id=row['id'], confirmation_token=check['result']['confirmation_token'])
        if phase == 'finish_reset':
            store.request('recovery_reset', 'reset', payload, ADMIN, 'reset')
            row, data = store.claim(owner), store.snapshot()
    physical = owner.check_owner()
    entered, release = threading.Event(), threading.Event()
    snapshot = store.snapshot

    def slow_snapshot(connection=None):
        if connection is not None:
            entered.set()
            assert release.wait(5), 'Test did not release the delayed ledger read'
        return snapshot(connection)

    monkeypatch.setattr(store, 'snapshot', slow_snapshot)

    def finalize():
        if phase == 'request_reset':
            return store.request('recovery_reset', 'reset', payload, ADMIN, 'reset')
        return getattr(store, phase)(WorkerOwner(owner), row, data, [], proof,
                                     **({'blockers': []} if phase == 'finish_check' else {}))

    def publish():
        # Same production publication transaction, with a short DB deadline
        # solely on this test connection so an unsafe row lock fails promptly.
        from sqlalchemy import create_engine, event
        from sqlalchemy.pool import NullPool

        from hummingbot.model.bot_state_store import BotStateStore
        engine = create_engine(store.engine.url, poolclass=NullPool)

        @event.listens_for(engine, 'connect')
        def deadline(connection, _):
            with connection.cursor() as cursor:
                cursor.execute("SET statement_timeout='200ms'")
            connection.commit()
        try:
            publication = BotStateStore(engine, store.bot_id)
            return publication.publish(owner_uuid=owner.owner_uuid, owner_generation=owner.generation,
                                       version=0, status={'running': False}, save_balances=False)
        finally:
            engine.dispose()

    with ThreadPoolExecutor(max_workers=2) as work:
        task = work.submit(finalize)
        try:
            assert entered.wait(3)
            assert work.submit(publish).result(timeout=2), 'Status publication must remain available during proof'
            assert owner.check_owner()['owner_generation'] == physical['owner_generation']
        finally:
            release.set()
            task.result(timeout=5)


@pytest.mark.parametrize('phase', ['finish_check', 'request_reset', 'finish_reset'])
def test_risk_drift_during_precomputed_checkpoint_proof_is_still_rejected(recovering, monkeypatch, phase):
    monkeypatch.setenv('OKX_TRADING_ENV', 'demo')
    store, owner = recovering
    seed_checkpoint(store, orders=12, fills=3)
    store.request('recovery_check', 'check', dict(expected_version=0), ADMIN, 'check')
    row, data = store.claim(owner), store.snapshot()
    proof = dict(verified=True, scope='verified_checkpoint', venue_heads={'okx': '3'})
    if phase != 'finish_check':
        store.finish_check(owner, row, data, [], proof, blockers=[])
        check = store.get(row['id'])
        payload = dict(expected_version=0, check_id=row['id'], confirmation_token=check['result']['confirmation_token'])
        if phase == 'finish_reset':
            store.request('recovery_reset', 'reset', payload, ADMIN, 'reset')
            row, data = store.claim(owner), store.snapshot()
    snapshot = store.snapshot
    changed = False

    def read_then_change(connection=None):
        nonlocal changed
        result = snapshot(connection)
        if connection is not None and not changed:
            changed = True
            # Separate connection changes authority after the bulk read. It
            # must be compared again under the final BotState lock.
            owner._mutate_recovery(lambda state: state.update(policy_changed=True))
        return result

    monkeypatch.setattr(store, 'snapshot', read_then_change)
    from hummingbot.model.bot_state_store import ControlConflict
    with pytest.raises(ControlConflict):
        if phase == 'request_reset':
            store.request('recovery_reset', 'reset', payload, ADMIN, 'reset')
        else:
            getattr(store, phase)(owner, row, data, [], proof,
                                  **({'blockers': []} if phase == 'finish_check' else {}))
    assert owner.check_owner()['recovery_state']['halt']


def test_financial_change_racing_reset_acceptance_is_rechecked_before_any_repair(recovering, monkeypatch):
    from sqlalchemy import update

    from hummingbot.model.bot_state_store import ControlConflict
    from hummingbot.model.order import Order
    monkeypatch.setenv('OKX_TRADING_ENV', 'demo')
    store, owner = recovering
    seed_checkpoint(store, orders=12, fills=3)
    store.request('recovery_check', 'check', dict(expected_version=0), ADMIN, 'check')
    row, data = store.claim(owner), store.snapshot()
    proof = dict(verified=True, scope='verified_checkpoint', venue_heads={'okx': '3'})
    store.finish_check(owner, row, data, [], proof, blockers=[])
    check = store.get(row['id'])
    payload = dict(expected_version=0, check_id=row['id'], confirmation_token=check['result']['confirmation_token'])
    snapshot = store.snapshot

    def read_then_edit(connection=None):
        result = snapshot(connection)
        if connection is not None:
            with store.engine.begin() as db:
                db.execute(update(Order).where(Order.id == 'o00000').values(price=101))
        return result

    monkeypatch.setattr(store, 'snapshot', read_then_edit)
    store.request('recovery_reset', 'reset', payload, ADMIN, 'reset')
    monkeypatch.setattr(store, 'snapshot', snapshot)
    claimed, fresh = store.claim(owner), store.snapshot()
    with pytest.raises(ControlConflict, match='recovery_state_changed'):
        store.finish_reset(owner, claimed, fresh, [], proof)
    assert owner.check_owner()['recovery_state']['halt']
    assert store.get(claimed['id'])['status'] == 'applying'
