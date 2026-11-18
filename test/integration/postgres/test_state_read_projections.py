import pytest
from sqlalchemy import text

from hummingbot.core.bot_ownership import BotOwnership
from hummingbot.core.control_errors import ControlConflict
from hummingbot.model.bot_state import BotState
from hummingbot.model.bot_state_store import BotStateStore
from hummingbot.model.db_migration.postgres.runner import bootstrap_postgres


def test_publication_projection_excludes_private_recovery_and_catalogue_but_reads_fresh_control(pg_engine):
    bootstrap_postgres(pg_engine, auto_migrate=True)
    store = BotStateStore(pg_engine, "hummingbot")
    store.ensure()
    with pg_engine.begin() as connection:
        connection.execute(BotState.__table__.update().values(
            recovery_state={"halt": True, "private_diagnostic": "not_for_reporting"},
            mm_session={"id": "session", "generation": 2, "status": "running", "catalogue": {"large": "configuration"}},
            status={"running": False, "phase": "halted"}))
    full = store.read()
    public = store.read(publication=True)
    assert "recovery_state" not in public
    summary = public['recovery_summary']
    assert {key: summary[key] for key in ('halted', 'pending_mutations', 'credential_operation_pending',
                                          'recovery_operation_pending', 'automatic_recovery', 'shutdown_pending')} == {
        'halted': True, 'pending_mutations': False, 'credential_operation_pending': False,
        'recovery_operation_pending': False, 'automatic_recovery': None, 'shutdown_pending': False}
    assert summary['reason'] is None and summary['halt_kind'] is None
    assert summary['shutdown_incomplete'] is None
    assert summary['halt_diagnostic'] == dict(reason_code=None, occurred_at=None, revision=None,
                                              runtime_reason_code=None, component=None, task=None, task_state=None)
    assert "private_diagnostic" not in str(public)
    assert "catalogue" not in public["mm_session"]
    assert public["mm_session"] == {key: value for key, value in full["mm_session"].items() if key != "catalogue"}
    assert public["status"] == full["status"]
    assert full["recovery_state"]["halt"] and "catalogue" in full["mm_session"]
    with pg_engine.begin() as connection:
        connection.execute(BotState.__table__.update().values(recovery_state={
            "halt": True, "recovery_operation": {"id": "private-operation", "proof": "not_for_reporting"}}))
    public = store.read(publication=True)
    assert public["recovery_summary"]["recovery_operation_pending"] is True
    assert "private-operation" not in str(public) and "not_for_reporting" not in str(public)
    with pytest.raises(ControlConflict, match='recovery operation'):
        store.control(expected_version=0)
    with pg_engine.begin() as connection:
        connection.execute(BotState.__table__.update().values(recovery_state={"halt": True}))
    store.control(expected_version=0)
    assert store.read(publication=True)["control_version"] == 1
    with pg_engine.begin() as connection:
        connection.execute(text("UPDATE bot_state SET mm_session=NULL"))
    assert store.read(publication=True)["mm_session"] is None
    with pg_engine.begin() as connection:
        connection.execute(BotState.__table__.update().values(mm_session=None))
    assert store.read()["mm_session"] is None
    assert store.read(publication=True)["mm_session"] is None


def test_owner_gate_keeps_fresh_authority_without_reporting_payload(pg_engine):
    bootstrap_postgres(pg_engine, auto_migrate=True)
    store = BotStateStore(pg_engine, "hummingbot")
    store.ensure()
    owner = BotOwnership(pg_engine, "hummingbot")
    assert owner.try_acquire()
    try:
        with pg_engine.begin() as connection:
            connection.execute(BotState.__table__.update().values(status={"large_report": "unused"}, recovery_state={"halt": True}))
        row = owner.check_owner()
        assert "status" not in row
        assert row["recovery_state"]["halt"] is True
        assert row["backend_pid"] == owner.backend_pid and row["owner_generation"] == owner.generation
        store.control(expected_version=0, desired_state="stopped")
        assert owner.check_owner()["control_version"] == 1
    finally:
        owner.release()


