from copy import deepcopy

import pytest
from sqlalchemy import select

from hummingbot.core.admin_identity import AccessDenied, Principal
from hummingbot.core.bot_ownership import BotOwnership
from hummingbot.core.recovery import fingerprint
from hummingbot.model.admin_access import AdminAuditEvent
from hummingbot.model.bot_state_store import BotStateStore, ControlConflict
from hummingbot.model.db_migration.postgres.runner import bootstrap_postgres
from hummingbot.model.recovery_store import RecoveryStore

ADMIN = Principal('basic:operator', 'basic', 'admin')


@pytest.fixture
def recovering(pg_engine):
    bootstrap_postgres(pg_engine, auto_migrate=True)
    state = BotStateStore(pg_engine, 'test'); state.ensure()
    owner = BotOwnership(pg_engine, 'test'); assert owner.try_acquire()
    owner.require_recovery('Strategy initialization/reconciliation failed; inspect sanitized logs.')
    try:
        yield RecoveryStore(state), owner
    finally:
        owner.release()


@pytest.mark.parametrize('role', ['read', 'write'])
def test_recovery_requires_admin_at_persistence_boundary(recovering, role):
    store, owner = recovering
    with pytest.raises(AccessDenied):
        store.request('recovery_check', 'key', {'expected_version': 0}, Principal('basic:test', 'basic', role), 'req')
    assert store.latest() is None


def ready_check(store, owner):
    row = store.request('recovery_check', 'check', {'expected_version': 0}, ADMIN, 'req')
    claimed = store.claim(owner)
    snap = store.snapshot()
    store.finish_check(owner, claimed, snap, [], dict(verified=True), blockers=[])
    return store.get(row['id'])


def test_check_idempotency_and_confirmed_reset_leave_stopped_with_audit(recovering):
    store, owner = recovering
    check = ready_check(store, owner)
    assert store.request('recovery_check', 'check', {'expected_version': 0}, ADMIN, 'req')['id'] == check['id']
    payload = dict(expected_version=0, check_id=check['id'], confirmation_token=check['result']['confirmation_token'])
    request = store.request('recovery_reset', 'reset', payload, ADMIN, 'reset-request')
    claimed = store.claim(owner); snap = store.snapshot()
    assert fingerprint(snap) == check['result']['_private']['fingerprint']
    assert store.finish_reset(owner, claimed, snap, [], dict(verified=True))
    state = owner.check_owner()
    assert state['desired_state'] == 'stopped' and not state['recovery_state'].get('halt')
    assert state['control_version'] == 1 and 'recovery_operation' not in state['recovery_state']
    assert store.request('recovery_reset', 'reset', payload, ADMIN, 'again')['id'] == request['id']
    assert owner.check_owner()['control_version'] == 1
    with store.engine.connect() as db:
        actions = set(db.execute(select(AdminAuditEvent.action)).scalars())
    assert {'recovery_check_requested', 'recovery_check_completed', 'recovery_reset_requested', 'recovery_reset_completed'} <= actions


def test_stale_control_or_tampered_confirmation_never_clears_halt(recovering):
    store, owner = recovering
    check = ready_check(store, owner)
    for payload in [dict(expected_version=1, check_id=check['id'], confirmation_token=check['result']['confirmation_token']),
                    dict(expected_version=0, check_id=check['id'], confirmation_token='tampered')]:
        with pytest.raises(ControlConflict):
            store.request('recovery_reset', 'bad', payload, ADMIN, 'req')
    assert owner.check_owner()['recovery_state']['halt']


def test_financial_or_risk_drift_after_preview_requires_new_check(recovering):
    store, owner = recovering
    check = ready_check(store, owner)
    payload = dict(expected_version=0, check_id=check['id'], confirmation_token=check['result']['confirmation_token'])
    store.request('recovery_reset', 'reset', payload, ADMIN, 'req')
    claimed = store.claim(owner); before = store.snapshot()
    owner._mutate_recovery(lambda state: state.update(strategy_checkpoints={'x': {'max_global_pnl': '123'}}))
    with pytest.raises(ControlConflict):
        store.finish_reset(owner, claimed, before, [], dict(verified=True))
    assert owner.check_owner()['recovery_state']['halt']


