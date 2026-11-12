"""Clean, reviewed financial baselines and streamed recovery fingerprints.

No repair is inferred from a matching quantity alone. Unsettled or unanchored
ledgers retain the existing complete-history recovery path.
"""
import hashlib
import json
import re

from sqlalchemy import and_, exists, func, select
from sqlalchemy.orm import Session

from hummingbot.model.admin_access import AdminAuditEvent
from hummingbot.model.admin_command import AdminCommand
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

BATCH_SIZE = 500
MODE = 'verified_checkpoint'
LEDGER_MODELS = (Order, OrderStatus, TradeFill, RuntimeJournal, SubmissionAttempt, MarketState)


def fill_identity():
    return and_(RuntimeJournal.kind == 'fill', RuntimeJournal.order_id == TradeFill.order_id,
                RuntimeJournal.payload['market'].as_string() == TradeFill.market,
                RuntimeJournal.payload['exchange_trade_id'].as_string() == TradeFill.exchange_trade_id)


def ledger_hash(connection):
    """Bound memory; retain detection of edits/deletes in the reviewed prefix."""
    result = hashlib.sha256()
    for model in LEDGER_MODELS:
        result.update(model.__tablename__.encode() + b'\n')
        statement = select(model).order_by(*model.__table__.primary_key.columns).execution_options(yield_per=BATCH_SIZE)
        with connection.execute(statement) as rows:
            for partition in rows.mappings().partitions(BATCH_SIZE):
                for row in partition:
                    result.update(json.dumps(dict(row), sort_keys=True, separators=(',', ':'), default=str).encode() + b'\n')
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


def snapshot(connection, state, positions, bindings):
    if state.get('config_type') != 'mm-session' or not state.get('config_file_path'):
        return None
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
        if (connection.scalar(select(exists().where(Order.creation_pending.is_(True))))
                or connection.scalar(select(exists().where(SubmissionAttempt.state != 'terminal')))
                or connection.scalar(select(exists().where(RuntimeJournal.applied.is_(False))))):
            return None
        unjournaled = connection.scalar(select(func.count()).select_from(TradeFill).where(
            ~exists(select(RuntimeJournal.id).where(fill_identity()))))
        if unjournaled != anchor['prefix_unjournaled_fills']:
            return None
        work = and_(RuntimeJournal.kind == 'fill', RuntimeJournal.id > anchor['journal_watermark'])
        expected = connection.scalar(select(func.count()).select_from(RuntimeJournal).where(work))
        covered = connection.scalar(select(func.count()).select_from(RuntimeJournal).join(TradeFill, fill_identity()).where(work))
        if expected != covered:
            return None
        # Persisted nonterminal tracker state is not an empty exchange account.
        from hummingbot.core.data_type.in_flight_order import InFlightOrder
        trackers = connection.execute(select(MarketState).limit(101)).mappings().all()
        if len(trackers) > 100:
            return None
        try:
            if any(len(row['saved_state']) > 1000 or any(not InFlightOrder.from_json(value).is_done
                                                         for value in row['saved_state'].values()) for row in trackers):
                return None
        except (ValueError, TypeError, KeyError, ArithmeticError):
            return None
        value = dict(financial_hash=checkpoint.financial_hash, bindings_hash=checkpoint.bindings_hash,
                     watermark=checkpoint.watermark, payload=checkpoint.payload, anchor=anchor,
                     expected_fills=expected, prefix_unjournaled_fills=unjournaled)
    return dict(mode=MODE, state=state, positions=positions, bindings=bindings, orders=[], fills=[], truncated=False,
                checkpoint=value, ledger_hash=ledger_hash(connection))
