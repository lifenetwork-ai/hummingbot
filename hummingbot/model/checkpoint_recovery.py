"""Clean, reviewed financial baselines and streamed recovery fingerprints.

No repair is inferred from a matching quantity alone. Unsettled or unanchored
ledgers retain the existing complete-history recovery path.
"""
import hashlib
import re
import time
from contextlib import contextmanager

from sqlalchemy import Text, and_, cast, exists, func, or_, select
from sqlalchemy.orm import Session

from hummingbot.model.admin_access import AdminAuditEvent
from hummingbot.model.admin_command import AdminCommand
from hummingbot.model.db_migration.postgres.io_deadline import bootstrap_io_budget
from hummingbot.model.market_state import MarketState
from hummingbot.model.order import Order
from hummingbot.model.order_status import OrderStatus
from hummingbot.model.runtime_journal import (
    RuntimeCheckpoint,
    RuntimeJournal,
    SubmissionAttempt,
    binding_hash,
    environment_identity,
    financial_hash,
)
from hummingbot.model.trade_fill import TradeFill

BATCH_SIZE = 4000
MODE = 'verified_checkpoint'
LEDGER_MODELS = (Order, OrderStatus, TradeFill, RuntimeJournal, SubmissionAttempt, MarketState)


def fill_identity():
    return and_(RuntimeJournal.kind == 'fill', RuntimeJournal.order_id == TradeFill.order_id,
                RuntimeJournal.payload['market'].as_string() == TradeFill.market,
                RuntimeJournal.payload['exchange_trade_id'].as_string() == TradeFill.exchange_trade_id)


@contextmanager
def _recovery_read_budget(connection):
    # Only the read-only checkpoint proof gets this budget. A failed SQL read rolls
    # back to the savepoint with the original setting and the caller's locks.
    with bootstrap_io_budget(connection, time.monotonic() + 50), connection.begin_nested():
        timeout = connection.exec_driver_sql('SHOW statement_timeout').scalar_one()
        connection.exec_driver_sql("SET LOCAL statement_timeout = '6s'")
        yield
        connection.execute(select(func.set_config('statement_timeout', timeout, True)))


def ledger_hash(connection):
    with _recovery_read_budget(connection):
        return _stream_ledger_hash(connection)


def _stream_ledger_hash(connection):
    """Stream fixed-size hashes of the complete, primary-key-ordered ledger."""
    result = hashlib.sha256()
    for model in LEDGER_MODELS:
        table = model.__table__
        result.update(table.name.encode() + b'\n')
        # Hash every column without sending whole JSON payloads to Python.
        row_hash = func.sha256(func.convert_to(cast(func.to_jsonb(table.table_valued()), Text), 'UTF8'))
        statement = select(row_hash).select_from(table).order_by(*table.primary_key.columns).execution_options(yield_per=BATCH_SIZE)
        with connection.execute(statement) as rows:
            for value in rows.scalars():
                result.update(value)
    return result.hexdigest()


def reviewed_anchor(connection, state, checkpoint):
    payload = checkpoint.payload or {}
    anchor = payload.get('recovery_anchor')
    if isinstance(anchor, dict):
        row = connection.execute(select(AdminCommand).where(AdminCommand.bot_id == state['bot_id'],
                                                            AdminCommand.id == anchor.get('operation_id'), AdminCommand.action == 'recovery_reset',
                                                            AdminCommand.status == 'succeeded')).mappings().first()
        if (row and (row['result'] or {}).get('stage') == 'recovered'
                and (row['result'].get('verification') or {}).get('verified') is True
                and (row['result'].get('verification') or {}).get('scope') in (MODE, 'bounded_complete_local_history_and_dedicated_account')
                and (row['result'].get('verification') or {}).get('recovery_anchor') == anchor
                and type(anchor.get('journal_watermark')) is int and anchor['journal_watermark'] >= 0
                and type(anchor.get('prefix_unjournaled_fills')) is int and anchor['prefix_unjournaled_fills'] >= 0
                and type(anchor.get('verified_at_ms')) is int):
            return dict(anchor)
        return None
    proof = payload.get('proof_sha256')
    if payload.get('baseline') != 'reviewed_legacy_maintenance' or not isinstance(proof, str) or not re.fullmatch('[a-f0-9]{64}', proof):
        return None
    row = connection.execute(select(AdminAuditEvent).where(AdminAuditEvent.bot_id == state['bot_id'],
                                                           AdminAuditEvent.event_key == 'legacy-baseline:' + proof, AdminAuditEvent.action == 'runtime_baseline_bound',
                                                           AdminAuditEvent.target_id == state['config_file_path'], AdminAuditEvent.outcome == 'succeeded')).mappings().first()
    details = row['details'] if row else {}
    if (not row or row['actor_role'] != 'admin' or row['actor_kind'] != 'maintenance'
            or details.get('proof_sha256') != proof or details.get('rehearsal') is not False
            or type(details.get('verified_fills')) is not int or details['verified_fills'] < 0):
        return None
    # This legacy cutover explicitly marked its journal-free prefix applied.
    # A different prefix size cannot be silently inferred from an audit count.
    return dict(audit_id=row['id'], verified_at_ms=int(row['created_at'].timestamp() * 1000),
                journal_watermark=0, prefix_unjournaled_fills=details['verified_fills'], venue_heads=None)