def test_proof_cannot_cross_owner_generation(recovering):
    store, owner = recovering
    check = ready_check(store, owner)
    owner.release(); assert owner.try_acquire()
    with pytest.raises(ControlConflict):
        store.request('recovery_reset', 'reset', dict(expected_version=0, check_id=check['id'],
                      confirmation_token=check['result']['confirmation_token']), ADMIN, 'req')
    assert owner.check_owner()['recovery_state']['halt']


def seed_stale_position(store):
    from hummingbot.model.order import Order
    from hummingbot.model.order_status import OrderStatus
    from hummingbot.model.position import Position
    from hummingbot.model.trade_fill import TradeFill
    with store.engine.begin() as db:
        db.execute(Order.__table__.insert().values(id='a', config_file_path='life_mm_profiles.yaml', strategy='life_mm',
                                                   market='okx', symbol='BTC-USDT', base_asset='BTC', quote_asset='USDT', creation_timestamp=1000,
                                                   order_type='LIMIT_MAKER', amount=1, price=100, last_status='BuyOrderCompleted', last_update_timestamp=2000,
                                                   exchange_order_id='10', position='NIL'))
        db.execute(OrderStatus.__table__.insert().values(order_id='a', timestamp=2000, status='BuyOrderCompleted'))
        db.execute(TradeFill.__table__.insert().values(config_file_path='life_mm_profiles.yaml', strategy='life_mm',
                                                       market='okx', symbol='BTC-USDT', base_asset='BTC', quote_asset='USDT', timestamp=2000, order_id='a',
                                                       trade_type='BUY', order_type='LIMIT_MAKER', price=100, amount=1, exchange_trade_id='1', position='NIL',
                                                       trade_fee_in_quote='.1', trade_fee={'percent': '0', 'flat_fees': [{'token': 'USDT', 'amount': '.1'}]}))
        db.execute(Position.__table__.insert().values(id='position', controller_id='spot', connector_name='okx',
                                                      side='BUY', trading_pair='BTC-USDT', timestamp=1000, amount=0, volume_traded_quote=0,
                                                      breakeven_price=0, unrealized_pnl_quote=0, realized_pnl_quote=0, cum_fees_quote=0))


def position_check(store, owner):
    from hummingbot.core.recovery import replay_positions
    row = store.request('recovery_check', 'check', {'expected_version': 0}, ADMIN, 'req')
    claimed = store.claim(owner)
    snapshot = store.snapshot()
    plan = replay_positions(snapshot)
    store.finish_check(owner, claimed, snapshot, plan, {'verified': True}, blockers=[])
    return store.get(row['id']), plan


@pytest.mark.parametrize('fail_audit', [False, True])
def test_position_repair_and_audit_commit_together_or_rollback(recovering, monkeypatch, fail_audit):
    from hummingbot.model.position import Position
    store, owner = recovering
    seed_stale_position(store)
    check, plan = position_check(store, owner)
    payload = dict(expected_version=0, check_id=check['id'], confirmation_token=check['result']['confirmation_token'])
    store.request('recovery_reset', 'reset', payload, ADMIN, 'reset')
    claimed, before = store.claim(owner), store.snapshot()
    audit = store.access.audit

    def guarded_audit(*args, **kwargs):
        audit(*args, **kwargs)
        if kwargs['action'] == 'recovery_reset_completed':
            raise RuntimeError('injected failure after all financial writes')

    if fail_audit:
        monkeypatch.setattr(store.access, 'audit', guarded_audit)
        with pytest.raises(RuntimeError, match='injected'):
            store.finish_reset(owner, claimed, before, plan, {'verified': True})
    else:
        store.finish_reset(owner, claimed, before, plan, {'verified': True})
    with store.engine.connect() as db:
        position = db.execute(select(Position)).mappings().one()
        repairs = db.execute(select(AdminAuditEvent).where(AdminAuditEvent.action == 'recovery_position_repaired')).mappings().all()
    assert position['amount'] == (0 if fail_audit else 1)
    assert len(repairs) == (0 if fail_audit else 1)
    assert bool(owner.check_owner()['recovery_state'].get('halt')) == fail_audit
    assert store.get(claimed['id'])['status'] == ('applying' if fail_audit else 'succeeded')
    assert len(store.snapshot()['fills']) == len(before['fills']) == 1


