"""Admin-reviewed recovery receipts and atomic, owner-fenced aggregate repair."""
import hashlib
import hmac
import json
import uuid
from collections import defaultdict
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timedelta

from sqlalchemy import column, delete, exists, func, or_, select, text, tuple_, update, values
from sqlalchemy.exc import IntegrityError

from hummingbot.core.admin_identity import Principal
from hummingbot.core.halt_diagnostics import halt_cause
from hummingbot.core.recovery import (
    MAX_FILLS,
    MAX_ORDERS,
    PROOF_TTL_SECONDS,
    RecoveryBlocked,
    decimal,
    fingerprint,
    recovery_blockers,
    supports_halted_order_review,
    uncertain_order_ids,
)
from hummingbot.core.recovery_pending_fills import fill_ids
from hummingbot.model.admin_access import AdminAccessStore
from hummingbot.model.admin_command import AdminCommand
from hummingbot.model.bot_state import BotState
from hummingbot.model.bot_state_store import ControlConflict
from hummingbot.model.exchange_credential import ExchangeCredential
from hummingbot.model.order import Order
from hummingbot.model.order_status import OrderStatus
from hummingbot.model.position import Position
from hummingbot.model.recovery_proof import RecoveryProofFill
from hummingbot.model.trade_fill import TradeFill

ACTIONS = ('recovery_check', 'recovery_reset')


def _conflict(code):
    raise ControlConflict(code, code=code)