def unresolved_attempts(connection, *, excluding=()):
    # The recorder durably retires rejected creates as `rejected`, including
    # positive venue-expiry rejections and locally unsent failures. They are
    # resolved only with the corresponding failure row and no fill/venue ID.
    # Unknown states or contradictory evidence still require reconciliation.
    failed_order = exists(select(Order.id).where(
        Order.id == SubmissionAttempt.order_id, Order.creation_pending.is_(False),
        Order.last_status == 'OrderFailure', Order.exchange_order_id.is_(None))).correlate(SubmissionAttempt)
    fills = exists(select(TradeFill.order_id).where(
        TradeFill.order_id == SubmissionAttempt.order_id)).correlate(SubmissionAttempt)
    return bool(connection.scalar(select(exists().where(SubmissionAttempt.order_id.notin_(excluding), or_(
        SubmissionAttempt.state.notin_(('terminal', 'rejected')),
        and_(SubmissionAttempt.state == 'rejected', or_(~failed_order, fills)))))))


def snapshot(connection, state, positions, bindings, *, uncertain_ids=()):
    if state.get('config_type') != 'mm-session' or not state.get('config_file_path'):
        return None
    with _recovery_read_budget(connection):
        return _snapshot(connection, state, positions, bindings, uncertain_ids)


def _snapshot(connection, state, positions, bindings, uncertain_ids):
    with Session(bind=connection) as session:
        checkpoint = session.get(RuntimeCheckpoint, state['config_file_path'])
        if (checkpoint is None or checkpoint.financial_hash != financial_hash(session)
                or checkpoint.bindings_hash != binding_hash(session, state['bot_id'])
                or (checkpoint.payload or {}).get('environment') != environment_identity()):
            return None
        current = sorted((r['connector'], r['environment'], r['account_id']) for r in bindings if r['state'] == 'active')
        previous = sorted((r['connector'], r['environment'], r['account_id']) for r in checkpoint.payload.get('bindings', []))
        if current != previous or not current:
            return None
        anchor = reviewed_anchor(connection, state, checkpoint)
        if anchor is None or not 0 <= anchor['journal_watermark'] <= checkpoint.watermark:
            return None
        uncertain = []
        if uncertain_ids:
            from hummingbot.model.recovery_uncertain import candidates
            uncertain = candidates(connection, state, bindings, checkpoint.payload, anchor, uncertain_ids)
            terminal_orders, excluded = [], uncertain_ids
        else:
            from hummingbot.model.recovery_terminal import candidates
            terminal_orders = candidates(connection, state, bindings, checkpoint.payload)
            if terminal_orders is None or connection.scalar(select(exists().where(Order.creation_pending.is_(True)))):
                return None
            excluded = [row['id'] for row in terminal_orders]
        if unresolved_attempts(connection, excluding=excluded):
            return None
        from hummingbot.model.recovery_pending_fills import candidates as pending_candidates
        pending = pending_candidates(connection, state, positions, bindings, checkpoint, anchor)
        if pending is None or uncertain and pending[0]:
            return None
        # Native fill identities are unique by the trade_fills primary key. A set
        # difference reads the two identity sets once and collapses duplicate journal
        # entries, avoiding repeated probes of the JSON journal on large ledgers.
        unjournaled_keys = select(TradeFill.order_id, TradeFill.market, TradeFill.exchange_trade_id).except_(
            select(RuntimeJournal.order_id, RuntimeJournal.payload['market'].as_string(),
                   RuntimeJournal.payload['exchange_trade_id'].as_string()).where(RuntimeJournal.kind == 'fill'))
        unjournaled = connection.scalar(select(func.count()).select_from(unjournaled_keys.subquery()))
        if unjournaled != anchor['prefix_unjournaled_fills']:
            return None
        work = and_(RuntimeJournal.kind == 'fill', RuntimeJournal.id > anchor['journal_watermark'])
        expected = connection.scalar(select(func.count()).select_from(RuntimeJournal).where(work))
        covered = connection.scalar(select(func.count()).select_from(RuntimeJournal).join(TradeFill, fill_identity()).where(work))
        if expected != covered:
            return None
        value = dict(financial_hash=checkpoint.financial_hash, bindings_hash=checkpoint.bindings_hash,
                     watermark=checkpoint.watermark, payload=checkpoint.payload, anchor=anchor,
                     expected_fills=expected, prefix_unjournaled_fills=unjournaled)
    result = dict(mode=MODE, state=state, positions=positions, bindings=bindings, orders=[], fills=[], truncated=False,
                  checkpoint=value, ledger_hash=_stream_ledger_hash(connection), terminal_orders=terminal_orders)
    if len(uncertain) == 1:
        result['uncertain_order'] = uncertain[0]  # Preserve existing single-order receipts and fingerprints.
    elif uncertain:
        result['uncertain_orders'] = uncertain
    if pending[0]:
        result['pending_fill_transfers'], result['pending_fill_plan'] = pending
    return result
