"""Ordered, immutable reporting commands; SQL floats never become money proof."""
import asyncio
import json
from dataclasses import dataclass
from typing import Protocol

from hummingbot.strategy_v2.life_liquidity.storage.contracts import (
    AtomicScope,
    OwnerMode,
    OwnerToken,
    StateBinding,
    StateCut,
    StorageConflict,
    StorageError,
    StorageUnavailable,
    encode,
    fingerprint,
    identifier,
    thaw,
    utc,
)
from hummingbot.strategy_v2.models.executors_info import ExecutorInfo

REPORT_FLOATS = ('net_pnl_pct', 'net_pnl_quote', 'cum_fees_quote', 'filled_amount_quote')
PROVENANCE_KEYS = ('recovery_account_uid', 'life_storage_binding')


def same_reporting(left, right):
    """Compare actual legacy SQL Float representation; this is reporting only."""
    left, right = json.loads(left), json.loads(right)
    return all(float(left[key]) == float(right[key]) if key in REPORT_FLOATS else left[key] == right[key]
               for key in left) and left.keys() == right.keys()


def progress_transition(before, after):
    for key in ('id', 'controller_id', 'config', 'type', 'timestamp'):
        if before[key] != after[key]:
            raise StorageConflict('RECORDER_PROGRESS_IDENTITY_CONFLICT')
    if any(before['custom_info'].get(key) != after['custom_info'].get(key) for key in PROVENANCE_KEYS):
        raise StorageConflict('RECORDER_PROGRESS_PROVENANCE_CONFLICT')
    old, new = before['custom_info'].get('recovery_order_ids'), after['custom_info'].get('recovery_order_ids')
    if (not isinstance(old, list) or not isinstance(new, list) or not set(old).issubset(new)
            or len(set(new)) != len(new) or any(type(wire) is not str or not wire for wire in new)
            or after['status'] < before['status']
            or float(after['filled_amount_quote']) < float(before['filled_amount_quote'])
            or after['status'] == 4 and after['is_active'] is not False):
        raise StorageError('RECORDER_PROGRESS_TRANSITION_INVALID')


@dataclass(frozen=True)
class ExecutorProgress:
    operation_id: str
    binding: StateBinding
    expected_sequence: int
    payload_json: str

    def __post_init__(self):
        from hummingbot.strategy_v2.life_liquidity.storage.recorder import ExecutorProjection
        identifier(self.operation_id)
        if type(self.expected_sequence) is not int or self.expected_sequence < 0:
            raise StorageError('RECORDER_PROGRESS_INVALID')
        validated = ExecutorProjection(self.operation_id, self.binding, self.payload_json)
        object.__setattr__(self, 'payload_json', validated.payload_json)

    @classmethod
    def from_info(cls, operation_id, binding, expected_sequence, info):
        from hummingbot.strategy_v2.life_liquidity.storage.recorder import ExecutorProjection
        captured = ExecutorProjection.from_info(operation_id, binding, info)
        return cls(operation_id, binding, expected_sequence, captured.payload_json)

    @property
    def executor_id(self):
        return json.loads(self.payload_json)['id']

    def json(self):
        return {'operation_id': self.operation_id, 'binding': self.binding.json(),
                'expected_sequence': self.expected_sequence, 'payload_json': self.payload_json}

    @classmethod
    def from_json(cls, value):
        return cls(value['operation_id'], StateBinding.from_json(value['binding']),
                   value['expected_sequence'], value['payload_json'])


@dataclass(frozen=True)
class PendingExecutorProgress:
    command: ExecutorProgress
    original_cut: StateCut
    owner: OwnerToken


@dataclass(frozen=True)
class ProgressReceipt:
    binding: StateBinding
    operation_id: str
    executor_id: str
    sequence: int
    replayed: bool
    atomic_scope: AtomicScope = AtomicScope.RECOVERABLE_SAGA


@dataclass(frozen=True)
class RecorderProgressCut:
    executor_id: str
    sequence: int
    cut: StateCut


class RecorderProgressRepository(Protocol):
    async def progress_cut(self, executor_id: str, binding: StateBinding) -> RecorderProgressCut:
        ...

    async def update_progress(self, command: ExecutorProgress, *, cut: StateCut, owner: OwnerToken) -> ProgressReceipt:
        ...

    async def pending_progress(self, binding: StateBinding) -> tuple[PendingExecutorProgress, ...]:
        ...


