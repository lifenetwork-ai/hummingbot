"""Independent recorder projection/outbox; never a balance or send grant.

SQLite's legacy floating reporting columns remain reporting only. Immutable
configuration and recovery provenance bind a projection to the full namespace.
Financial decimal authority stays in the domain repositories.
"""
import asyncio
import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
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
from hummingbot.strategy_v2.life_liquidity.storage.recorder_positions import FilePositionAdapter, PositionRepository
from hummingbot.strategy_v2.life_liquidity.storage.recorder_progress import (
    PROVENANCE_KEYS,
    FileRecorderProgressAdapter,
    RecorderProgressRepository,
)
from hummingbot.strategy_v2.models.executors_info import ExecutorInfo


@dataclass(frozen=True)
class ExecutorProjection:
    operation_id: str
    binding: StateBinding
    payload_json: str

    def __post_init__(self):
        identifier(self.operation_id)
        if len(self.operation_id) > 96 or not isinstance(self.binding, StateBinding):
            raise StorageError('RECORDER_PROJECTION_INVALID')
        try:
            info = ExecutorInfo.model_validate_json(self.payload_json)
            if (any(not getattr(info, key).is_finite() for key in ('net_pnl_pct', 'net_pnl_quote', 'cum_fees_quote', 'filled_amount_quote'))
                    or not math.isfinite(info.timestamp) or info.timestamp != info.config.timestamp
                    or info.close_timestamp is not None and not math.isfinite(info.close_timestamp)
                    or info.filled_amount_quote < 0):
                raise StorageError('RECORDER_REPORT_VALUE_INVALID')
            if (info.id != info.config.id or info.controller_id != info.config.controller_id
                    or info.type != 'order_executor' or not info.controller_id
                    or info.config.connector_name not in ('okx', 'okx_perpetual')
                    or info.config.trading_pair != 'LIFE-USDT'
                    or info.custom_info.get('recovery_account_uid') != self.binding.account_uid
                    or info.custom_info.get('life_storage_binding') != self.binding.json()):
                raise StorageError('RECORDER_ACCOUNT_OR_IDENTITY_MISMATCH')
            # Validate opaque reporting payload using the actual recorder model;
            # forbid nonfinite JSON even in extensible metadata/reporting fields.
            canonical = json.dumps(json.loads(info.model_dump_json()), sort_keys=True, separators=(',', ':'), allow_nan=False)
            object.__setattr__(self, 'payload_json', canonical)
        except StorageError:
            raise
        except Exception:
            raise StorageError('RECORDER_PROJECTION_INVALID') from None

    @classmethod
    def from_info(cls, operation_id, binding, info):
        detached = info.model_copy(deep=True)
        detached.custom_info = {**detached.custom_info, 'life_storage_binding': binding.json()}
        return cls(operation_id, binding, detached.model_dump_json())

    @property
    def executor_id(self):
        return json.loads(self.payload_json)['id']

    def json(self):
        return {'operation_id': self.operation_id, 'binding': self.binding.json(), 'payload_json': self.payload_json}


@dataclass(frozen=True)
class RecorderRow:
    executor_id: str
    payload_json: str  # Detached immutable reporting snapshot, not financial proof.


@dataclass(frozen=True)
class ProjectionReceipt:
    binding: StateBinding
    operation_id: str
    executor_id: str
    replayed: bool
    atomic_scope: AtomicScope = AtomicScope.RECOVERABLE_SAGA


@dataclass(frozen=True)
class PendingExecutorProjection:
    command: ExecutorProjection
    original_cut: StateCut
    owner: OwnerToken


class RecorderRepository(RecorderProgressRepository, PositionRepository, Protocol):
    async def read(self, binding: StateBinding) -> tuple[RecorderRow, ...]:
        ...

    async def project(self, command: ExecutorProjection, cut: StateCut, owner: OwnerToken) -> ProjectionReceipt:
        ...

    async def pending_projections(self, binding: StateBinding) -> tuple[PendingExecutorProjection, ...]:
        ...

    async def assert_supported_positions(self, binding: StateBinding) -> None:
        ...


