"""Bound configuration audit. SQL is a projection, never activation authority."""
import asyncio
import json
import math
from dataclasses import asdict, dataclass
from typing import Protocol

from hummingbot.model.controllers import Controllers
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


def financial_controller_config(config):
    """Operational kill cannot change the pinned financial policy namespace.

    Normalizing the existing false default preserves existing fingerprints.
    Kill is checked separately by every local send fence.
    """
    return {**config.model_dump(mode='json'), 'manual_kill_switch': False}


def service_policy(config):
    # Operational timeouts have floats; keep them in a strict JSON string,
    # outside the financial port's restricted decimal-string JSON domain.
    value = {**config.model_dump(mode='json'), 'controller': financial_controller_config(config.controller)}
    return {'service_json': json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)}


@dataclass(frozen=True)
class ConfigurationReport:
    operation_id: str
    binding: StateBinding
    controller_id: str
    expected_sequence: int
    active_revision: int
    controller_json: str
    policy_hash: str
    candidate_digest: str
    outcome: str
    reason_code: str
    timestamp_ms: int

    def __post_init__(self):
        from controllers.generic.life_liquidity import LifeLiquidityConfig
        from hummingbot.strategy_v2.life_liquidity.storage.contracts import digest
        identifier(self.operation_id)
        identifier(self.controller_id)
        digest(self.policy_hash)
        digest(self.candidate_digest)
        reasons = {'INITIAL': 'CONFIG_STARTUP', 'APPLIED': 'CONFIG_UPDATE_APPLIED', 'UNCHANGED': 'CONFIG_UNCHANGED'}
        if (type(self.binding) is not StateBinding or len(self.operation_id) > 96
                or type(self.controller_json) is not str or len(self.controller_json.encode()) > 65536
                or type(self.expected_sequence) is not int or self.expected_sequence < 0
                or type(self.active_revision) is not int or self.active_revision < 1
                or type(self.timestamp_ms) is not int or not 0 <= self.timestamp_ms < 2 ** 53
                or self.outcome not in (*reasons, 'REJECTED')
                or self.outcome != 'REJECTED' and self.reason_code != reasons[self.outcome]
                or self.outcome == 'REJECTED' and self.reason_code not in (
                    'CONFIG_UPDATE_UNSUPPORTED', 'CONFIG_VALIDATION_FAILED', 'CONFIG_LOAD_FAILED')):
            raise StorageError('CONFIG_REPORT_INVALID')
        try:
            c = LifeLiquidityConfig.model_validate_json(self.controller_json)
            if (c.id != self.controller_id or c.storage_binding != self.binding or c.storage_backend not in ('file', 'postgres')
                    or fingerprint(c.strategy.model_dump(mode='json')) != self.binding.config_hash):
                raise ValueError()
            object.__setattr__(self, 'controller_json', c.model_dump_json())
        except Exception:
            raise StorageError('CONFIG_REPORT_BINDING_INVALID') from None

    def json(self):
        return {**asdict(self), 'binding': self.binding.json()}

    @classmethod
    def from_json(cls, value):
        return cls(**{**value, 'binding': StateBinding.from_json(value['binding'])})


@dataclass(frozen=True)
class ConfigurationState:
    sequence: int
    active_revision: int
    controller_json: str | None
    rejection_latched: bool
    kill_latched: bool
    last_outcome: str | None
    reason_code: str | None = None
    rejection_reason_code: str | None = None


@dataclass(frozen=True)
class ConfigurationReceipt:
    operation_id: str
    sequence: int
    active_revision: int
    reason_code: str
    replayed: bool
    atomic_scope: AtomicScope = AtomicScope.RECOVERABLE_SAGA


@dataclass(frozen=True)
class PendingConfiguration:
    command: ConfigurationReport
    original_cut: StateCut
    owner: OwnerToken


class ConfigurationRepository(Protocol):
    async def configuration(self, binding: StateBinding) -> ConfigurationState:
        ...

    async def pending_configurations(self, binding: StateBinding) -> tuple[PendingConfiguration, ...]:
        ...

    async def report_configuration(self, command: ConfigurationReport, *, cut: StateCut,
                                   owner: OwnerToken) -> ConfigurationReceipt:
        ...


def validate_successor(command, active):
    if active is None:
        valid = command.outcome == 'INITIAL' and command.active_revision == 1 and command.expected_sequence == 0
    else:
        c, old = json.loads(command.controller_json), json.loads(active.controller_json)
        valid = (command.outcome != 'INITIAL' and command.policy_hash == active.policy_hash
                 and {**c, 'manual_kill_switch': False} == {**old, 'manual_kill_switch': False}
                 and command.active_revision == active.active_revision + (command.outcome == 'APPLIED')
                 and (c != old) == (command.outcome == 'APPLIED') and command.timestamp_ms >= active.timestamp_ms)
    if not valid:
        raise StorageError('CONFIG_TRANSITION_INVALID')


