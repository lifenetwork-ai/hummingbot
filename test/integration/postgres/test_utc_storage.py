from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from hummingbot.cli.admin_models import CommandResponse


def test_connections_force_utc_even_when_database_default_is_another_timezone(pg_engine):
    name = pg_engine.dialect.identifier_preparer.quote(pg_engine.url.database)
    with pg_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        connection.exec_driver_sql(f"ALTER DATABASE {name} SET timezone TO 'Asia/Bangkok'")
    pg_engine.dispose()
    try:
        with pg_engine.connect() as connection:
            assert connection.execute(text("SHOW TIME ZONE")).scalar_one() == "UTC"
            value = connection.execute(text("SELECT clock_timestamp()")).scalar_one()
            assert value.utcoffset() == timedelta(0)
    finally:
        with pg_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            connection.exec_driver_sql(f"ALTER DATABASE {name} RESET timezone")
        pg_engine.dispose()


def test_api_normalizes_offset_timestamps_to_utc_without_changing_the_instant():
    timestamp = datetime(2026, 1, 2, 0, 0, tzinfo=timezone(timedelta(hours=7)))
    receipt = CommandResponse(id="1", action="stop", session_id="session", expected_version=1, effective_version=2,
                              status="accepted", payload={"action": "stop", "session_id": "session", "expected_version": 1},
                              created_at=timestamp, updated_at=timestamp)
    assert receipt.model_dump(mode="json")["created_at"] == "2026-01-01T17:00:00Z"
