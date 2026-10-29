"""Small replayable SQL runner. No ORM DDL, semicolon splitting or file DB migrator."""
import hashlib
import logging
import threading
import time
from pathlib import Path
from typing import Optional

from sqlalchemy import Float, Numeric, Sequence, inspect, text
from sqlalchemy.engine import Connection, Engine

from hummingbot.model import get_declarative_base
from hummingbot.model.db_migration.postgres.io_deadline import bootstrap_io_budget

LOGGER = logging.getLogger(__name__)
SCRIPTS = Path(__file__).resolve().parent / "scripts"
MIGRATION_LOCK = 0x48424F544D494752  # HBOTMIGR; locks are scoped to the connected database
LEGACY_TABLES = (
    "Order", "OrderStatus", "TradeFill", "MarketState", "Executors", "Controllers", "Position",
    "FundingPayment", "InventoryCost", "MarketData", "RangePositionUpdate", "RangePositionCollectedFees", "Metadata",
)


class PostgreSQLBootstrapError(RuntimeError):
    """Safe diagnostics: never include driver messages, SQL parameters or the URL."""

    def __init__(self, message, *, retryable=False):
        super().__init__(message)
        self.retryable = retryable


def reject_legacy_schema(connection: Connection) -> None:
    names = set(inspect(connection).get_table_names(schema="public"))
    legacy = sorted(names.intersection(LEGACY_TABLES))
    if legacy:
        raise PostgreSQLBootstrapError(
            "Legacy PostgreSQL tables detected: " + ", ".join(legacy)
            + ". Stop legacy writers and perform the explicit canonical-name cutover before startup."
        )


def _type_matches(actual, expected, dialect) -> bool:
    expected = expected.dialect_impl(dialect)
    if hasattr(expected, "impl"):
        expected = expected.impl
    if isinstance(expected, Float):
        return isinstance(actual, Float) and not isinstance(actual, Numeric)
    return str(actual.compile(dialect=dialect)) == str(expected.compile(dialect=dialect))


