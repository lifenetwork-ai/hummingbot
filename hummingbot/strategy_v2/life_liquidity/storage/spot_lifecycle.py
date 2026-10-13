"""Repository-owned spot abort/cancel commands and fresh send qualification.

Abort requires durable PREPARED provenance, not an inference from a failed HTTP
call. Cancellation records intent only. Qualification facts are not send grants.
"""
import asyncio
import time
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Protocol

from hummingbot.strategy_v2.life_liquidity.storage.contracts import (
    AtomicScope,
    CommitUnknown,
    OwnerMode,
    OwnerToken,
    StateBinding,
    StorageConflict,
    StorageError,
    encode,
    fingerprint,
    identifier,
    thaw,
    utc,
)
from hummingbot.strategy_v2.life_liquidity.storage.financial import FinancialCut, PrepareSpot


@dataclass(frozen=True)
class SpotLifecycleCommand:
    operation_id: str
    prepare_operation_id: str
    intent_id: str
    client_order_id: str
    action: str
    attempted_at: datetime | None = None
    expected_cancel_attempts: int | None = None
    retry_interval_ms: int | None = None

    def __post_init__(self):
        for value in (self.operation_id, self.prepare_operation_id, self.intent_id, self.client_order_id):
            identifier(value)
        if len(self.operation_id) > 96 or self.action not in ('ABORT_PREPARED', 'REQUEST_CANCEL', 'RECORD_CANCEL_ATTEMPT'):
            raise StorageError('SPOT_LIFECYCLE_COMMAND_INVALID')
        if self.action == 'RECORD_CANCEL_ATTEMPT':
            utc(self.attempted_at)
            if (type(self.expected_cancel_attempts) is not int or self.expected_cancel_attempts < 0
                    or type(self.retry_interval_ms) is not int or self.retry_interval_ms <= 0):
                raise StorageError('SPOT_CANCEL_ATTEMPT_INVALID')
        elif any(value is not None for value in (self.attempted_at, self.expected_cancel_attempts, self.retry_interval_ms)):
            raise StorageError('SPOT_LIFECYCLE_COMMAND_INVALID')

    def json(self):
        result = dict(self.__dict__)
        if self.attempted_at is not None:
            result['attempted_at'] = self.attempted_at.isoformat()
        return result

    @classmethod
    def from_json(cls, row):
        data = dict(row)
        if data.get('attempted_at') is not None:
            data['attempted_at'] = datetime.fromisoformat(data['attempted_at'])
        return cls(**data)


@dataclass(frozen=True)
class SpotLifecycleReceipt:
    binding: StateBinding
    operation_id: str
    intent_id: str
    state: str
    replayed: bool
    atomic_scope: AtomicScope = AtomicScope.RECOVERABLE_SAGA


@dataclass(frozen=True)
class SpotSendQualification:
    binding: StateBinding
    qualified: bool
    reason_code: str
    cut: FinancialCut | None


class SpotLifecycleRepository(Protocol):
    async def apply_lifecycle(self, command: SpotLifecycleCommand, *, cut: FinancialCut,
                              owner: OwnerToken) -> SpotLifecycleReceipt:
        ...

    async def check_spot_send(self, command: PrepareSpot, *, owner: OwnerToken) -> SpotSendQualification:
        ...