def configuration_rows(session, sql_id):
    rows = session.query(Controllers).filter(Controllers.id == sql_id).all()
    if any(type(r.config) is not dict or type(r.config.get('expected_sequence')) is not int
           or not math.isfinite(r.timestamp) for r in rows):
        raise StorageUnavailable('CONFIG_SQL_UNVERIFIED')
    return tuple(sorted(({'id': r.id, 'timestamp': r.timestamp, 'type': r.type, 'config': r.config} for r in rows),
                        key=lambda r: r['config']['expected_sequence']))


def persist_configurations(recorder, sql_id, before, after):
    if recorder.sql_manager.engine.dialect.name != 'sqlite':
        raise StorageError('RECORDER_FILE_SQLITE_REQUIRED')
    with recorder.sql_manager.get_new_session() as session:
        session.connection().exec_driver_sql('BEGIN IMMEDIATE')
        actual = configuration_rows(session, sql_id)
        if actual == after:
            return
        if actual != before or any(r['id'] != sql_id for r in after):
            raise StorageConflict('CONFIG_SQL_CONFLICT')
        # Restore only missing verified rows; never rewrite observed history.
        if any(row not in after for row in actual) or len({json.dumps(r, sort_keys=True) for r in actual}) != len(actual):
            raise StorageConflict('CONFIG_SQL_CONFLICT')
        for row in after:
            if row not in actual:
                session.add(Controllers(**row))
        session.commit()