class FileRecorderProgressAdapter:
    def _progress_cut(self, executor_id):
        identifier(executor_id)
        meta = self.store._read()
        self._verify_binding(meta)
        head = meta.resources.get('recorder_progress_heads', {}).get(executor_id)
        return RecorderProgressCut(executor_id, 0 if head is None else head['sequence'], meta.cut)

    async def progress_cut(self, executor_id, binding):
        self.store._assert_binding(binding)
        return await self.store._call(self._progress_cut, executor_id)

    def _row_projection(self, executor_id):
        from hummingbot.strategy_v2.life_liquidity.storage.recorder import ExecutorProjection
        rows = self._recorder.get_executors_by_ids([executor_id])
        if len(rows) != 1:
            raise StorageUnavailable('RECORDER_PROGRESS_PROJECTION_REQUIRED')
        return ExecutorProjection('read', self.binding, rows[0].to_executor_info().model_dump_json())

    def _pending_progress(self):
        meta = self.store._read()
        self._verify_binding(meta)
        result = []
        for row in meta.resources.get('recorder_progress', {}).values():
            if row['state'] == 'APPLIED':
                continue
            command = ExecutorProgress.from_json(row['command'])
            cut, owner = StateCut.from_json(row['cut']), OwnerToken.from_json(row['owner'])
            if row['identity'] != fingerprint({'command': command.json(), 'cut': cut.json(), 'owner': owner.json()}):
                raise StorageError('RECORDER_PROGRESS_ORIGIN_UNVERIFIED')
            result.append(PendingExecutorProgress(command, cut, owner))
        return tuple(result)

    async def pending_progress(self, binding):
        self.store._assert_binding(binding)
        return await self.store._call(self._pending_progress)

    def _update_progress(self, command, cut, owner):
        self.store._assert_binding(command.binding)
        self.store._assert_binding(cut.binding)
        meta = self.store._read()
        self._verify_binding(meta)
        info = ExecutorInfo.model_validate_json(command.payload_json)
        if info.controller_id != self.controller_id:
            raise StorageError('RECORDER_CONTROLLER_MISMATCH')
        identity = fingerprint({'command': command.json(), 'cut': cut.json(), 'owner': owner.json()})
        operations = thaw(meta.resources.get('recorder_progress', {}))
        heads = thaw(meta.resources.get('recorder_progress_heads', {}))
        previous = operations.get(command.operation_id)
        if previous is not None and previous['identity'] != identity:
            raise StorageConflict('RECORDER_PROGRESS_OPERATION_CONFLICT')
        now = utc(self.store._clock())
        if (meta.ownership.token != owner or meta.ownership.mode not in (
                OwnerMode.ACTIVE, OwnerMode.DRAINING, OwnerMode.RECOVERY_ONLY) or now < owner.valid_from):
            raise StorageError('RECORDER_PROGRESS_OWNER_INVALID')
        if previous is not None and previous['state'] == 'APPLIED':
            return ProgressReceipt(self.binding, command.operation_id, info.id, command.expected_sequence + 1, True)
        head = heads.get(info.id)
        if (0 if head is None else head['sequence']) != command.expected_sequence:
            raise StorageConflict('RECORDER_PROGRESS_SEQUENCE_STALE')
        actual = self._row_projection(info.id)
        if previous is None:
            if meta.cut != cut:
                raise StorageConflict('STORAGE_CUT_STALE')
            if (any(row['state'] != 'APPLIED' for row in operations.values())
                    or any(row['state'] != 'APPLIED' for name in ('recorder_outbox', 'recorder_positions', 'recorder_events', 'recorder_configurations')
                           for row in meta.resources.get(name, {}).values())):
                raise StorageUnavailable('RECORDER_OUTBOX_REPAIR_REQUIRED')
            origins = [row for row in meta.resources.get('recorder_outbox', {}).values()
                       if json.loads(row['command']['payload_json'])['id'] == info.id and row['state'] == 'APPLIED']
            if not origins:
                raise StorageError('RECORDER_PROGRESS_PROJECTION_REQUIRED')
            expected = origins[0]['command']['payload_json'] if head is None else head['payload_json']
            if not same_reporting(actual.payload_json, expected):
                raise StorageUnavailable('RECORDER_PROGRESS_HEAD_UNVERIFIED')
            progress_transition(json.loads(actual.payload_json), json.loads(command.payload_json))
            operations[command.operation_id] = {'identity': identity, 'command': command.json(), 'cut': cut.json(),
                                                'owner': owner.json(), 'state': 'PROJECTING'}
            self.store._commit(meta.cut, owner, 'report-start:' + command.operation_id,
                               (('recorder_progress', encode(operations)),), observation=True)
        else:
            expected = (next(row['command']['payload_json'] for row in meta.resources['recorder_outbox'].values()
                             if json.loads(row['command']['payload_json'])['id'] == info.id)
                        if head is None else head['payload_json'])
            if not (same_reporting(actual.payload_json, expected) or same_reporting(actual.payload_json, command.payload_json)):
                raise StorageUnavailable('RECORDER_PROGRESS_NATIVE_CUT_UNVERIFIED')
            progress_transition(json.loads(expected), json.loads(command.payload_json))
        try:
            self._recorder.update_executor_info(info)
            result = self._row_projection(info.id)
            if not same_reporting(result.payload_json, command.payload_json):
                raise StorageUnavailable('RECORDER_PROGRESS_NATIVE_POSTCONDITION_FAILED')
            heads[info.id] = {'sequence': command.expected_sequence + 1, 'payload_json': result.payload_json,
                              'operation_id': command.operation_id}
            operations[command.operation_id]['state'] = 'APPLIED'
            latest = self.store._read()
            self.store._commit(latest.cut, owner, 'report-done:' + command.operation_id,
                               (('recorder_progress', encode(operations)), ('recorder_progress_heads', encode(heads))),
                               observation=True)
            return ProgressReceipt(self.binding, command.operation_id, info.id, command.expected_sequence + 1, previous is not None)
        except BaseException:
            self.store._uncertain = True
            raise StorageUnavailable('RECORDER_PROGRESS_UNKNOWN') from None

    async def update_progress(self, command, *, cut, owner):
        if not isinstance(command, ExecutorProgress) or not isinstance(cut, StateCut) or not isinstance(owner, OwnerToken):
            raise StorageError('RECORDER_PROGRESS_INVALID')
        try:
            return await self.store._call(self._update_progress, command, cut, owner)
        except asyncio.CancelledError:
            self.store._uncertain = True
            raise
