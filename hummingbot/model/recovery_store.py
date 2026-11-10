"""Admin-reviewed recovery receipts and atomic, owner-fenced aggregate repair."""
import hashlib
import hmac
import json
import uuid
from collections import defaultdict
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timedelta

from sqlalchemy import func, select, update

from hummingbot.core.admin_identity import Principal
from hummingbot.core.recovery import MAX_FILLS, MAX_ORDERS, PROOF_TTL_SECONDS, decimal, fingerprint, recovery_blockers
from hummingbot.model.admin_access import AdminAccessStore
from hummingbot.model.admin_command import AdminCommand
from hummingbot.model.bot_state import BotState
from hummingbot.model.bot_state_store import ControlConflict
from hummingbot.model.exchange_credential import ExchangeCredential
from hummingbot.model.order import Order
from hummingbot.model.order_status import OrderStatus
from hummingbot.model.position import Position
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

    def snapshot(self, connection=None):
        if connection is None:
            with self.engine.connect().execution_options(isolation_level='REPEATABLE READ') as connection, connection.begin():
                return self.snapshot(connection)
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
        positions = [dict(row) for row in connection.execute(select(Position).order_by(Position.id).limit(101)).mappings()]
        bindings = [dict(row) for row in connection.execute(select(
            ExchangeCredential.exchange, ExchangeCredential.connector, ExchangeCredential.environment,
            ExchangeCredential.account_id, ExchangeCredential.version, ExchangeCredential.state).where(
                ExchangeCredential.bot_id == self.bot_id).order_by(ExchangeCredential.exchange, ExchangeCredential.connector, ExchangeCredential.environment)).mappings()]
        return dict(state=self._state(connection), orders=orders, fills=fills, positions=positions, bindings=bindings,
                    truncated=len(orders) > MAX_ORDERS or len(fills) > MAX_FILLS or len(positions) > 100)

    def get(self, operation_id, connection=None):
        if connection is None:
            with self.engine.connect() as connection:
                return self.get(operation_id, connection)
        row = connection.execute(select(AdminCommand).where(AdminCommand.bot_id == self.bot_id,
                                                            AdminCommand.id == operation_id, AdminCommand.action.in_(ACTIONS))).mappings().first()
        return dict(row) if row else None

    def latest(self):
        with self.engine.connect() as connection:
            row = connection.execute(select(AdminCommand).where(AdminCommand.bot_id == self.bot_id,
                                                                AdminCommand.action.in_(ACTIONS)).order_by(AdminCommand.created_at.desc(), AdminCommand.id.desc()).limit(1)).mappings().first()
            return dict(row) if row else None

    @staticmethod
    def _same_owner(state, private):
        return state['owner_uuid'] == private['owner_uuid'] and state['owner_generation'] == private['owner_generation']

    def _checked_preview(self, connection, payload, state):
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
        if fingerprint(self.snapshot(connection)) != report['_private']['fingerprint']:
            _conflict('recovery_state_changed')
        return check

    def request(self, action, key, payload, principal, request_id):
        if action not in ACTIONS or not isinstance(key, str) or not 1 <= len(key) <= 128 or any(not 33 <= ord(c) <= 126 for c in key):
            raise ValueError('Invalid recovery request or Idempotency-Key')
        if type(payload.get('expected_version')) is not int or payload['expected_version'] < 0:
            raise ValueError('Invalid expected control version')
        digest = hashlib.sha256(json.dumps(dict(action=action, payload=payload), sort_keys=True).encode()).hexdigest()
        with self.engine.begin() as connection:
            actor = self.access.authorize(connection, principal, 'admin')
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
            blockers = recovery_blockers(state)
            if action == 'recovery_check':
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
                self._checked_preview(connection, payload, state)
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
            result = deepcopy(row['result']); result['stage'] = 'checking_exchange' if row['action'] == 'recovery_check' else 'revalidating'
            return dict(connection.execute(update(AdminCommand).where(AdminCommand.id == row['id']).values(
                status='applying', result=result, updated_at=state['database_now']).returning(*AdminCommand.__table__.c)).mappings().one())

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
        blockers = recovery_blockers(state)
        if checking:
            blockers = [code for code in blockers if code in ('recovery_stop_required', 'recovery_stop_incomplete',
                                                              'recovery_not_halted', 'credential_operation_in_progress', 'recovery_pending_mutations')]
        if blockers:
            _conflict(blockers[0])
        return state, actor

    def finish_check(self, owner, row, snapshot, plan, verification, *, blockers):
        owner.check_owner()
        with self.engine.begin() as connection:
            state, actor = self._operation(connection, owner, row, checking=True)
            if not blockers and not verification.get('verified'):
                _conflict('recovery_check_unavailable')
            if fingerprint(self.snapshot(connection)) != fingerprint(snapshot):
                _conflict('recovery_state_changed')
            result = dict(stage='blocked' if blockers else 'review_required', can_recover=not blockers,
                          blockers=blockers, proposed_changes=plan, verification=verification,
                          confirmation_token=None if blockers else uuid.uuid4().hex,
                          expires_at=(state['database_now'] + timedelta(seconds=PROOF_TTL_SECONDS)).isoformat(),
                          _private={**row['result']['_private'], 'fingerprint': fingerprint(snapshot)})
            self._finish(connection, state, row, result, actor)
            owner.check_owner()
            return True

    def finish_reset(self, owner, row, snapshot, plan, verification):
        owner.check_owner()
        with self.engine.begin() as connection:
            state, actor = self._operation(connection, owner, row)
            check = self._checked_preview(connection, row['payload'], state)
            if (not verification.get('verified') or fingerprint(self.snapshot(connection)) != fingerprint(snapshot)
                    or plan != check['result']['proposed_changes']):
                _conflict('recovery_state_changed')
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
            # This is an operator-confirmed, complete venue/financial proof. It
            # may establish the first incremental baseline; ordinary Start may
            # never certify a pre-existing ledger in its place.
            from sqlalchemy.orm import Session
            if state['config_type'] == 'mm-session' and state['config_file_path']:
                with Session(bind=connection) as session:
                    self._bind_runtime_baseline(session, state, row, snapshot)
            recovery = deepcopy(state['recovery_state'])
            for key in ('halt', 'halt_kind', 'reason', 'shutdown_incomplete', 'automatic_recovery'):
                recovery.pop(key, None)
            recovery['runtime_active'] = False
            state['recovery_state'] = recovery
            result = dict(stage='recovered', can_recover=False, blockers=[], repaired_positions=plan,
                          verification=verification, trading_state='stopped', _private=row['result']['_private'])
            self._finish(connection, state, row, result, actor, control_version=state['control_version'] + 1)
            owner.check_owner()  # Fresh physical authority before committing all writes together.
            return True

    def _bind_runtime_baseline(self, session, state, row, snapshot):
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
        values = dict(financial_hash=financial_hash(session),
                      bindings_hash=binding_hash(session, self.bot_id),
                      watermark=session.query(func.max(RuntimeJournal.id)).scalar() or 0,
                      payload={'baseline': 'reviewed_recovery', 'operation_id': row['id'], 'environment': environment_identity(),
                               'bindings': bindings(session, self.bot_id),
                               'proof_fingerprint': fingerprint(snapshot)})
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

    def _finish(self, connection, state, row, result, actor, *, control_version=None):
        recovery = deepcopy(state['recovery_state'])
        recovery.pop('recovery_operation', None)
        values = dict(recovery_state=recovery)
        if control_version is not None:
            values.update(control_version=control_version, desired_state='stopped')
        connection.execute(update(BotState).where(BotState.bot_id == self.bot_id).values(**values))
        connection.execute(update(AdminCommand).where(AdminCommand.id == row['id']).values(
            status='succeeded', result=result, updated_at=state['database_now']))
        self.access.audit(connection, actor=actor, action=row['action'] + '_completed',
                          outcome='blocked' if result.get('blockers') else 'succeeded', operation_id=row['id'],
                          request_id=row['result']['_private']['request_id'])

    def _fail(self, connection, state, row, code):
        recovery = deepcopy(state['recovery_state'])
        if (recovery.get('recovery_operation') or {}).get('id') == row['id']:
            recovery.pop('recovery_operation', None)
            connection.execute(update(BotState).where(BotState.bot_id == self.bot_id).values(recovery_state=recovery))
        result = {**row['result'], 'stage': 'blocked', 'can_recover': False, 'blockers': [code]}
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