class FileConfigurationAdapter:
    def _configuration_sql_id(self):
        return 'life-config:' + fingerprint({'binding': self.binding.json(), 'controller': self.controller_id})

    def _configuration_rows(self):
        self.store.assert_worker_thread()
        with self._recorder.sql_manager.get_new_session() as session:
            return configuration_rows(session, self._configuration_sql_id())

    def _configuration_history(self):
        try:
            return self._audit_configuration_history()
        except StorageError:
            raise
        except Exception:
            raise StorageUnavailable('CONFIG_HISTORY_UNVERIFIED') from None

    def _audit_configuration_history(self):
        meta = self.store._read()
        self._verify_binding(meta)
        with self.store._state.inspect() as checkpoint:
            ledger = thaw(checkpoint['operations'])
        result, active, last_time = [], None, -1
        for key, row in sorted(meta.resources.get('recorder_configurations', {}).items(),
                               key=lambda item: item[1]['command']['expected_sequence']):
            command = ConfigurationReport.from_json(thaw(row['command']))
            if json.loads(command.controller_json)['storage_backend'] != 'file':
                raise StorageError('CONFIG_REPORT_BACKEND_INVALID')
            cut, owner = StateCut.from_json(thaw(row['cut'])), OwnerToken.from_json(thaw(row['owner']))
            start = ledger['config-start:' + key]
            if (command.operation_id != key or command.binding != self.binding or cut.binding != self.binding
                    or command.controller_id != self.controller_id or command.expected_sequence != len(result)
                    or command.timestamp_ms < last_time or row['state'] not in ('PROJECTING', 'APPLIED')
                    or start['revision'] != cut.revision + 1
                    or row['identity'] != fingerprint({'command': command.json(), 'cut': cut.json(), 'owner': owner.json()})
                    or result and result[-1][1]['state'] != 'APPLIED'):
                raise StorageUnavailable('CONFIG_HISTORY_UNVERIFIED')
            validate_successor(command, active)
            if row['state'] == 'APPLIED':
                done = ledger['config-done:' + key]
                if done['revision'] != row['revision'] or done['revision'] <= start['revision']:
                    raise StorageUnavailable('CONFIG_HISTORY_UNVERIFIED')
            active, last_time = command, command.timestamp_ms
            result.append((command, row, cut, owner))
        return meta, result

    def _expected_configuration_rows(self, history):
        return tuple({'id': self._configuration_sql_id(), 'timestamp': c.timestamp_ms / 1000,
                      'type': 'generic', 'config': c.json()} for c, _, _, _ in history)

    def _configuration(self):
        meta, history = self._configuration_history()
        if any(row['state'] != 'APPLIED' for _, row, _, _ in history):
            raise StorageUnavailable('CONFIG_OUTBOX_REPAIR_REQUIRED')
        if self._configuration_rows() != self._expected_configuration_rows(history):
            raise StorageUnavailable('CONFIG_SQL_UNVERIFIED')
        if not history:
            return ConfigurationState(0, 0, None, False, False, None)
        rejected = max((r['revision'] for c, r, _, _ in history if c.outcome == 'REJECTED'), default=0)
        valid = tuple(r['revision'] for c, r, _, _ in history if c.outcome != 'REJECTED' and r['revision'] > rejected)
        # _read audits signatures and their original fact/cut before this check.
        cleared = any(r['approval']['role'] == 'ACTIVATE'
                      and any(v <= r['cut']['checkpoint']['revision'] for v in valid)
                      for r in meta.resources.get('control_operations', {}).values())
        last = history[-1][0]
        rejection_reason = next((c.reason_code for c, _, _, _ in reversed(history) if c.outcome == 'REJECTED'), None)
        return ConfigurationState(len(history), last.active_revision, last.controller_json, bool(rejected and not cleared),
                                  any(json.loads(c.controller_json)['manual_kill_switch'] for c, _, _, _ in history),
                                  last.outcome, last.reason_code, rejection_reason)

    async def configuration(self, binding):
        self.store._assert_binding(binding)
        return await self.store._call(self._configuration)

    def _pending_configurations(self):
        _, history = self._configuration_history()
        return tuple(PendingConfiguration(c, cut, owner) for c, r, cut, owner in history if r['state'] != 'APPLIED')

    async def pending_configurations(self, binding):
        self.store._assert_binding(binding)
        return await self.store._call(self._pending_configurations)

    def _repair_configurations(self):
        _, history = self._configuration_history()
        expected, actual = self._expected_configuration_rows(history), self._configuration_rows()
        if any(row not in expected for row in actual) or len({json.dumps(r, sort_keys=True) for r in actual}) != len(actual):
            raise StorageUnavailable('CONFIG_SQL_UNVERIFIED')
        if actual != expected:
            self._recorder.project_configuration_reports(self._configuration_sql_id(), actual, expected)
            if self._configuration_rows() != expected:
                raise StorageUnavailable('CONFIG_SQL_UNVERIFIED')

    def _report_configuration(self, command, cut, owner):
        self.store._assert_binding(command.binding)
        if json.loads(command.controller_json)['storage_backend'] != 'file':
            raise StorageError('CONFIG_REPORT_BACKEND_INVALID')
        meta, history = self._configuration_history()
        now = utc(self.store._clock())
        operations = thaw(meta.resources.get('recorder_configurations', {}))
        previous = operations.get(command.operation_id)
        identity = fingerprint({'command': command.json(), 'cut': cut.json(), 'owner': owner.json()})
        if previous is not None and previous['identity'] != identity:
            raise StorageConflict('CONFIG_OPERATION_CONFLICT')
        if previous is not None and previous['state'] == 'APPLIED':
            self._configuration()  # Historical readback never restores an old owner/configuration.
            return ConfigurationReceipt(command.operation_id, command.expected_sequence + 1, command.active_revision,
                                        command.reason_code, True)
        if (meta.ownership.token != owner or meta.ownership.mode not in (
                OwnerMode.ACTIVE, OwnerMode.DRAINING, OwnerMode.RECOVERY_ONLY) or now < owner.valid_from):
            raise StorageError('CONFIG_OWNER_INVALID')
        if previous is None:
            self._configuration()  # Exact native history before accepting a new attempt.
            if cut != meta.cut or command.expected_sequence != len(history):
                raise StorageConflict('CONFIG_SEQUENCE_OR_CUT_STALE')
            if command.timestamp_ms > int(now.timestamp() * 1000):
                raise StorageError('CONFIG_TIME_INVALID')
            validate_successor(command, None if not history else history[-1][0])
            if self.store._reporting_pending(meta.resources):
                raise StorageUnavailable('RECORDER_OUTBOX_REPAIR_REQUIRED')
            operations[command.operation_id] = {'identity': identity, 'command': command.json(), 'cut': cut.json(),
                                                'owner': owner.json(), 'state': 'PROJECTING'}
            self.store._commit(meta.cut, owner, 'config-start:' + command.operation_id,
                               (('recorder_configurations', encode(operations)), ('recorder_binding', encode(self._identity))), observation=True)
        try:
            self._repair_configurations()
            if previous is None or previous['state'] != 'APPLIED':
                latest = self.store._read()
                operations[command.operation_id].update(state='APPLIED', revision=latest.cut.revision + 1)
                self.store._commit(latest.cut, owner, 'config-done:' + command.operation_id,
                                   (('recorder_configurations', encode(operations)),), observation=True)
            self._configuration()
            return ConfigurationReceipt(command.operation_id, command.expected_sequence + 1, command.active_revision,
                                        command.reason_code, previous is not None)
        except BaseException:
            self.store._uncertain = True
            raise StorageUnavailable('CONFIG_PUBLICATION_UNKNOWN') from None

    async def report_configuration(self, command, *, cut, owner):
        if type(command) is not ConfigurationReport or type(cut) is not StateCut or type(owner) is not OwnerToken:
            raise StorageError('CONFIG_REPORT_INVALID')
        try:
            return await self.store._call(self._report_configuration, command, cut, owner)
        except asyncio.CancelledError:
            # Cancellation can leave physical work running; never certify rollback.
            self.store._uncertain = True
            raise