def test_expired_proof_cannot_be_confirmed(recovering):
    from hummingbot.model.admin_command import AdminCommand
    store, owner = recovering
    check = ready_check(store, owner)
    report = deepcopy(check['result'])
    report['expires_at'] = '2000-01-01T00:00:00+00:00'
    with store.engine.begin() as db:
        db.execute(AdminCommand.__table__.update().where(AdminCommand.id == check['id']).values(result=report))
    with pytest.raises(ControlConflict, match='recovery_proof_expired'):
        store.request('recovery_reset', 'reset', dict(expected_version=0, check_id=check['id'],
                                                      confirmation_token=check['result']['confirmation_token']), ADMIN, 'req')
    assert owner.check_owner()['recovery_state']['halt']


def test_risk_stop_check_explains_blocker_without_reset(recovering):
    store, owner = recovering
    owner._mutate_recovery(lambda state: state.update(strategy_checkpoints={'x': {'_is_stop_triggered': True}}))
    row = store.request('recovery_check', 'check', {'expected_version': 0}, ADMIN, 'req')
    claimed, snap = store.claim(owner), store.snapshot()
    store.finish_check(owner, claimed, snap, [], {'verified': False}, blockers=['recovery_risk_stop'])
    report = store.get(row['id'])['result']
    assert not report['can_recover'] and report['confirmation_token'] is None
    assert owner.check_owner()['recovery_state']['strategy_checkpoints']['x']['_is_stop_triggered']


def test_role_epoch_change_invalidates_queued_operation(recovering):
    from test.integration.postgres.test_admin_access import seed_account

    from hummingbot.model.admin_access import AdminAccount
    store, owner = recovering
    seed_account(store.access, role='admin')
    actor = store.access.resolve_google(dict(iss='https://accounts.google.com', sub='boss-sub', email='boss@gmail.com'))
    row = store.request('recovery_check', 'check', {'expected_version': 0}, actor, 'req')
    with store.engine.begin() as db:
        db.execute(AdminAccount.__table__.update().values(version=2))
    with pytest.raises(ControlConflict, match='recovery_actor_changed'):
        store.claim(owner)
    store.fail(owner, row['id'], 'recovery_actor_changed')
    assert store.get(row['id'])['status'] == 'failed'
    assert owner.check_owner()['recovery_state']['halt']


def test_crash_owner_change_fails_incomplete_operation_without_repair(recovering):
    store, owner = recovering
    row = store.request('recovery_check', 'check', {'expected_version': 0}, ADMIN, 'req')
    store.claim(owner)
    owner.release()
    assert owner.try_acquire()
    assert store.claim(owner) is None
    assert store.get(row['id'])['error'] == 'recovery_owner_changed'
    assert owner.check_owner()['recovery_state']['halt']
    assert 'recovery_operation' not in owner.check_owner()['recovery_state']


def test_recovery_fences_other_control_and_credential_mutations(recovering):
    from hummingbot.model.credential_store import CredentialStore
    store, owner = recovering
    store.request('recovery_check', 'check', {'expected_version': 0}, ADMIN, 'req')
    with pytest.raises(ControlConflict, match='recovery'):
        store.state.control(expected_version=0, desired_state='running')
    with store.engine.begin() as db:
        with pytest.raises(ControlConflict, match='recovery'):
            CredentialStore(store.state).lock_request(db, ADMIN, 'credentials', {'connector': 'okx'})


@pytest.mark.parametrize('conflicting', [False, True])
def test_snapshot_distinguishes_missing_and_conflicting_local_side(recovering, conflicting):
    from sqlalchemy import delete

    from hummingbot.model.order_status import OrderStatus
    store, _ = recovering
    seed_stale_position(store)
    with store.engine.begin() as db:
        if conflicting:
            db.execute(OrderStatus.__table__.insert().values(order_id='a', timestamp=2001, status='SellOrderCreated'))
        else:
            db.execute(delete(OrderStatus).where(OrderStatus.order_id == 'a'))
    order = store.snapshot()['orders'][0]
    assert order['side'] is None and order['side_ambiguous'] == conflicting