def validate_schema(connection: Connection) -> None:
    """Read-only catalog validation; safe with a DML-only runtime role."""
    reject_legacy_schema(connection)
    inspector = inspect(connection)
    tables = set(inspector.get_table_names(schema="public"))
    metadata = get_declarative_base().metadata
    errors = []
    for table in metadata.sorted_tables:
        if table.name not in tables:
            errors.append(f"missing table public.{table.name}")
            continue
        actual_columns = {column["name"]: column for column in inspector.get_columns(table.name, schema="public")}
        for column in table.columns:
            actual = actual_columns.get(column.name)
            if actual is None:
                errors.append(f"missing column {table.name}.{column.name}")
                continue
            if actual["nullable"] != column.nullable or not _type_matches(actual["type"], column.type, connection.dialect):
                errors.append(f"type/nullability mismatch {table.name}.{column.name}")
            if isinstance(column.default, Sequence):
                expected_default = f"nextval('{column.default.name}'::regclass)"
                actual_default = (actual.get("default") or "").replace('"public".', "").replace("public.", "")
                if actual_default != expected_default:
                    errors.append(f"sequence default mismatch {table.name}.{column.name}")
                owned = connection.execute(text("SELECT pg_get_serial_sequence(:table, :column)"), {
                    "table": f"public.{table.name}", "column": column.name,
                }).scalar()
                if owned != f"public.{column.default.name}":
                    errors.append(f"sequence ownership mismatch {table.name}.{column.name}")
            elif column.server_default is not None:
                expected = str(column.server_default.arg)
                actual_default = actual.get("default") or ""
                if actual_default.removesuffix("::text").removesuffix("::jsonb") != expected:
                    errors.append(f"default mismatch {table.name}.{column.name}")
            elif actual.get("default") is not None or actual.get("identity"):
                errors.append(f"unexpected default/identity {table.name}.{column.name}")
        # Extra nullable/additive columns are compatible; extra required columns without a
        # default break this revision's inserts and must fail before trading.
        for name, column in actual_columns.items():
            if name not in table.columns and not column["nullable"] and not column.get("default") and not column.get("identity"):
                errors.append(f"incompatible required column {table.name}.{name}")
        pk = inspector.get_pk_constraint(table.name, schema="public")
        if pk.get("name") != table.primary_key.name or pk.get("constrained_columns") != [c.name for c in table.primary_key]:
            errors.append(f"primary key mismatch {table.name}")
        actual_indexes = {index["name"]: index for index in inspector.get_indexes(table.name, schema="public")}
        for index in table.indexes:
            actual = actual_indexes.get(index.name)
            options = actual.get("dialect_options", {}) if actual else {}
            if (not actual or actual.get("column_names") != [c.name for c in index.columns]
                    or bool(actual.get("unique")) != bool(index.unique)
                    or actual.get("expressions") or actual.get("column_sorting")
                    or options.get("postgresql_where") or options.get("postgresql_include")
                    or options.get("postgresql_using", "btree") != "btree"):
                errors.append(f"index mismatch {index.name}")
        actual_uniques = {item["name"]: item["column_names"] for item in inspector.get_unique_constraints(table.name, schema="public")}
        for constraint in table.constraints:
            if constraint.__class__.__name__ == "UniqueConstraint":
                if actual_uniques.get(constraint.name) != [c.name for c in constraint.columns]:
                    errors.append(f"unique constraint mismatch {constraint.name}")
        actual_fks = {fk["name"]: fk for fk in inspector.get_foreign_keys(table.name, schema="public")}
        for constraint in table.foreign_key_constraints:
            actual = actual_fks.get(constraint.name)
            if (not actual or actual["constrained_columns"] != [c.name for c in constraint.columns]
                    or actual["referred_table"] != constraint.referred_table.name
                    or actual["referred_schema"] not in (None, "public")
                    or actual["referred_columns"] != [element.column.name for element in constraint.elements]
                    or actual.get("options")):
                errors.append(f"foreign key mismatch {constraint.name}")
    invalid = connection.execute(text("""
        SELECT c.relname FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND NOT i.indisvalid
    """)).scalars().all()
    if invalid:
        errors.append("invalid indexes: " + ", ".join(invalid))
    unvalidated = connection.execute(text("""
        SELECT conname FROM pg_constraint c JOIN pg_namespace n ON n.oid = c.connamespace
        WHERE n.nspname = 'public' AND NOT convalidated
    """)).scalars().all()
    if unvalidated:
        errors.append("unvalidated constraints: " + ", ".join(unvalidated))
    triggers = connection.execute(text("""
        SELECT t.tgname, c.relname FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND NOT t.tgisinternal
    """)).all()
    for name, table in triggers:
        if table in metadata.tables:
            errors.append(f"unexpected trigger {table}.{name}")
    if "bot_state" in tables:
        checks = {item["name"]: item["sqltext"] for item in inspector.get_check_constraints("bot_state", schema="public")}
        expected = "desired_state = ANY (ARRAY['stopped'::text, 'running'::text])"
        if checks.get("chk_bot_state_desired_state") != expected:
            errors.append("check constraint mismatch chk_bot_state_desired_state")
    if errors:
        raise PostgreSQLBootstrapError("Incompatible PostgreSQL schema: " + "; ".join(errors))