class LegacySpotLifecycleAdapter:
    def _observation_context(self):
        from hummingbot.strategy_v2.life_liquidity.storage.financial import RecordedSpotFill, SpotFillObservation
        from hummingbot.strategy_v2.life_liquidity.storage.spot_observation import SpotObservationContext
        self._require_accounting()
        view = self._snapshot()
        resources = self._read().resources
        policy = replace(self._accounting_policy, approved_cashflows=tuple(self._approved_cashflows().values()))
        return SpotObservationContext(self.binding, view.cut, policy, self._fill_policy,
                                      tuple(PrepareSpot.from_json(row['command']) for _, row in sorted(resources['commands'].items())),
                                      tuple(RecordedSpotFill(row['prepare_operation_id'], row['intent_id'],
                                                             row['exchange_order_id'], SpotFillObservation.from_json(row['fill']))
                                            for _, row in sorted(resources.get('fills', {}).items())),
                                      tuple(self._cashflow_from_json(row) for _, row in sorted(resources.get('cashflows', {}).items())),
                                      self._pending_spot_commands(), self._pending_recovery_batches())

    async def observation_context(self, binding):
        self._assert_binding(binding)
        return await self._call(self._observation_context)

    def _verify_lifecycle_projection(self, resources):
        latest = {}
        for operation_id, row in resources.get('lifecycle_operations', {}).items():
            command = SpotLifecycleCommand.from_json(row['command'])
            if (command.operation_id != operation_id or row['state'] != 'APPLIED'
                    or row['identity'] != fingerprint({'command': command.json(), 'owner': row['owner']})):
                raise StorageError('SPOT_LIFECYCLE_PROJECTION_UNVERIFIED')
            record = resources['commands'][command.prepare_operation_id]
            prepared, attempt = self._lifecycle_scope(command, record)
            if command.action == 'ABORT_PREPARED':
                native = self._ledger.reservation_state(command.intent_id)
                if (record['state'] != 'ABORTED_BEFORE_SEND' or attempt.state != 'ABORTED_BEFORE_SEND'
                        or record.get('arm_fingerprint') is not None or native.state != 'TERMINAL'
                        or native.remaining_base != 0 or native.filled_base != 0
                        or not self._subsidy.matches_settled(command.intent_id, Decimal('0'),
                                                             session_id=prepared.session_id)):
                    raise StorageError('SPOT_LIFECYCLE_PROJECTION_UNVERIFIED')
            elif not attempt.cancel_requested:
                raise StorageError('SPOT_LIFECYCLE_PROJECTION_UNVERIFIED')
            if command.action == 'RECORD_CANCEL_ATTEMPT':
                count = command.expected_cancel_attempts + 1
                if attempt.cancel_attempts < count:
                    raise StorageError('SPOT_LIFECYCLE_PROJECTION_UNVERIFIED')
                prior = latest.get(command.intent_id)
                if prior is None or count > prior.expected_cancel_attempts + 1:
                    latest[command.intent_id] = command
        for intent_id, command in latest.items():
            attempt = self._wal.get(intent_id)
            if (attempt.cancel_attempts != command.expected_cancel_attempts + 1
                    or attempt.last_cancel_attempt_at != command.attempted_at.isoformat()):
                raise StorageError('SPOT_LIFECYCLE_PROJECTION_UNVERIFIED')

    def _publish_lifecycle(self, key, owner, commands, operations, hashes):
        meta = self._read()
        return self._commit(meta.cut, owner, key, (
            ('commands', encode(commands)), ('lifecycle_operations', encode(operations)),
            ('journal_hashes', encode(hashes))), observation=True)

    def _lifecycle_scope(self, command, record):
        prepared = PrepareSpot.from_json(record['command'])
        attempt = self._wal.get(command.intent_id)
        if (prepared.intent_id != command.intent_id or prepared.client_order_id != command.client_order_id
                or (attempt.intent_id, attempt.reservation_id, attempt.client_order_id,
                    attempt.session_id, attempt.epoch, attempt.slot_market, attempt.slot_side, attempt.slot_level) != (
                        prepared.intent_id, prepared.intent_id, prepared.client_order_id, prepared.session_id,
                        prepared.epoch, prepared.market, prepared.side, prepared.quote_level)):
            raise StorageError('SPOT_LIFECYCLE_SCOPE_MISMATCH')
        return prepared, attempt

    def _apply_lifecycle(self, command, cut, owner):
        self._assert_binding(cut.checkpoint.binding)
        view, meta = self._snapshot(), self._read()
        operations = thaw(meta.resources.get('lifecycle_operations', {}))
        commands = thaw(meta.resources['commands'])
        identity = fingerprint({'command': command.json(), 'owner': owner.json()})
        previous = operations.get(command.operation_id)
        if previous is not None and previous['identity'] != identity:
            raise StorageConflict('SPOT_LIFECYCLE_OPERATION_CONFLICT')
        record = commands.get(command.prepare_operation_id)
        if record is None or record.get('original_owner') != owner.json():
            raise StorageError('SPOT_LIFECYCLE_ORIGINAL_WRITER_MISMATCH')
        prepared, attempt = self._lifecycle_scope(command, record)
        if previous is not None and previous['state'] == 'APPLIED':
            return SpotLifecycleReceipt(self.binding, command.operation_id, command.intent_id,
                                        previous['result'], True)
        now = utc(self._clock())
        if (meta.ownership.token != owner or meta.ownership.mode not in (
                OwnerMode.ACTIVE, OwnerMode.DRAINING, OwnerMode.RECOVERY_ONLY) or now < owner.valid_from):
            raise StorageError('SPOT_LIFECYCLE_OWNER_MISMATCH')
        if previous is None:
            self._ack_guard(view, owner)
            if cut != view.cut:
                raise StorageConflict('SPOT_CUT_STALE')
            prior_state = record['state']
        else:
            prior_state = previous['prior_state']
            if (record['state'] != previous['phase'] or any(key != command.operation_id and row['state'] != 'APPLIED'
                                                            for key, row in operations.items())
                    or any(key != command.prepare_operation_id and row['state'] in (
                        'PREPARING', 'ARMING', 'ACKING', 'FILLING', 'TERMINATING', 'ABORTING', 'CANCELING')
                        for key, row in commands.items())
                    or any(row['state'] != 'APPLIED' for row in meta.resources.get('cashflow_operations', {}).values())
                    or any(row['state'] != 'APPLIED' for row in meta.resources.get('product_operations', {}).values())
                    or any(row['state'] != 'APPLIED' for row in meta.resources.get('session_operations', {}).values())
                    or any(row['state'] != 'APPLIED' for row in meta.resources.get('recovery_batches', {}).values())
                    or any(row['state'] != 'SETTLED' for row in meta.resources.get('settlement_operations', {}).values())):
                raise StorageError('SPOT_LIFECYCLE_OTHER_RECOVERY_REQUIRED')
        self._verify_fill_projection(commands, meta.resources.get('fills', {}))
        if self._accounting_policy is not None:
            self._verify_capital_projection(commands, meta.resources.get('fills', {}), meta.resources.get('cashflows', {}))
        if command.action == 'ABORT_PREPARED':
            if (prior_state != 'PREPARED' or attempt.state not in ('PREPARED', 'ABORTED_BEFORE_SEND')
                    or record.get('arm_fingerprint') is not None or attempt.exchange_order_id is not None
                    or not self._ledger.can_abort_unsent(prepared.intent_id, prepared.session_id, prepared.epoch)
                    or self._subsidy.verified_fill_floors().get(prepared.intent_id, Decimal('0')) != 0):
                raise StorageError('SPOT_ABORT_NOT_PROVEN_UNSENT')
            phase, result = 'ABORTING', 'ABORTED_BEFORE_SEND'
        else:
            if (prior_state not in ('SEND_UNKNOWN', 'ACKED') or attempt.state != prior_state
                    or not self._matches_accounting_hold(prepared, record)):
                raise StorageError('SPOT_CANCEL_ATTEMPT_INVALID')
            phase, result = 'CANCELING', prior_state
            if command.action == 'RECORD_CANCEL_ATTEMPT':
                intervals = {row['command']['retry_interval_ms'] for row in operations.values()
                             if row['command']['action'] == 'RECORD_CANCEL_ATTEMPT'
                             and row['command']['intent_id'] == command.intent_id}
                if intervals and intervals != {command.retry_interval_ms}:
                    raise StorageError('SPOT_CANCEL_RETRY_POLICY_MISMATCH')
                if previous is None:
                    if command.attempted_at != now or attempt.cancel_attempts != command.expected_cancel_attempts:
                        raise StorageError('SPOT_CANCEL_ATTEMPT_STALE')
                    if attempt.last_cancel_attempt_at is not None:
                        last = datetime.fromisoformat(attempt.last_cancel_attempt_at)
                        if now < last:
                            raise StorageError('SPOT_CANCEL_CLOCK_ROLLBACK')
                        if now < last + timedelta(milliseconds=command.retry_interval_ms):
                            raise StorageError('SPOT_CANCEL_RETRY_NOT_DUE')
        if previous is None:
            operations[command.operation_id] = {'identity': identity, 'command': command.json(), 'owner': owner.json(),
                                                'cut': cut.json(), 'state': 'APPLYING', 'phase': phase,
                                                'prior_state': prior_state, 'result': result}
            record['state'] = phase
            self._publish_lifecycle('lifecycle-start:' + command.operation_id, owner, commands, operations,
                                    dict(view.cut.components))
        try:
            if command.action == 'ABORT_PREPARED':
                self._wal.abort_before_send(prepared.intent_id)
                if self._wal.get(prepared.intent_id).state != 'ABORTED_BEFORE_SEND':
                    raise StorageError('SPOT_ABORT_WAL_INCOMPLETE')
                self._ledger.abort_unsent(prepared.intent_id, session_id=prepared.session_id,
                                          epoch=prepared.epoch, wal=self._wal)
                row = self._ledger.reservation_state(prepared.intent_id)
                if row.state != 'TERMINAL' or row.remaining_base != 0 or row.filled_base != 0:
                    raise StorageError('SPOT_ABORT_CAPITAL_INCOMPLETE')
                self._subsidy.release_unsent(prepared.intent_id, wal=self._wal, reservations=self._ledger)
                if not self._subsidy.matches_settled(prepared.intent_id, Decimal('0'), session_id=prepared.session_id):
                    raise StorageError('SPOT_ABORT_SUBSIDY_INCOMPLETE')
            else:
                if command.action == 'RECORD_CANCEL_ATTEMPT':
                    native = self._wal.get(prepared.intent_id)
                    if native.cancel_attempts == command.expected_cancel_attempts:
                        self._wal.mark_cancel_attempt(prepared.intent_id, at=command.attempted_at)
                        native = self._wal.get(prepared.intent_id)
                    if (native.cancel_attempts != command.expected_cancel_attempts + 1
                            or native.last_cancel_attempt_at != command.attempted_at.isoformat()):
                        raise StorageError('SPOT_CANCEL_ATTEMPT_PUBLICATION_INCOMPLETE')
                else:
                    self._wal.mark_cancel_requested(prepared.intent_id)
                self._ledger.request_cancel(prepared.intent_id)
                if (not self._wal.get(prepared.intent_id).cancel_requested
                        or self._ledger.reservation_state(prepared.intent_id).state != 'CANCEL_PENDING'):
                    raise StorageError('SPOT_CANCEL_PUBLICATION_INCOMPLETE')
            verified = self._snapshot()
            record['state'] = result
            operations[command.operation_id]['state'] = 'APPLIED'
            self._publish_lifecycle('lifecycle-done:' + command.operation_id, owner, commands, operations,
                                    dict(verified.cut.components))
            self._snapshot()
            return SpotLifecycleReceipt(self.binding, command.operation_id, command.intent_id, result, previous is not None)
        except BaseException:
            self._uncertain = True
            raise CommitUnknown('SPOT_LIFECYCLE_UNKNOWN') from None

    async def apply_lifecycle(self, command, *, cut, owner):
        if (not isinstance(command, SpotLifecycleCommand) or not isinstance(cut, FinancialCut)
                or not isinstance(owner, OwnerToken)):
            raise StorageError('SPOT_LIFECYCLE_COMMAND_INVALID')
        try:
            return await self._call(self._apply_lifecycle, command, cut, owner)
        except asyncio.CancelledError:
            self._uncertain = True
            raise

    def _pending_lifecycle_holds(self, resources, session_id, now):
        base = quote = session = day = campaign = Decimal('0')
        for row in resources.get('lifecycle_operations', {}).values():
            if row['state'] == 'APPLIED' or row['command']['action'] != 'ABORT_PREPARED':
                continue
            original = resources['commands'][row['command']['prepare_operation_id']]
            command = PrepareSpot.from_json(original['command'])
            reservation = self._ledger.reservation_state(command.intent_id)
            missing = command.quantity_base - reservation.remaining_base
            if command.side == 'BUY':
                quote += missing * command.limit_price_usdt
            else:
                base += missing
            if self._subsidy.matches_settled(command.intent_id, Decimal('0'), session_id=command.session_id):
                campaign += command.subsidy_quote_usdt
                session += command.subsidy_quote_usdt if command.session_id == session_id else Decimal('0')
                day += (command.subsidy_quote_usdt if datetime.fromisoformat(original['prepared_at']).date() == now.date()
                        else Decimal('0'))
        return base, quote, session, day, campaign

    def _check_spot_send(self, command, owner):
        try:
            view, meta = self._snapshot(), self._read()
            self._ack_guard(view, owner)
            self._guard(replace(view, recovery_required=False), owner)
            record = meta.resources['commands'].get(command.operation_id)
            if (record is None or record['command'] != command.json() or record['state'] != 'SEND_UNKNOWN'
                    or record.get('arm_owner') != owner.json() or view.budget.loss_exhausted
                    or view.balance.available_base_life < 0 or view.balance.available_quote_usdt < 0
                    or self._fee_claims(meta.resources['commands'])[2]
                    or time.monotonic() >= self._monotonic_deadline):
                raise StorageError('SPOT_SEND_STATE_UNQUALIFIED')
            attempt = self._wal.get(command.intent_id)
            if (attempt.state != 'SEND_UNKNOWN' or attempt.cancel_requested or attempt.exchange_order_id is not None
                    or self._ledger.reservation_state(command.intent_id).filled_base != 0
                    or not self._matches_accounting_hold(command, record)):
                raise StorageError('SPOT_SEND_STATE_UNQUALIFIED')
            self._lifecycle_scope(SpotLifecycleCommand('check', command.operation_id, command.intent_id,
                                                       command.client_order_id, 'REQUEST_CANCEL'), record)
            return SpotSendQualification(self.binding, True, 'SPOT_SEND_STATE_QUALIFIED', view.cut)
        except StorageError as exc:
            return SpotSendQualification(self.binding, False, exc.code, None)

    async def check_spot_send(self, command, *, owner):
        if not isinstance(command, PrepareSpot) or not isinstance(owner, OwnerToken):
            raise StorageError('SPOT_SEND_COMMAND_INVALID')
        return await self._call(self._check_spot_send, command, owner)