@pytest.mark.parametrize('halt,kind,reason,expected', [
    (True, 'shutdown_pending', None, True),
    (False, 'shutdown_pending', None, False),
    (True, 'manual', None, False),
    (True, 'shutdown_pending', 'drawdown_controller_limit', False),
    (True, 'shutdown_pending', 'private diagnostic string', False),
])
def test_publication_distinguishes_shutdown_fence_and_allowlists_reason(pg_engine, halt, kind, reason, expected):
    bootstrap_postgres(pg_engine, auto_migrate=True)
    store = BotStateStore(pg_engine, 'hummingbot')
    store.ensure()
    with pg_engine.begin() as connection:
        connection.execute(BotState.__table__.update().values(
            recovery_state={'halt': halt, 'halt_kind': kind, 'reason': reason}))
    public = store.read(publication=True)
    assert public['recovery_summary']['shutdown_pending'] is expected
    assert public['recovery_summary']['halted'] is halt
    assert public['recovery_summary']['halt_kind'] == ('shutdown_pending' if kind == 'shutdown_pending' and reason is None else None)
    assert public['recovery_summary']['reason'] == ('drawdown_controller_limit' if reason == 'drawdown_controller_limit' else None)
    assert 'private diagnostic string' not in str(public)


def test_publication_overview_uses_pinned_policy_without_loading_full_catalogue(pg_engine):
    from test.hummingbot.cli.test_admin_overview import row

    from hummingbot.cli.admin_overview import build_overview

    bootstrap_postgres(pg_engine, auto_migrate=True)
    store = BotStateStore(pg_engine, 'hummingbot')
    store.ensure()
    source = row()
    session = source['mm_session']
    session.update(request={'market_mode': 'both'}, catalogue={'private_unused': 'large-archived-catalogue' + 'x' * 500_000, 'markets': {
        'spot': {'id': 'demo_btc_spot', 'total_amount_quote': '100', 'private_unused': 'not_for_reporting',
                 'quote_guards': {'enabled': True, 'mode': 'follow_market', 'max_deviation_bps': '42',
                                  'private_unused': 'not_for_reporting'}},
        'perpetual': {'id': 'demo_btc_perpetual', 'total_amount_quote': '100'}}})
    source['status']['mm_drawdown'].update(max_controller_drawdown_quote='2500', controllers={
        'demo_btc_spot': {'drawdown_quote': '12'}, 'demo_btc_perpetual': {'drawdown_quote': '3'}})
    with pg_engine.begin() as connection:
        connection.execute(BotState.__table__.update().values(mm_session=session, status=source['status']))
    public = store.read(publication=True)
    overview = build_overview(public)
    assert overview.session.quote_budget.value is not None, 'The real /status projection must retain pinned quote budgets'
    assert overview.session.quote_budget.value.total_usdt == 200
    rules = {rule.controller_id: rule for rule in overview.risk_rules.value if rule.rule_id == 'drawdown_controller'}
    assert rules['demo_btc_spot'].market == 'spot'
    assert rules['demo_btc_perpetual'].market == 'perpetual'
    projected = public['overview_session_policy']
    assert len(str(projected)) < 1000
    assert next(rule for rule in overview.risk_rules.value if rule.rule_id == 'quote_deviation').upper_limit == 42
    assert 'catalogue' not in public['mm_session']
    assert 'large-archived-catalogue' not in str(public) and 'not_for_reporting' not in str(public)
    with pg_engine.begin() as connection:
        session['catalogue']['markets']['spot']['total_amount_quote'] = '50'
        session['request']['market_mode'] = 'spot'
        connection.execute(BotState.__table__.update().values(mm_session=session))
    assert build_overview(store.read(publication=True)).session.quote_budget.value.total_usdt == 50