def bootstrap_postgres(
    engine: Engine, *, auto_migrate: bool, scripts: Optional[Path] = None,
    lock_wait: float = 60, statement_timeout: float = 30, total_timeout: float = 180,
    legacy_cutover: bool = False,
) -> None:
    """Run a batch under one physical session lock, then always validate its schema.

    A deadline watchdog cancels a running command even when a multi-statement file
    contains several statements individually shorter than statement_timeout.
    """
    deadline = time.monotonic() + total_timeout
    current_script = None
    if legacy_cutover and not auto_migrate:
        raise PostgreSQLBootstrapError("Explicit legacy cutover must execute migrations.")
    try:
        with engine.connect() as connection, bootstrap_io_budget(connection, deadline):
            raw = connection.connection
            timer = threading.Timer(max(0, deadline - time.monotonic()), raw.cancel)
            timer.daemon = True
            locked = False
            timer.start()
            try:
                if not legacy_cutover:
                    reject_legacy_schema(connection)
                # End inspector's read transaction before the driver-owned script transactions.
                connection.rollback() if hasattr(connection, "rollback") else raw.rollback()
                if auto_migrate:
                    paths = sorted((scripts or SCRIPTS).glob("*.sql"))
                    if not paths:
                        raise PostgreSQLBootstrapError("No packaged PostgreSQL SQL scripts found.")
                    if legacy_cutover:
                        paths.insert(0, SCRIPTS.parent / "manual_scripts" / "001_normalize_legacy_names.sql")
                    lock_deadline = min(deadline, time.monotonic() + lock_wait)
                    with raw.cursor() as cursor:
                        while True:
                            cursor.execute("SELECT pg_try_advisory_lock(%s)", (MIGRATION_LOCK,))
                            locked = cursor.fetchone()[0]
                            raw.commit()
                            if locked:
                                break
                            if time.monotonic() >= lock_deadline:
                                raise PostgreSQLBootstrapError("PostgreSQL migration lock deadline exceeded.", retryable=True)
                            time.sleep(min(0.1, max(0, lock_deadline - time.monotonic())))
                    # Another process may have completed a cutover while this one waited.
                    if not legacy_cutover:
                        reject_legacy_schema(connection)
                    connection.rollback() if hasattr(connection, "rollback") else raw.rollback()
                    for path in paths:
                        current_script = path.name
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise PostgreSQLBootstrapError("PostgreSQL bootstrap deadline exceeded.", retryable=True)
                        content = path.read_text(encoding="utf-8")
                        with raw.cursor() as cursor:
                            cursor.execute("SET LOCAL search_path = public")
                            cursor.execute("SELECT set_config('statement_timeout', %s, true)",
                                           (str(max(1, int(min(statement_timeout, remaining) * 1000))),))
                            cursor.execute("SET LOCAL lock_timeout = '5s'")
                            cursor.execute(content)
                        if not legacy_cutover:
                            raw.commit()
                        LOGGER.info("Applied PostgreSQL script %s sha256=%s", path.name,
                                    hashlib.sha256(content.encode()).hexdigest())
                validate_schema(connection)
                if time.monotonic() >= deadline:
                    raise PostgreSQLBootstrapError("PostgreSQL bootstrap deadline exceeded.", retryable=True)
                if legacy_cutover:
                    raw.commit()
            finally:
                timer.cancel()
                timer.join()
                # Clear an aborted transaction before unlocking. A broken physical session
                # is invalidated, so it cannot return to the pool retaining the advisory lock.
                try:
                    raw.rollback()
                    if locked:
                        with raw.cursor() as cursor:
                            cursor.execute("SELECT pg_advisory_unlock(%s)", (MIGRATION_LOCK,))
                        raw.commit()
                except Exception:
                    connection.invalidate()
    except PostgreSQLBootstrapError:
        raise
    except Exception as error:
        from sqlalchemy.exc import OperationalError, TimeoutError as PoolTimeout
        code = getattr(error, "pgcode", None) or getattr(getattr(error, "orig", None), "pgcode", None)
        retryable = ((code is not None and (code.startswith(('08', '53')) or code in ('57014', '57P01', '57P02', '57P03', '55P03', '40001', '40P01')))
                     or (code is None and isinstance(error, (OSError, TimeoutError, OperationalError, PoolTimeout))))
        location = f" in {current_script}" if current_script else ""
        raise PostgreSQLBootstrapError(
            f"PostgreSQL bootstrap failed{location} (SQLSTATE {code or 'unavailable'}).", retryable=retryable
        ) from None