class FileRecorderRepository(FileRecorderProgressAdapter, FilePositionAdapter):
    """One bounded store worker owns outbox publication and actual SQLite calls."""

    def __init__(self, store, recorder, controller_id):
        from hummingbot.connector.markets_recorder import MarketsRecorder
        from hummingbot.strategy_v2.life_liquidity.storage.file import FileCheckpointStore
        if not isinstance(store, FileCheckpointStore) or not isinstance(recorder, MarketsRecorder):
            raise StorageError('RECORDER_FILE_REPOSITORY_REQUIRED')
        identifier(controller_id)
        self.store, self._recorder, self.binding, self.controller_id = store, recorder, store.binding, controller_id
        url = recorder.sql_manager.engine.url
        if recorder.sql_manager.engine.dialect.name != 'sqlite' or url.database in (None, ':memory:'):
            raise StorageError('RECORDER_FILE_SQLITE_REQUIRED')
        self._database = Path(url.database).absolute()
        self._identity = {'backend': 'sqlite', 'controller_id': controller_id,
                          'database_sha256': hashlib.sha256(str(self._database.resolve()).encode()).hexdigest()}

    def _verify_binding(self, meta):
        if not self._database.is_file() or self._database.is_symlink():
            raise StorageUnavailable('RECORDER_DATABASE_UNAVAILABLE')
        previous = meta.resources.get('recorder_binding')
        if previous is not None and thaw(previous) != self._identity:
            raise StorageError('RECORDER_REPOSITORY_BINDING_MISMATCH')

    def _read(self):
        self._verify_binding(self.store._read())
        rows = self._recorder.get_executors_by_controller(self.controller_id)
        detached = []
        for info in rows:
            # A foreign/missing binding is an error, never an implicit import.
            command = ExecutorProjection('read', self.binding, info.model_dump_json())
            detached.append(RecorderRow(info.id, command.payload_json))
        return tuple(sorted(detached, key=lambda row: row.executor_id))

    async def read(self, binding):
        self.store._assert_binding(binding)
        return await self.store._call(self._read)

    def _assert_supported_positions(self):
        self._positions()

    async def assert_supported_positions(self, binding):
        self.store._assert_binding(binding)
        await self.store._call(self._assert_supported_positions)

    def _pending_projections(self):
        self._verify_binding(self.store._read())
        result = []
        for row in self.store._read().resources.get('recorder_outbox', {}).values():
            if row['state'] == 'APPLIED':
                continue
            value = row['command']
            command = ExecutorProjection(value['operation_id'], StateBinding.from_json(value['binding']), value['payload_json'])
            cut, owner = StateCut.from_json(row['cut']), OwnerToken.from_json(row['owner'])
            if row['identity'] != fingerprint({'command': command.json(), 'cut': cut.json(), 'owner': owner.json()}):
                raise StorageError('RECORDER_OUTBOX_ORIGIN_UNVERIFIED')
            result.append(PendingExecutorProjection(command, cut, owner))
        return tuple(result)

    async def pending_projections(self, binding):
        self.store._assert_binding(binding)
        return await self.store._call(self._pending_projections)

    def _project(self, command, cut, owner):
        self.store._assert_binding(command.binding)
        self.store._assert_binding(cut.binding)
        info = ExecutorInfo.model_validate_json(command.payload_json)
        if info.controller_id != self.controller_id:
            raise StorageError('RECORDER_CONTROLLER_MISMATCH')
        meta = self.store._read()
        self._verify_binding(meta)
        operations = thaw(meta.resources.get('recorder_outbox', {}))
        identity = fingerprint({'command': command.json(), 'cut': cut.json(), 'owner': owner.json()})
        previous = operations.get(command.operation_id)
        now = utc(self.store._clock())
        if now < owner.valid_from:
            raise StorageError('RECORDER_CLOCK_ROLLBACK')
        if previous is not None:
            if previous['identity'] != identity:
                raise StorageConflict('RECORDER_OPERATION_CONFLICT')
            if meta.ownership.token != owner or meta.ownership.mode not in (
                    OwnerMode.ACTIVE, OwnerMode.DRAINING, OwnerMode.RECOVERY_ONLY):
                raise StorageError('RECORDER_OWNER_MISMATCH')
        else:
            if (not isinstance(owner, OwnerToken) or meta.ownership.token != owner
                    or meta.ownership.mode is not OwnerMode.ACTIVE
                    or not owner.valid_from <= now < owner.expires_at
                    or not getattr(self.store, '_created_offline', False)
                    and getattr(self.store, '_admission_generation', None) != owner.generation
                    or getattr(self.store, '_cold_unarmed', False)):
                raise StorageError('RECORDER_OWNER_REVOKED_OR_EXPIRED')
            if meta.cut != cut:
                raise StorageConflict('STORAGE_CUT_STALE')
            if (any(row['state'] != 'APPLIED' for row in operations.values())
                    or any(row['state'] != 'APPLIED' for name in ('recorder_progress', 'recorder_positions')
                           for row in meta.resources.get(name, {}).values())):
                raise StorageUnavailable('RECORDER_OUTBOX_REPAIR_REQUIRED')
            operations[command.operation_id] = {'identity': identity, 'command': command.json(),
                                                'owner': owner.json(), 'cut': cut.json(), 'state': 'PROJECTING'}
            self.store._commit(meta.cut, owner, 'recorder-start:' + command.operation_id,
                               (('recorder_outbox', encode(operations)), ('recorder_binding', encode(self._identity))))
        try:
            head = meta.resources.get('recorder_progress_heads', {}).get(info.id)
            if head is None:
                self._recorder.project_executor_info(info)
            else:
                rows = self._recorder.get_executors_by_ids([info.id])
                if not rows:
                    self._recorder.project_executor_info(ExecutorInfo.model_validate_json(head['payload_json']))
                if self._row_projection(info.id).payload_json != head['payload_json']:
                    raise StorageUnavailable('RECORDER_PROGRESS_HEAD_UNVERIFIED')
            rows = self._recorder.get_executors_by_ids([info.id])
            if len(rows) != 1:
                raise StorageUnavailable('RECORDER_NATIVE_POSTCONDITION_FAILED')
            actual = ExecutorProjection('verify', self.binding, rows[0].to_executor_info().model_dump_json())
            intended = json.loads(command.payload_json)
            observed = json.loads(actual.payload_json)
            immutable = ('id', 'config', 'controller_id') + (() if head is not None else ('custom_info',))
            if (any(observed[key] != intended[key] for key in immutable)
                    or any(observed['custom_info'].get(key) != intended['custom_info'].get(key) for key in PROVENANCE_KEYS)):
                raise StorageUnavailable('RECORDER_NATIVE_POSTCONDITION_FAILED')
            if previous is None or previous['state'] != 'APPLIED':
                operations[command.operation_id]['state'] = 'APPLIED'
                latest = self.store._read()
                self.store._commit(latest.cut, owner, 'recorder-done:' + command.operation_id,
                                   (('recorder_outbox', encode(operations)),), observation=True)
            return ProjectionReceipt(self.binding, command.operation_id, info.id, previous is not None)
        except Exception:
            self.store._uncertain = True
            raise StorageUnavailable('RECORDER_PROJECTION_UNKNOWN') from None

    async def project(self, command, cut, owner):
        if not isinstance(command, ExecutorProjection) or not isinstance(cut, StateCut) or not isinstance(owner, OwnerToken):
            raise StorageError('RECORDER_PROJECTION_INVALID')
        try:
            return await self.store._call(self._project, command, cut, owner)
        except asyncio.CancelledError:
            self.store._uncertain = True
            raise