class RecoveryStore:
    def __init__(self, store):
        self.state, self.engine, self.bot_id = store, store.engine, store.bot_id
        self.access = AdminAccessStore(self.engine, self.bot_id)

    def _state(self, connection, *, lock=False):
        statement = select(BotState, func.clock_timestamp().label('database_now')).where(BotState.bot_id == self.bot_id)
        return dict(connection.execute(statement.with_for_update() if lock else statement).mappings().one())

    def snapshot(self, connection=None, *, uncertain_ids=()):
        if connection is None:
            with self.engine.connect().execution_options(isolation_level='REPEATABLE READ') as connection, connection.begin():
                return self.snapshot(connection, uncertain_ids=uncertain_ids)
        state = self._state(connection)
        from hummingbot.model.checkpoint_recovery import snapshot as checkpoint_snapshot
        positions = [dict(row) for row in connection.execute(select(Position).order_by(Position.id).limit(101)).mappings()]
        bindings = [dict(row) for row in connection.execute(select(
            ExchangeCredential.exchange, ExchangeCredential.connector, ExchangeCredential.environment,
            ExchangeCredential.account_id, ExchangeCredential.version, ExchangeCredential.state).where(
                ExchangeCredential.bot_id == self.bot_id).order_by(ExchangeCredential.exchange, ExchangeCredential.connector, ExchangeCredential.environment)).mappings()]
        if len(positions) <= 100:
            current = checkpoint_snapshot(connection, state, positions, bindings, uncertain_ids=uncertain_ids)
            if current is not None:
                return current
        if uncertain_ids:
            raise RecoveryBlocked('recovery_uncertain_order_scope')
        orders = [dict(row) for row in connection.execute(select(Order).order_by(Order.id).limit(MAX_ORDERS + 1)).mappings()]
        sides = defaultdict(set)
        for identity, status in connection.execute(select(OrderStatus.order_id, OrderStatus.status).where(
                OrderStatus.order_id.in_([row['id'] for row in orders]),
                OrderStatus.status.in_(('BuyOrderCreated', 'SellOrderCreated', 'BuyOrderCompleted', 'SellOrderCompleted'))).distinct()):
            sides[identity].add('BUY' if status.startswith('Buy') else 'SELL')
        for row in orders:
            row['side'] = next(iter(sides[row['id']])) if len(sides[row['id']]) == 1 else None
            row['side_ambiguous'] = len(sides[row['id']]) > 1
        fills = [dict(row) for row in connection.execute(select(TradeFill).order_by(
            TradeFill.timestamp, TradeFill.exchange_trade_id, TradeFill.order_id).limit(MAX_FILLS + 1)).mappings()]
        return dict(state=state, orders=orders, fills=fills, positions=positions, bindings=bindings,
                    pending_intents=bool(connection.scalar(select(exists().where(Order.creation_pending.is_(True))))),
                    truncated=len(orders) > MAX_ORDERS or len(fills) > MAX_FILLS or len(positions) > 100)

    def operation_snapshot(self, payload, connection=None):
        identities = uncertain_order_ids(payload)
        return self.snapshot(connection, uncertain_ids=identities) if identities else self.snapshot(connection)

    def checkpoint_fill_rows(self, keys, watermark, *, pending_ids=()):
        from hummingbot.model.checkpoint_recovery import fill_identity
        from hummingbot.model.recovery_pending_fills import MAX_PENDING_FILLS
        from hummingbot.model.runtime_journal import RuntimeJournal
        if (not keys or len(keys) > 100 or type(watermark) is not int or watermark < 0
                or len(pending_ids) > MAX_PENDING_FILLS or any(type(i) is not int or i <= watermark for i in pending_ids)):
            raise ValueError('Invalid bounded fill proof')
        # Give PostgreSQL the page as a relation so it can join the requested
        # identities before reading the journal. Duplicate keys still match once.
        requested = values(column('market', TradeFill.market.type),
                           column('venue_order_id', Order.exchange_order_id.type),
                           column('trade_id', TradeFill.exchange_trade_id.type), name='requested_fills').data(
            list(dict.fromkeys(tuple(key) for key in keys)))
        columns = ([column.label('fill_' + column.name) for column in TradeFill.__table__.columns]
                   + [column.label('order_' + column.name) for column in Order.__table__.columns]
                   + [RuntimeJournal.payload.label('journal'), RuntimeJournal.id.label('journal_id')])
        statement = (select(*columns).select_from(TradeFill).join(Order, Order.id == TradeFill.order_id)
                     .join(requested, tuple_(TradeFill.market, Order.exchange_order_id, TradeFill.exchange_trade_id)
                           == tuple_(requested.c.market, requested.c.venue_order_id, requested.c.trade_id))
                     .join(RuntimeJournal, fill_identity())
                     .where(RuntimeJournal.id > watermark,
                            or_(RuntimeJournal.applied.is_(True), RuntimeJournal.id.in_(pending_ids))))
        with self.engine.connect() as connection:
            rows = connection.execute(statement).mappings().all()
        return [dict(fill={c.name: row['fill_' + c.name] for c in TradeFill.__table__.columns},
                     order={c.name: row['order_' + c.name] for c in Order.__table__.columns},
                     journal=row['journal'], journal_id=row['journal_id']) for row in rows]

    @staticmethod
    def _lock_checkpoint_proof(connection, snapshot):
        if snapshot.get('mode') == 'verified_checkpoint':
            # Called on the recovery DB worker, while the old core is drained.
            # Readers remain available; no financial write can race final proof.
            from hummingbot.model.checkpoint_recovery import LEDGER_MODELS
            from hummingbot.model.runtime_journal import RuntimeCheckpoint
            models = (*LEDGER_MODELS, Position, RuntimeCheckpoint, ExchangeCredential)
            if snapshot.get('pending_fill_transfers'):
                from hummingbot.model.executors import Executors
                models += (Executors,)
            tables = ', '.join(connection.dialect.identifier_preparer.format_table(model.__table__)
                               for model in models)
            connection.execute(text('LOCK TABLE ' + tables + ' IN SHARE MODE'))

    def get(self, operation_id, connection=None):
        if connection is None:
            with self.engine.connect() as connection:
                return self.get(operation_id, connection)
        row = connection.execute(select(AdminCommand).where(AdminCommand.bot_id == self.bot_id,
                                                            AdminCommand.id == operation_id, AdminCommand.action.in_(ACTIONS))).mappings().first()
        return dict(row) if row else None

    def latest(self, *, uncertain_ids=()):
        identity = AdminCommand.payload['uncertain_order_id'].as_string()
        batch = AdminCommand.payload['uncertain_order_ids']
        if len(uncertain_ids) > 1:
            scope = batch == list(uncertain_ids)
        elif uncertain_ids:
            scope = identity == uncertain_ids[0]
        else:
            scope = identity.is_(None) & batch.as_string().is_(None)
        with self.engine.connect() as connection:
            row = connection.execute(select(AdminCommand).where(AdminCommand.bot_id == self.bot_id,
                                                                AdminCommand.action.in_(ACTIONS), scope).order_by(
                AdminCommand.created_at.desc(), AdminCommand.id.desc()).limit(1)).mappings().first()
            return dict(row) if row else None

    def uncertain_orders_pending(self, identities):
        from hummingbot.model.recovery_uncertain import PENDING_STATUS
        from hummingbot.model.runtime_journal import SubmissionAttempt
        with self.engine.connect() as connection:
            rows = connection.execute(select(Order.id, Order.market, Order.last_status, Order.exchange_order_id,
                                             SubmissionAttempt.state).outerjoin(SubmissionAttempt, SubmissionAttempt.order_id == Order.id)
                                      .where(Order.creation_pending.is_(True)).limit(len(identities) + 1)).all()
        return {row.id for row in rows} == set(identities) and all(
            row.market == 'okx' and row.last_status in PENDING_STATUS.values()
            and row.exchange_order_id is None and row.state == 'uncertain' for row in rows)

    @staticmethod
    def _same_owner(state, private):
        return state['owner_uuid'] == private['owner_uuid'] and state['owner_generation'] == private['owner_generation']

    def _checked_preview(self, connection, payload, state, *, current_snapshot=None, actor=None):
        check = self.get(payload['check_id'], connection)
        if (not check or check['action'] != 'recovery_check' or check['status'] != 'succeeded'
                or not (check['result'] or {}).get('can_recover')):
            _conflict('recovery_check_required')
        report = check['result']
        token = payload.get('confirmation_token')
        if (not isinstance(token, str) or not token.isascii()
                or not hmac.compare_digest(token, str(report['confirmation_token']))):
            _conflict('recovery_confirmation_invalid')
        if datetime.fromisoformat(report['expires_at']) <= state['database_now']:
            _conflict('recovery_proof_expired')
        if not self._same_owner(state, report['_private']):
            _conflict('recovery_owner_changed')
        identities = uncertain_order_ids(payload)
        if uncertain_order_ids(check['payload']) != identities:
            _conflict('recovery_state_changed')
        if identities and report['_private']['actor'] != asdict(actor):
            _conflict('recovery_actor_changed')
        review = (report.get('proposed_order_changes') or report.get('proposed_uncertain_order') or report.get('proposed_uncertain_orders')
                  or (report.get('verification') or {}).get('pending_fill_ids'))
        if review and payload.get('confirm_order_changes') is not True:
            _conflict('recovery_order_review_required')
        current_snapshot = self.operation_snapshot(payload, connection) if current_snapshot is None else current_snapshot
        if fingerprint({**current_snapshot, 'state': state}) != report['_private']['fingerprint']:
            _conflict('recovery_state_changed')
        return check

    def request(self, action, key, payload, principal, request_id):
        if action not in ACTIONS or not isinstance(key, str) or not 1 <= len(key) <= 128 or any(not 33 <= ord(c) <= 126 for c in key):
            raise ValueError('Invalid recovery request or Idempotency-Key')
        if type(payload.get('expected_version')) is not int or payload['expected_version'] < 0:
            raise ValueError('Invalid expected control version')
        identities = uncertain_order_ids(payload)
        if len(identities) > 1:
            payload = {**payload, 'uncertain_order_ids': list(identities)}
        digest = hashlib.sha256(json.dumps(dict(action=action, payload=payload), sort_keys=True).encode()).hexdigest()
        with self.engine.begin() as connection:
            actor = self.access.authorize(connection, principal, 'admin')
            # A request only queues work. Stream the large checkpoint ledger
            # before locking the publication/authority row. Reset finalization
            # independently revalidates under financial-table locks; accepting
            # a request can never certify a concurrent financial change.
            preview = self.get(payload.get('check_id', ''), connection) if action == 'recovery_reset' else None
            prepared, preparation_error = None, None
            if (preview and uncertain_order_ids(preview['payload']) == identities
                    and ((preview.get('result') or {}).get('verification') or {}).get('scope') == 'verified_checkpoint'):
                try:
                    prepared = self.operation_snapshot(payload, connection)
                except RecoveryBlocked as error:
                    preparation_error = error.code
            state = self._state(connection, lock=True)
            prior = connection.execute(select(AdminCommand).where(AdminCommand.bot_id == self.bot_id,
                                                                  AdminCommand.idempotency_key == key)).mappings().first()
            if prior:
                if prior['request_hash'] != digest:
                    _conflict('idempotency_key_reused')
                return dict(prior)
            if state['control_version'] != payload['expected_version']:
                _conflict('control_version_changed')
            if not isinstance(state['recovery_state'], dict):
                _conflict('recovery_checkpoint_invalid')
            uncertain = bool(identities)
            blockers = recovery_blockers(state, uncertain_ids=identities)
            if action == 'recovery_check' and not uncertain:
                blockers = [code for code in blockers if code in ('recovery_stop_required', 'recovery_stop_incomplete',
                                                                  'recovery_not_halted', 'credential_operation_in_progress', 'recovery_pending_mutations')]
            if blockers:
                _conflict(blockers[0])
            if (not state['owner_uuid'] or state['heartbeat'] is None
                    or not 0 <= (state['database_now'] - state['heartbeat']).total_seconds() <= 5):
                _conflict('owner_unavailable')
            if (state['recovery_state'] or {}).get('recovery_operation'):
                _conflict('recovery_operation_in_progress')
            if action == 'recovery_reset':
                if preparation_error:
                    _conflict(preparation_error)
                self._checked_preview(connection, payload, state, current_snapshot=prepared, actor=actor)
            identity = str(uuid.uuid4())
            private = dict(actor=asdict(actor), request_id=request_id,
                           owner_uuid=state['owner_uuid'], owner_generation=state['owner_generation'])
            values = dict(id=identity, bot_id=self.bot_id, idempotency_key=key, request_hash=digest, action=action,
                          expected_version=state['control_version'], effective_version=state['control_version'], payload=payload,
                          status='accepted', result=dict(stage='queued', can_recover=False, blockers=[], _private=private),
                          created_at=state['database_now'], updated_at=state['database_now'])
            row = dict(connection.execute(AdminCommand.__table__.insert().values(**values).returning(*AdminCommand.__table__.c)).mappings().one())
            recovery = deepcopy(state['recovery_state'])
            recovery['recovery_operation'] = dict(id=identity, action=action)
            connection.execute(update(BotState).where(BotState.bot_id == self.bot_id).values(recovery_state=recovery))
            self.access.audit(connection, actor=actor, action=action + '_requested', outcome='accepted',
                              operation_id=identity, request_id=request_id)
            return row

    def _authorize_actor(self, connection, row):
        original = Principal(**row['result']['_private']['actor'])
        current = self.access.authorize(connection, original, 'admin')
        if original.kind == 'google' and original.account_version != current.account_version:
            _conflict('recovery_actor_changed')
        return current

    def claim(self, owner):
        owner.check_owner()
        with self.engine.begin() as connection:
            state = self._state(connection, lock=True)
            operation = (state['recovery_state'] or {}).get('recovery_operation') or {}
            row = self.get(operation.get('id', ''), connection)
            if not row or row['status'] not in ('accepted', 'applying'):
                return None
            if state['owner_uuid'] != owner.owner_uuid or state['owner_generation'] != owner.generation:
                _conflict('recovery_owner_changed')
            if not self._same_owner(state, row['result']['_private']):
                return self._fail(connection, state, row, 'recovery_owner_changed')
            self._authorize_actor(connection, row)
            retry_at = (row['result'].get('_private') or {}).get('retry_at')
            if retry_at and datetime.fromisoformat(retry_at) > state['database_now']:
                return None
            result = deepcopy(row['result']); result['stage'] = 'checking_exchange' if row['action'] == 'recovery_check' else 'revalidating'
            return dict(connection.execute(update(AdminCommand).where(AdminCommand.id == row['id']).values(
                status='applying', result=result, updated_at=state['database_now']).returning(*AdminCommand.__table__.c)).mappings().one())

    def save_proof_progress(self, owner, row, snapshot, before, after, entries):
        """One atomic page: dedup identities and advance its private cursor."""
        from hummingbot.core.segmented_recovery import public_progress, validate_progress
        if (len(entries) > 100 or len(json.dumps(after, allow_nan=False).encode()) > 16384
                or after.get('verified') is not False or after['fingerprint'] != fingerprint(snapshot)
                or after['seq'] != (before['seq'] + 1 if before else 0)):
            _conflict('recovery_state_changed')
        owner.check_owner()
        try:
            with self.engine.begin() as connection:
                state, actor = self._operation(connection, owner, row, checking=row['action'] == 'recovery_check')
                if fingerprint({**snapshot, 'state': state}) != after['fingerprint']:
                    _conflict('recovery_state_changed')
                validate_progress(after, {**snapshot, 'state': state})
                current = self.get(row['id'], connection)
                report = deepcopy(current['result'])
                existing = report['_private'].get('proof')
                if existing == after:
                    return True  # An acknowledged-late commit of this exact page.
                if existing != before:
                    _conflict('recovery_state_changed')
                if entries:
                    if after['scan'] != before['scan'] or after['scan'] not in (0, 1):
                        _conflict('recovery_state_changed')
                    connection.execute(RecoveryProofFill.__table__.insert(), [dict(command_id=row['id'], scan=after['scan'],
                                                                                   connector=market, venue_order_id=order, trade_id=trade, bill_id=bill)
                                                                              for market, order, trade, bill in entries])
                report['_private']['proof'] = after
                for key in ('retry_at', 'read_retries'):
                    report['_private'].pop(key, None)
                report.update(stage='checking_exchange_segment', can_recover=False,
                              confirmation_token=None, expires_at=None, verification=public_progress(after))
                connection.execute(update(AdminCommand).where(AdminCommand.id == row['id']).values(
                    result=report, updated_at=state['database_now']))
                owner.check_owner()
            return True
        except IntegrityError:
            _conflict('recovery_unrecorded_or_duplicate_venue_fill')

    def defer_proof(self, owner, row, code):
        """Retry a GET-only proof from its last durable page, never grant reset."""
        owner.check_owner()
        with self.engine.begin() as connection:
            state, actor = self._operation(connection, owner, row, checking=row['action'] == 'recovery_check')
            current = self.get(row['id'], connection)
            result = deepcopy(current['result'])
            private = result['_private']
            attempts = private.get('read_retries', 0) + 1
            if attempts > 5 or (state['database_now'] - row['created_at']).total_seconds() > 1800:
                self._fail(connection, state, current, code)
            else:
                private.update(read_retries=attempts,
                               retry_at=(state['database_now'] + timedelta(seconds=min(30, 2 ** attempts))).isoformat())
                result.update(stage='waiting_for_exchange', can_recover=False, confirmation_token=None, expires_at=None)
                result['verification'] = {**result.get('verification', {}), 'verified': False,
                                          'retry_count': attempts, 'next_retry_at': private['retry_at']}
                connection.execute(update(AdminCommand).where(AdminCommand.id == row['id']).values(
                    result=result, updated_at=state['database_now']))
            owner.check_owner()
        return True

    def _operation(self, connection, owner, row, *, checking=False):
        actor = self._authorize_actor(connection, row)
        state = self._state(connection, lock=True)
        current = self.get(row['id'], connection)
        if (not current or current['status'] != 'applying' or not self._same_owner(state, row['result']['_private'])
                or state['owner_uuid'] != owner.owner_uuid or state['owner_generation'] != owner.generation
                or (state['recovery_state'].get('recovery_operation') or {}).get('id') != row['id']):
            _conflict('recovery_owner_changed')
        if state['control_version'] != row['expected_version']:
            _conflict('control_version_changed')
        identities = uncertain_order_ids(row['payload'])
        blockers = recovery_blockers(state, uncertain_ids=identities)
        if checking and not identities:
            blockers = [code for code in blockers if code in ('recovery_stop_required', 'recovery_stop_incomplete',
                                                              'recovery_not_halted', 'credential_operation_in_progress', 'recovery_pending_mutations')]
        if blockers:
            _conflict(blockers[0])
        return state, actor

    def _complete_segmented_proof(self, connection, row, snapshot, verification):
        from hummingbot.core.segmented_recovery import validate_progress
        private = (self.get(row['id'], connection)['result'] or {}).get('_private') or {}
        proof = private.get('proof')
        pending = fill_ids(snapshot)
        if pending != verification.get('pending_fill_ids', []):
            _conflict('recovery_pending_fills_unproven')
        if proof is None and not verification.get('segmented') and not snapshot.get('terminal_orders') and not pending:
            return  # Existing bounded complete-history / one-shot verifier.
        if proof:
            validate_progress(proof, {**snapshot, 'state': self._state(connection)})
        expected = (snapshot.get('checkpoint') or {}).get('expected_fills')
        if (not proof or proof.get('scan') != 2 or proof.get('count') != expected
                or proof.get('fingerprint') != fingerprint(snapshot)
                or (proof.get('first') or {}).get('count') != expected
                or proof.get('first') != dict(digest=proof.get('digest'), heads=proof.get('heads'), count=expected)
                or proof.get('connector_index') != 0 or proof.get('cursor') is not None
                or proof.get('heads') != verification.get('venue_heads')
                or proof.get('digest') != verification.get('proof_digest')
                or verification.get('fills_checked') != expected or not verification.get('segmented')):
            _conflict('recovery_check_unavailable')
        counts = dict(connection.execute(select(RecoveryProofFill.scan, func.count()).where(
            RecoveryProofFill.command_id == row['id']).group_by(RecoveryProofFill.scan)).all())
        if set(counts) - {0, 1} or any(counts.get(scan, 0) != expected for scan in (0, 1)):
            _conflict('recovery_check_unavailable')
        if pending:
            keys = {(item['exchange_order_id'], fill['trade_id']) for item in snapshot['pending_fill_transfers']
                    for fill in item['fills']}
            proved = connection.execute(select(RecoveryProofFill.scan, RecoveryProofFill.venue_order_id, RecoveryProofFill.trade_id)
                                        .where(RecoveryProofFill.command_id == row['id'], RecoveryProofFill.connector == 'okx',
                                               tuple_(RecoveryProofFill.venue_order_id, RecoveryProofFill.trade_id).in_(keys))).all()
            if set(proved) != {(scan, order_id, trade_id) for scan in (0, 1) for order_id, trade_id in keys}:
                _conflict('recovery_pending_fills_unproven')

    def finish_check(self, owner, row, snapshot, plan, verification, *, blockers):
        owner.check_owner()
        with self.engine.begin() as connection:
            self._lock_checkpoint_proof(connection, snapshot)
            # SHARE locks keep the financial snapshot stable while its hash
            # is streamed. Status publication remains free to update BotState.
            payload = row['payload']
            prepared = self.operation_snapshot(payload, connection) if snapshot.get('mode') == 'verified_checkpoint' else None
            state, actor = self._operation(connection, owner, row, checking=True)
            if not blockers and (not verification.get('verified')
                                 or snapshot.get('mode') == 'verified_checkpoint' and
                                 (plan != snapshot.get('pending_fill_plan', []) or verification.get('scope') != 'verified_checkpoint')):
                _conflict('recovery_check_unavailable')
            current = self.operation_snapshot(payload, connection) if prepared is None else prepared
            if fingerprint({**current, 'state': state}) != fingerprint(snapshot):
                _conflict('recovery_state_changed')
            if not blockers:
                self._complete_segmented_proof(connection, row, snapshot, verification)
                from hummingbot.core.recovery_terminal import validate_changes
                validate_changes(snapshot, verification.get('terminal_order_changes', []))
                self._verified_uncertain_change(snapshot, verification)
            result = dict(stage='blocked' if blockers else 'review_required', can_recover=not blockers,
                          blockers=blockers, proposed_changes=plan, verification=verification,
                          proposed_order_changes=verification.get('terminal_order_changes', []) if not blockers else [],
                          proposed_uncertain_order=verification.get('uncertain_order_change') if not blockers else None,
                          proposed_uncertain_orders=verification.get('uncertain_order_changes', []) if not blockers else [],
                          confirmation_token=None if blockers else uuid.uuid4().hex,
                          expires_at=(state['database_now'] + timedelta(seconds=PROOF_TTL_SECONDS)).isoformat(),
                          _private={**row['result']['_private'], 'fingerprint': fingerprint(snapshot)})
            self._finish(connection, state, row, result, actor)
            owner.check_owner()
            return True

    def finish_reset(self, owner, row, snapshot, plan, verification):
        owner.check_owner()
        with self.engine.begin() as connection:
            self._lock_checkpoint_proof(connection, snapshot)
            payload = row['payload']
            prepared = self.operation_snapshot(payload, connection) if snapshot.get('mode') == 'verified_checkpoint' else None
            state, actor = self._operation(connection, owner, row)
            check = self._checked_preview(connection, payload, state, current_snapshot=prepared, actor=actor)
            current = self.operation_snapshot(payload, connection) if prepared is None else prepared
            if (not verification.get('verified') or fingerprint({**current, 'state': state}) != fingerprint(snapshot)
                    or plan != check['result']['proposed_changes']
                    or fill_ids(snapshot) != (check['result'].get('verification') or {}).get('pending_fill_ids', [])):
                _conflict('recovery_state_changed')
            self._complete_segmented_proof(connection, row, snapshot, verification)
            if snapshot.get('uncertain_order') or snapshot.get('uncertain_orders'):
                self._finish_uncertain_reset(connection, state, row, snapshot, verification, check, actor)
                owner.check_owner()
                return True
            from hummingbot.model.recovery_terminal import apply_changes
            order_changes = verification.get('terminal_order_changes', [])
            if order_changes != check['result'].get('proposed_order_changes', []):
                _conflict('recovery_state_changed')
            apply_changes(connection, snapshot, order_changes)
            for change in order_changes:
                self.access.audit(connection, actor=actor, action='recovery_order_repaired', outcome='succeeded',
                                  operation_id=row['id'], request_id=row['result']['_private']['request_id'],
                                  target_id=change['id'], connector=change['connector'], exchange='okx',
                                  environment=next(r['environment'] for r in snapshot['bindings']
                                                   if r['connector'] == change['connector'] and r['state'] == 'active'),
                                  reason_code='verified_native_cancellation',
                                  details=dict(order_id=change['id'], pair=change['pair'], amount=change['amount'],
                                               stage='terminal_metadata_repaired'))
            for change in plan:
                conditions = [Position.id == change['id'], Position.controller_id == change['controller_id'],
                              Position.connector_name == change['connector'], Position.trading_pair == change['pair']]
                for field, before in change['before'].items():
                    conditions.append(getattr(Position, field) == (before if field == 'side' else decimal(before)))
                values = {key: value if key == 'side' else decimal(value) for key, value in change['after'].items()}
                if connection.execute(update(Position).where(*conditions).values(**values)).rowcount != 1:
                    _conflict('recovery_state_changed')
                self.access.audit(connection, actor=actor, action='recovery_position_repaired', outcome='succeeded',
                                  operation_id=row['id'], request_id=row['result']['_private']['request_id'],
                                  details=dict(position_id=change['id'], amount=change['after']['amount'], pair=change['pair']))
            for item in snapshot.get('pending_fill_transfers', []):
                self.access.audit(connection, actor=actor, action='recovery_pending_fill_applied', outcome='succeeded',
                                  operation_id=row['id'], request_id=row['result']['_private']['request_id'],
                                  target_id=item['id'], connector='okx', exchange='okx',
                                  environment=snapshot['checkpoint']['payload']['environment']['mode'],
                                  reason_code='verified_native_fill',
                                  details=dict(pair=item['pair'], order_id=item['id'], stage='pending_fills_applied'))
            # This is an operator-confirmed, complete venue/financial proof. It
            # may establish the first incremental baseline; ordinary Start may
            # never certify a pre-existing ledger in its place.
            from sqlalchemy.orm import Session
            if state['config_type'] == 'mm-session' and state['config_file_path']:
                with Session(bind=connection) as session:
                    if snapshot.get('mode') == 'verified_checkpoint':
                        if plan != snapshot.get('pending_fill_plan', []) or verification.get('scope') != 'verified_checkpoint':
                            _conflict('recovery_state_changed')
                        from hummingbot.model.recovery_pending_fills import apply as apply_pending_fills
                        from hummingbot.model.runtime_journal import RuntimeCheckpoint
                        checkpoint = session.get(RuntimeCheckpoint, state['config_file_path'])
                        apply_pending_fills(session, checkpoint, snapshot, row['id'])
                        checkpoint.payload = {**checkpoint.payload, 'recovery_anchor': dict(
                            operation_id=row['id'], journal_watermark=checkpoint.watermark,
                            prefix_unjournaled_fills=snapshot['checkpoint']['prefix_unjournaled_fills'],
                            verified_at_ms=int(snapshot['state']['database_now'].timestamp() * 1000),
                            venue_heads=verification['venue_heads'])}
                        session.flush()
                        anchor = checkpoint.payload['recovery_anchor']
                    else:
                        anchor = self._bind_runtime_baseline(session, state, row, snapshot, verification=verification)
                    verification = {**verification, 'recovery_anchor': anchor}
            recovered_cause = halt_cause(state['recovery_state'])
            recovery = deepcopy(state['recovery_state'])
            for key in ('halt', 'halt_kind', 'halt_diagnostic', 'reason', 'shutdown_incomplete', 'automatic_recovery'):
                recovery.pop(key, None)
            recovery['runtime_active'] = False
            state['recovery_state'] = recovery
            result = dict(stage='recovered', can_recover=False, blockers=[], repaired_positions=plan,
                          repaired_orders=order_changes,
                          verification=verification, trading_state='stopped', halt_cause=recovered_cause,
                          _private=row['result']['_private'])
            self._finish(connection, state, row, result, actor, control_version=state['control_version'] + 1)
            owner.check_owner()  # Fresh physical authority before committing all writes together.
            return True

    @staticmethod
    def _verified_uncertain_change(snapshot, verification):
        single = snapshot.get('uncertain_order')
        candidates = snapshot.get('uncertain_orders') or ([single] if single else [])
        actual_single = verification.get('uncertain_order_change')
        actual_batch = verification.get('uncertain_order_changes', [])
        if candidates:
            from hummingbot.model.recovery_uncertain import change_for
            expected = [change_for(candidate) for candidate in candidates]
            same_scope = actual_single == expected[0] and not actual_batch if single else actual_single is None and actual_batch == expected
            if not same_scope or not verification.get('segmented'):
                _conflict('recovery_uncertain_order_unproven')
            return candidates, expected
        if actual_single is not None or actual_batch:
            _conflict('recovery_uncertain_order_scope')

    def _finish_uncertain_reset(self, connection, state, row, snapshot, verification, check, actor):
        from hummingbot.model.recovery_uncertain import apply_change
        candidates, changes = self._verified_uncertain_change(snapshot, verification)
        preview = check['result']
        if changes != (preview.get('proposed_uncertain_orders') or [preview.get('proposed_uncertain_order')]):
            _conflict('recovery_state_changed')
        timestamp = int(state['database_now'].timestamp() * 1000)
        environment = next(r['environment'] for r in snapshot['bindings'] if r['connector'] == 'okx' and r['state'] == 'active')
        halted_order = supports_halted_order_review(state, uncertain_order_ids(row['payload']))
        for candidate, change in zip(candidates, changes):
            apply_change(connection, candidate, timestamp)
            self.access.audit(connection, actor=actor, action='recovery_order_repaired', outcome='succeeded',
                              operation_id=row['id'], request_id=row['result']['_private']['request_id'],
                              target_id=change['id'], connector='okx', exchange='okx', environment=environment,
                              reason_code='operator_reviewed_transport_absence' if halted_order else 'operator_reviewed_busy_absence',
                              details=dict(order_id=change['id'], pair=change['pair'], amount=change['amount'],
                                           stage='uncertain_submission_retired'))
        recovery = deepcopy(state['recovery_state'])
        recovery.pop('automatic_recovery', None)
        result = dict(stage='recovered', can_recover=False, blockers=[],
                      **({'repaired_uncertain_order': changes[0]} if len(changes) == 1 else {'repaired_uncertain_orders': changes}),
                      verification=verification, trading_state='stopped', _private=row['result']['_private'])
        if halted_order:
            result['halt_cause'] = halt_cause(state['recovery_state'])
            for key in ('halt', 'halt_kind', 'halt_diagnostic', 'reason', 'shutdown_incomplete'):
                recovery.pop(key, None)
            recovery['runtime_active'] = False
        state['recovery_state'] = recovery
        self._finish(connection, state, row, result, actor, control_version=state['control_version'] + 1)

    def _bind_runtime_baseline(self, session, state, row, snapshot, *, verification=None):
        from hummingbot.model.runtime_journal import (
            RuntimeCheckpoint,
            RuntimeJournal,
            SubmissionAttempt,
            binding_hash,
            bindings,
            environment_identity,
            financial_hash,
        )
        checkpoint = session.get(RuntimeCheckpoint, state['config_file_path'])
        from sqlalchemy import exists

        from hummingbot.model.checkpoint_recovery import fill_identity
        unjournaled = session.scalar(select(func.count()).select_from(TradeFill).where(
            ~exists(select(RuntimeJournal.id).where(fill_identity()))))
        watermark = session.query(func.max(RuntimeJournal.id)).scalar() or 0
        values = dict(financial_hash=financial_hash(session),
                      bindings_hash=binding_hash(session, self.bot_id),
                      watermark=watermark,
                      payload={'baseline': 'reviewed_recovery', 'operation_id': row['id'], 'environment': environment_identity(),
                               'bindings': bindings(session, self.bot_id),
                               'proof_fingerprint': fingerprint(snapshot),
                               'recovery_anchor': dict(operation_id=row['id'], journal_watermark=watermark,
                                                       prefix_unjournaled_fills=unjournaled,
                                                       verified_at_ms=int(snapshot['state']['database_now'].timestamp() * 1000),
                                                       venue_heads=(verification or {}).get('venue_heads'))})
        if checkpoint is None:
            session.add(RuntimeCheckpoint(config_file_path=state['config_file_path'], **values))
        else:
            for name, value in values.items():
                setattr(checkpoint, name, value)
        session.query(RuntimeJournal).filter(RuntimeJournal.kind == 'fill').update(
            {RuntimeJournal.applied: True}, synchronize_session=False)
        session.query(SubmissionAttempt).update({SubmissionAttempt.state: 'terminal'}, synchronize_session=False)
        session.query(Order).update({Order.position_applied: True}, synchronize_session=False)
        session.flush()
        return values['payload']['recovery_anchor']

    def _finish(self, connection, state, row, result, actor, *, control_version=None):
        result = deepcopy(result)
        for key in ('proof', 'read_retries', 'retry_at'):
            result.get('_private', {}).pop(key, None)
        recovery = deepcopy(state['recovery_state'])
        recovery.pop('recovery_operation', None)
        values = dict(recovery_state=recovery)
        if control_version is not None:
            values.update(control_version=control_version, desired_state='stopped')
        connection.execute(update(BotState).where(BotState.bot_id == self.bot_id).values(**values))
        connection.execute(update(AdminCommand).where(AdminCommand.id == row['id']).values(
            status='succeeded', result=result, updated_at=state['database_now']))
        connection.execute(delete(RecoveryProofFill).where(RecoveryProofFill.command_id == row['id']))
        self.access.audit(connection, actor=actor, action=row['action'] + '_completed',
                          outcome='blocked' if result.get('blockers') else 'succeeded', operation_id=row['id'],
                          reason_code=(result.get('halt_cause') or {}).get('reason_code'),
                          request_id=row['result']['_private']['request_id'])

    def _fail(self, connection, state, row, code):
        recovery = deepcopy(state['recovery_state'])
        if (recovery.get('recovery_operation') or {}).get('id') == row['id']:
            recovery.pop('recovery_operation', None)
            connection.execute(update(BotState).where(BotState.bot_id == self.bot_id).values(recovery_state=recovery))
        result = {**row['result'], 'stage': 'blocked', 'can_recover': False, 'blockers': [code]}
        result = deepcopy(result)
        for key in ('proof', 'read_retries', 'retry_at'):
            result.get('_private', {}).pop(key, None)
        connection.execute(delete(RecoveryProofFill).where(RecoveryProofFill.command_id == row['id']))
        connection.execute(update(AdminCommand).where(AdminCommand.id == row['id']).values(
            status='failed', error=code, result=result, updated_at=state['database_now']))
        self.access.audit(connection, actor=Principal(**row['result']['_private']['actor']), action=row['action'] + '_failed',
                          outcome='failed', reason_code=code, operation_id=row['id'], request_id=row['result']['_private']['request_id'])
        return None

    def fail(self, owner, operation_id, code):
        owner.check_owner()
        with self.engine.begin() as connection:
            state = self._state(connection, lock=True)
            if state['owner_uuid'] != owner.owner_uuid or state['owner_generation'] != owner.generation:
                _conflict('recovery_owner_changed')
            row = self.get(operation_id, connection)
            if row and row['status'] in ('accepted', 'applying'):
                self._fail(connection, state, row, code)
            owner.check_owner()
