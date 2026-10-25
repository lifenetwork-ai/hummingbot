from decimal import Decimal

import pytest
from sqlalchemy import event, select

from hummingbot.core.bot_ownership import BotOwnership
from hummingbot.model.bot_state_store import BotStateStore, ControlConflict
from hummingbot.model.db_migration.postgres.runner import bootstrap_postgres
from hummingbot.model.funding_bill import FundingBill, FundingBillStore


@pytest.fixture
def funding(pg_engine):
    bootstrap_postgres(pg_engine, auto_migrate=True)
    BotStateStore(pg_engine, "hummingbot").ensure()
    owner = BotOwnership(pg_engine, "hummingbot")
    assert owner.try_acquire()
    try:
        yield FundingBillStore(pg_engine, "hummingbot", owner_backend_pid=owner.backend_pid), owner, pg_engine
    finally:
        owner.release()


def bill(identity="1", **changes):
    row = dict(account_id="account", environment="demo", bill_id=identity, symbol="BTC-USDT",
               timestamp=1700000000000, currency="USDT", amount=Decimal("-0.0000001234567890123456789"), rate=None)
    return {**row, **changes}


def commit(store, owner, rows, **changes):
    checkpoint = dict(account_id="account", environment="demo", pairs=["BTC-USDT"],
                      covered_from_ms=1699999900000, covered_until_ms=1700000000001,
                      last_success_ms=1700000000001, pending=None)
    return store.commit(owner.owner_uuid, owner.generation, rows, {**checkpoint, **changes})


def test_exact_bills_replay_same_time_and_account_environment_isolation(funding):
    store, owner, engine = funding
    rows = [bill(), bill("2"), bill(environment="live"), bill(account_id="second")]
    commit(store, owner, rows)
    commit(store, owner, rows)
    with engine.connect() as c:
        actual = c.execute(select(FundingBill)).mappings().all()
        assert len(actual) == 4
        assert all(row["amount"] == Decimal("-0.0000001234567890123456789") for row in actual)
    assert store.load()["covered_until_ms"] == 1700000000001
    before = store.load()
    with pytest.raises(ValueError, match="conflict"):
        commit(store, owner, [bill(amount=Decimal("2"))], covered_until_ms=1700000000100)
    assert store.load() == before


def test_page_and_checkpoint_rollback_and_stale_owner(funding):
    store, owner, engine = funding

    def fail(connection):
        raise RuntimeError("injected commit failure")
    event.listen(engine, "commit", fail)
    try:
        with pytest.raises(RuntimeError, match="injected"):
            commit(store, owner, [bill()])
    finally:
        event.remove(engine, "commit", fail)
    assert store.load() == {}
    with engine.connect() as c:
        assert c.execute(select(FundingBill)).first() is None
    with pytest.raises(ControlConflict):
        store.commit("stale", owner.generation, [bill()], {"covered_until_ms": 1})
    assert store.load() == {}


def test_bill_migration_replay_keeps_exact_rows(funding):
    store, owner, engine = funding
    commit(store, owner, [bill()])
    for enabled in (True, True, False):
        bootstrap_postgres(engine, auto_migrate=enabled)
        with engine.connect() as c:
            assert c.execute(select(FundingBill.amount)).scalar_one() == bill()["amount"]


def test_daily_prefers_exact_identified_bills_without_legacy_double_counting(funding):
    from sqlalchemy.orm import Session

    from hummingbot.cli.admin_models import DailyResponse
    from hummingbot.model.admin_reporting import daily
    from hummingbot.model.funding_payment import FundingPayment
    store, owner, engine = funding
    with Session(engine) as s, s.begin():
        s.add(FundingPayment(timestamp=1700000000000, config_file_path='old.yml', market='okx_perpetual',
                             symbol='BTC-USDT', rate=.001, amount=-.3))
    commit(store, owner, [bill(), bill('2'), bill(account_id='other', amount=Decimal('-9'))])
    result = daily(engine, start_day='2023-11-14', end_day='2023-11-15', reporting_timezone='UTC')
    spot, perp = result['markets']
    assert spot['funding_status'] == 'not_applicable'
    assert perp['days'][0]['funding_paid'] == -bill()['amount'] * 2
    assert result['funding_sync']['legacy_history_unverified'] is True
    payload = DailyResponse.model_validate(result).model_dump(mode='json')
    assert payload['trade_storage_scale'] is None
    assert Decimal(payload['markets'][1]['days'][0]['funding_paid']) == -bill()['amount'] * 2


def test_killed_ownership_backend_cannot_commit_funding_page(funding):
    from sqlalchemy import text
    store, owner, engine = funding
    with engine.begin() as c:
        assert c.execute(text('SELECT pg_terminate_backend(:pid)'), {'pid': owner.backend_pid}).scalar()
    with pytest.raises(ControlConflict):
        commit(store, owner, [bill()])
    assert store.load() == {}


def test_page_does_not_overwrite_concurrent_halt_or_control_intent(funding):
    from sqlalchemy import text
    store, owner, engine = funding
    with engine.begin() as c:
        c.execute(text("UPDATE bot_state SET desired_state='stopped', recovery_state=CAST(:state AS jsonb)"),
                  {"state": '{"halt":true,"reason":"review"}'})
    commit(store, owner, [bill()])
    with engine.connect() as c:
        row = c.execute(text('SELECT desired_state,recovery_state FROM bot_state')).one()
    assert row[0] == 'stopped' and row[1]['halt'] is True and row[1]['reason'] == 'review'


def test_unobserved_funding_is_not_reported_as_free(funding):
    from test.integration.postgres.test_admin_daily import add_fill, millis

    from hummingbot.model.admin_reporting import daily
    _, _, engine = funding
    add_fill(engine, 'no-settlement', 'okx_perpetual', millis('2026-01-01T12:00:00+00:00'), '0', Decimal(0))
    result = daily(engine, start_day='2026-01-01', end_day='2026-01-02', reporting_timezone='UTC', market='perpetual')
    assert result['markets'][0]['days'][0]['funding_paid'] is None
    assert result['markets'][0]['days'][0]['funding_received'] is None
    assert result['funding_sync']['covered_until_ms'] is None
