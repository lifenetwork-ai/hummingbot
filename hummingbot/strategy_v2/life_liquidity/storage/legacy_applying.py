"""Retire interrupted File accounting without completing its money.

The capsule is source evidence for a future native import. It is neither a
balance import nor a send grant. Bound joint repositories must supply all their
financial participants; recorder/control-plane service backup remains separate.
"""
import asyncio
import base64
import hashlib
import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Context, localcontext
from pathlib import Path
from uuid import UUID

from hummingbot.strategy_v2.life_liquidity.storage.contracts import (
    CommitUnknown,
    OwnerMode,
    OwnerToken,
    ResourceVersion,
    StateBinding,
    StateCut,
    StorageConflict,
    StorageError,
    StorageUnavailable,
    digest,
    encode,
    fingerprint,
    identifier,
    thaw,
    utc,
)
from hummingbot.strategy_v2.life_liquidity.storage.control_plane import OperatorApproval
from hummingbot.strategy_v2.life_liquidity.storage.financial import FinancialCut
from hummingbot.strategy_v2.life_liquidity.storage.handoff_artifact import read_bytes, sync_dir
from hummingbot.strategy_v2.life_liquidity.storage.joint_settlement import JointCashSpot
from hummingbot.strategy_v2.life_liquidity.storage.recovery import SpotFinancialRecoveryFacts


@dataclass(frozen=True)
class LegacyApplyingSource:
    cut: FinancialCut
    checkpoint_bytes: bytes
    journal_images: tuple[tuple[str, bytes], ...]
    facts: SpotFinancialRecoveryFacts
    ancillary_images: tuple[tuple[str, str, bytes], ...] = ()

    def __post_init__(self):
        with localcontext(Context(prec=28, rounding=ROUND_HALF_EVEN)):
            self._validate()

    def _validate(self):
        try:
            if (type(self.cut) is not FinancialCut or type(self.facts) is not SpotFinancialRecoveryFacts
                    or type(self.checkpoint_bytes) is not bytes or type(self.journal_images) is not tuple
                    or any(type(row) is not tuple or len(row) != 2 or type(row[0]) is not str or type(row[1]) is not bytes
                           for row in self.journal_images)):
                raise ValueError()
            envelope = json.loads(self.checkpoint_bytes)
            state = envelope['state']
            actual = StateCut(self.cut.checkpoint.binding, state['revision'], state['event_version'],
                              tuple(ResourceVersion(k, v) for k, v in sorted(state['versions'].items())), fingerprint(state))
            names = tuple(name for name, _ in self.journal_images)
            if (set(envelope) != {'schema_version', 'policy', 'state'} or envelope['schema_version'] != 1
                    or envelope['policy']['binding'] != self.cut.checkpoint.binding.json()
                    or actual != self.cut.checkpoint or self.facts.binding != self.cut.checkpoint.binding
                    or state['ownership']['mode'] not in ('DRAINING', 'RECOVERY_ONLY')
                    or names != tuple(sorted(set(names))) or not {'session', 'wal', 'reservations', 'subsidy', 'loss'} <= set(names)
                    or {name: hashlib.sha256(raw).hexdigest() for name, raw in self.journal_images} != dict(self.cut.components)
                    or type(self.ancillary_images) is not tuple
                    or not self.ancillary_images and len(self.pending_operation_ids) != 1):
                raise ValueError()
            if self.ancillary_images:
                from hummingbot.strategy_v2.life_liquidity.storage.legacy_joint_applying import validate_images
                validate_images(self)
            for key in self.core_pending_operation_ids:
                row = self.original_operations[key]
                command, cut, owner = JointCashSpot.from_json(row['command']), FinancialCut.from_json(row['cut']), OwnerToken.from_json(row['owner'])
                if (command.operation_id != key or command.binding != self.cut.checkpoint.binding
                        or cut.checkpoint.binding != command.binding or owner.json() != state['ownership']['token']
                        or row['fingerprint'] != fingerprint({'command': command.json(), 'cut': cut.json(), 'owner': owner.json()})
                        or {name: hashlib.sha256(bytes.fromhex(raw)).hexdigest() for name, raw in row['before_native'].items()} != dict(cut.components)):
                    raise ValueError()
        except (ValueError, TypeError, KeyError, AttributeError, ArithmeticError, StorageError):
            raise StorageError('LEGACY_APPLYING_SOURCE_INVALID') from None

    @property
    def original_operations(self):
        return json.loads(self.checkpoint_bytes)['state']['resources'].get('joint_cash_operations', {})

    @property
    def core_pending_operation_ids(self):
        return tuple(sorted(k for k, v in self.original_operations.items() if v['state'] == 'APPLYING'))

    @property
    def joint_state(self):
        return None if not self.ancillary_images else json.loads(next(raw for name, _, raw in self.ancillary_images if name == 'joint_recovery'))['state']

    @property
    def pending_operation_ids(self):
        return self.core_pending_operation_ids if not self.ancillary_images else ('joint:' + self.joint_state['proof_id'],)

    @property
    def scope(self):
        return 'JOINT_ACCOUNTING_APPLYING' if self.ancillary_images else 'CORE_JOINT_CASH_APPLYING'

    def manifest(self):
        result = {'format': 2 if self.ancillary_images else 1, 'scope': self.scope, 'cut': self.cut.json(),
                  'pending_operation_ids': list(self.pending_operation_ids),
                  'checkpoint_sha256': hashlib.sha256(self.checkpoint_bytes).hexdigest(),
                  'journals': {name: hashlib.sha256(raw).hexdigest() for name, raw in self.journal_images}}
        if self.ancillary_images:
            result['ancillary'] = [[name, path, hashlib.sha256(raw).hexdigest()] for name, path, raw in self.ancillary_images]
        return result

    @property
    def content_digest(self):
        return fingerprint(self.manifest())

    def pack(self):
        result = {'format': 2 if self.ancillary_images else 1, 'cut': self.cut.json(), 'checkpoint': base64.b64encode(self.checkpoint_bytes).decode(),
                  'journals': [[name, base64.b64encode(raw).decode()] for name, raw in self.journal_images],
                  'facts': self.facts.json()}
        if self.ancillary_images:
            result['ancillary'] = [[name, path, base64.b64encode(raw).decode()] for name, path, raw in self.ancillary_images]
        return encode(result)

    @classmethod
    def unpack(cls, raw):
        try:
            value = json.loads(raw)
            expected = {'format', 'cut', 'checkpoint', 'journals', 'facts'} | ({'ancillary'} if value['format'] == 2 else set())
            if set(value) != expected or type(value['format']) is not int or value['format'] not in (1, 2):
                raise ValueError()
            result = cls(FinancialCut.from_json(value['cut']), base64.b64decode(value['checkpoint'], validate=True),
                         tuple((name, base64.b64decode(data, validate=True)) for name, data in value['journals']),
                         SpotFinancialRecoveryFacts.from_json(value['facts']),
                         tuple((name, path, base64.b64decode(raw, validate=True)) for name, path, raw in value.get('ancillary', ())))
            if result.pack() != raw:
                raise ValueError()
            return result
        except (ValueError, KeyError, TypeError, StorageError):
            raise StorageUnavailable('LEGACY_APPLYING_ARTIFACT_INVALID') from None


@dataclass(frozen=True)
class RetireLegacyApplying:
    operation_id: str
    source_digest: str
    target_binding: StateBinding
    target_database_lineage: UUID

    def __post_init__(self):
        identifier(self.operation_id)
        digest(self.source_digest)
        if (type(self.target_binding) is not StateBinding or type(self.target_database_lineage) is not UUID
                or not self.target_database_lineage.int):
            raise StorageError('LEGACY_APPLYING_TARGET_INVALID')

    def json(self):
        return {'kind': 'RETIRE_LEGACY_APPLYING', 'operation_id': self.operation_id, 'source_digest': self.source_digest,
                'target_binding': self.target_binding.json(), 'target_database_lineage': str(self.target_database_lineage)}

    @classmethod
    def from_json(cls, raw):
        data = thaw(raw)
        if data.pop('kind') != 'RETIRE_LEGACY_APPLYING':
            raise StorageError('LEGACY_APPLYING_COMMAND_INVALID')
        data['target_binding'] = StateBinding.from_json(data['target_binding'])
        data['target_database_lineage'] = UUID(data['target_database_lineage'])
        return cls(**data)

    def validate_source(self, binding):
        if (self.target_binding.lineage == binding.lineage
                or any(getattr(self.target_binding, k) != getattr(binding, k) for k in (
                    'environment', 'exchange', 'account_uid', 'risk_pool', 'schema_version', 'policy_hash', 'config_hash'))):
            raise StorageError('LEGACY_APPLYING_TARGET_INVALID')


@dataclass(frozen=True)
class LegacyApplyingRetirementReceipt:
    operation_id: str
    source_digest: str
    replayed: bool
    state: str = field(default='RETIRED', init=False)
    trading_permission: bool = field(default=False, init=False)


def retirement_projection(payload, ownership, descriptor):
    command = RetireLegacyApplying.from_json(payload)
    if ownership['mode'] not in ('DRAINING', 'RECOVERY_ONLY') or ownership['token'] is None:
        raise StorageError('LEGACY_APPLYING_DRAIN_REQUIRED')
    if (set(descriptor) != {'operation_id', 'source_digest', 'artifact_sha256'}
            or descriptor['operation_id'] != command.operation_id or descriptor['source_digest'] != command.source_digest):
        raise StorageError('LEGACY_APPLYING_RETIREMENT_UNVERIFIED')
    digest(descriptor['artifact_sha256'])
    return command


class LegacyApplyingAdapter:
    def _before_call(self, function, args):
        if getattr(self, '_legacy_applying_retired', False):
            allowed = ('_read', '_snapshot', '_read_frozen_applying', '_retire_legacy_applying')
            if (getattr(function, '__self__', None) is not self or not any(
                    getattr(function, '__func__', None) is getattr(getattr(self, name), '__func__', None) for name in allowed)):
                raise StorageError('LEGACY_APPLYING_SOURCE_RETIRED')
            self._read_frozen_applying()
        super()._before_call(function, args)

    def _applying_source(self, facts, cut, joint=None):
        self.assert_worker_thread()
        if self._operator_keys is None or self._recovery_policy is None:
            raise StorageError('LEGACY_APPLYING_CONTROL_REQUIRED')
        self._assert_binding(facts.binding)
        self._assert_binding(cut.checkpoint.binding)
        view, meta = self._snapshot(), self._read()
        if cut != view.cut:
            raise StorageConflict('SPOT_CUT_STALE')
        if meta.ownership.mode not in (OwnerMode.DRAINING, OwnerMode.RECOVERY_ONLY):
            raise StorageError('LEGACY_APPLYING_DRAIN_REQUIRED')
        if meta.resources.get('legacy_applying_retirement') is not None:
            raise StorageError('LEGACY_APPLYING_SOURCE_RETIRED')
        ancillary = ()
        if 'joint_repository_policy' in meta.resources:
            from hummingbot.strategy_v2.life_liquidity.storage.legacy_joint_applying import capture_images
            if joint is None:
                raise StorageError('LEGACY_APPLYING_ANCILLARY_CAPTURE_REQUIRED')
            ancillary = capture_images(self, joint)
        elif joint is not None:
            raise StorageError('LEGACY_JOINT_APPLYING_REPOSITORY_REQUIRED')
        now = utc(self._clock())
        if (not 0 <= (now - facts.observed_at).total_seconds() * 1000 <= self._recovery_policy.max_fact_age_ms
                or not facts.order_scope_complete or not facts.bill_scope_complete or not facts.runner_scope_complete
                or facts.liability_base_life != 0 or facts.liability_quote_usdt != 0):
            raise StorageError('LEGACY_APPLYING_FACTS_UNQUALIFIED')
        # Validate the actual partial wallet/loss images using the existing saga
        # projection, stopping before its first financial or coordinator write.
        pending = [row for row in meta.resources.get('joint_cash_operations', {}).values() if row['state'] == 'APPLYING']
        if len(pending) > 1 or not pending and not ancillary:
            raise StorageError('LEGACY_APPLYING_SINGLE_CORE_SAGA_REQUIRED')
        if pending:
            row = pending[0]
            command = JointCashSpot.from_json(row['command'])
            if (facts.account_base_life, facts.account_quote_usdt) != (command.account_base_life, command.account_quote_usdt):
                raise StorageError('LEGACY_APPLYING_FACTS_UNQUALIFIED')
            with localcontext(Context(prec=28, rounding=ROUND_HALF_EVEN)):
                self._apply_joint_cash(command, FinancialCut.from_json(row['cut']), OwnerToken.from_json(row['owner']),
                                       resume=True, preflight_only=True)
        else:
            # Outer APPLYING may follow a complete cash receipt or precede its
            # first write. Verify the completed core money independently of
            # potentially changed outer WAL/claim component hashes.
            self._verify_joint_cash_projection(meta.resources)
            self._verify_capital_projection(meta.resources['commands'], meta.resources.get('fills', {}),
                                            meta.resources.get('cashflows', {}), meta.resources.get('joint_cashflows', {}))
        self._check_file()
        checkpoint = read_bytes(self._path, self.limits.max_checkpoint_bytes)
        source = LegacyApplyingSource(cut, checkpoint, tuple(sorted(self._raw().items())), facts, ancillary)
        if json.loads(checkpoint)['policy'] != self._policy:
            raise StorageError('LEGACY_APPLYING_POLICY_MISMATCH')
        if self._snapshot().cut != cut:
            raise StorageConflict('LEGACY_APPLYING_SOURCE_CHANGED')
        return source

    async def legacy_applying_source(self, binding, *, facts, cut, joint=None):
        self._assert_binding(binding)
        if type(facts) is not SpotFinancialRecoveryFacts or type(cut) is not FinancialCut:
            raise StorageError('LEGACY_APPLYING_SOURCE_INVALID')
        return await self._call(self._applying_source, facts, cut, joint)

    def _artifact(self, command):
        folder = self._path.parent / 'legacy_applying_sources'
        if folder.is_symlink() or folder.exists() and not folder.is_dir():
            raise StorageUnavailable('LEGACY_APPLYING_ARTIFACT_UNSAFE')
        return folder, folder / (command.source_digest + '.json')

    def _persist_applying(self, command, source):
        folder, destination = self._artifact(command)
        if not folder.exists():
            folder.mkdir(mode=0o700)
            sync_dir(folder.parent)
        raw, maximum = source.pack(), 4 * self.limits.max_checkpoint_bytes + 65536
        if len(raw) > maximum:
            raise StorageUnavailable('LEGACY_APPLYING_ARTIFACT_BOUND')
        if destination.exists() or destination.is_symlink():
            if read_bytes(destination, maximum) != raw:
                raise StorageUnavailable('LEGACY_APPLYING_ARTIFACT_CONFLICT')
        else:
            with os.scandir(folder) as entries:
                for count, entry in enumerate(entries, 1):
                    if count >= self.limits.max_operations or entry.is_symlink() or not entry.is_file():
                        raise StorageUnavailable('LEGACY_APPLYING_ARTIFACT_BOUND')
            descriptor, temporary = tempfile.mkstemp(prefix='.applying-', dir=folder)
            try:
                with os.fdopen(descriptor, 'wb') as handle:
                    handle.write(raw)
                    handle.flush()
                    os.fsync(handle.fileno())
                try:
                    os.link(temporary, destination)
                except FileExistsError:
                    if read_bytes(destination, maximum) != raw:
                        raise StorageUnavailable('LEGACY_APPLYING_ARTIFACT_CONFLICT')
                os.unlink(temporary)
                sync_dir(folder)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        with destination.open('rb') as handle:
            os.fsync(handle.fileno())
        sync_dir(folder)
        return {'operation_id': command.operation_id, 'source_digest': source.content_digest,
                'artifact_sha256': hashlib.sha256(raw).hexdigest()}

    def _verify_applying_images(self, source):
        if type(source) is not LegacyApplyingSource:
            raise StorageError('LEGACY_APPLYING_PHYSICAL_SOURCE_REQUIRED')
        if self._hashes(self._raw()) != dict(source.cut.components):
            raise StorageConflict('LEGACY_APPLYING_SOURCE_CHANGED')
        for _, path, expected_bytes in source.ancillary_images:
            try:
                if read_bytes(Path(path), len(expected_bytes)) != expected_bytes:
                    raise StorageUnavailable('LEGACY_JOINT_APPLYING_SOURCE_CHANGED')
            except (OSError, StorageError):
                raise StorageUnavailable('LEGACY_JOINT_APPLYING_SOURCE_CHANGED') from None

    def _read_frozen_applying(self):
        meta = self._read()
        descriptor = meta.resources.get('legacy_applying_retirement')
        if descriptor is None:
            raise StorageError('LEGACY_APPLYING_RETIREMENT_REQUIRED')
        record = meta.resources['control_operations'][descriptor['operation_id']]
        command = RetireLegacyApplying.from_json(record['payload'])
        _, destination = self._artifact(command)
        raw = read_bytes(destination, 4 * self.limits.max_checkpoint_bytes + 65536)
        source = LegacyApplyingSource.unpack(raw)
        if (json.loads(source.checkpoint_bytes)['policy'] != self._policy
                or hashlib.sha256(raw).hexdigest() != descriptor['artifact_sha256'] or source.content_digest != command.source_digest
                or source.cut.json() != thaw(record['cut']) or source.facts.json() != thaw(record['facts'])
                or self._hashes(self._raw()) != dict(source.cut.components)):
            raise StorageUnavailable('LEGACY_APPLYING_ARTIFACT_ORIGIN_INVALID')
        self._verify_applying_images(source)
        # A valid retirement signature seals the entire preceding coordinator,
        # not just its monetary component hashes. No later resource/operation
        # edits can masquerade as the original retired source.
        expected = json.loads(source.checkpoint_bytes)['state']
        expected['resources'].setdefault('control_operations', {})[command.operation_id] = thaw(record)
        expected['resources']['legacy_applying_retirement'] = thaw(descriptor)
        for name in ('control_operations', 'legacy_applying_retirement'):
            expected['versions'][name] = expected['versions'].get(name, 0) + 1
        prior_checked_at = expected['checked_at']
        expected['revision'] += 1
        expected['event_version'] += 1
        expected['operations']['control:' + command.operation_id] = {
            'fingerprint': fingerprint({'approval': thaw(record['approval']), 'payload': command.json()}),
            'revision': expected['revision']}
        with self._state.inspect() as actual:
            expected['checked_at'] = actual['checked_at']
            try:
                published = utc(datetime.fromisoformat(actual['checked_at']))
                if (not datetime.fromisoformat(record['approval']['issued_at']) <= published
                        < datetime.fromisoformat(record['approval']['expires_at'])
                        or prior_checked_at is not None and published < datetime.fromisoformat(prior_checked_at)
                        or not 0 <= (published - source.facts.observed_at).total_seconds() * 1000 <= self._recovery_policy.max_fact_age_ms
                        or thaw(actual) != expected):
                    raise ValueError()
            except (ValueError, TypeError, KeyError):
                raise StorageUnavailable('LEGACY_APPLYING_RETIRED_IMAGE_CHANGED') from None
        return source

    async def frozen_legacy_applying_source(self, binding):
        self._assert_binding(binding)
        return await self._call(self._read_frozen_applying)

    def _retire_legacy_applying(self, command, source, approval, joint=None):
        meta = self._read()
        previous = meta.resources.get('control_operations', {}).get(command.operation_id)
        if previous is not None:
            if (thaw(previous['payload']) != command.json() or previous['approval'] != approval.body()
                    or previous['signature'] != approval.signature or self._read_frozen_applying() != source):
                raise StorageConflict('LEGACY_APPLYING_RETIREMENT_CONFLICT')
            return LegacyApplyingRetirementReceipt(command.operation_id, source.content_digest, True)
        if getattr(self, '_legacy_applying_retired', False):
            raise StorageError('LEGACY_APPLYING_SOURCE_RETIRED')
        command.validate_source(self.binding)
        if source.content_digest != command.source_digest:
            raise StorageConflict('LEGACY_APPLYING_SOURCE_CHANGED')
        current = self._applying_source(source.facts, source.cut, joint)
        if current != source:
            raise StorageConflict('LEGACY_APPLYING_SOURCE_CHANGED')
        self._operator_keys.verify(approval, self.binding, 'IMPORT', command.json(), source.cut, source.facts, utc(self._clock()))
        descriptor = self._persist_applying(command, source)
        # Disk publication may outlive either freshness window; revalidate on the
        # same owning worker before the irreversible coordinator publication.
        if self._applying_source(source.facts, source.cut, joint) != source:
            raise StorageConflict('LEGACY_APPLYING_SOURCE_CHANGED')
        ownership = {'mode': meta.ownership.mode.value, 'token': meta.ownership.token.json()}
        self._control_commit(command.operation_id, source.cut, approval, command.json(), ownership, source.facts,
                             {'legacy_applying_retirement': descriptor}, applying_source=source)
        self._legacy_applying_retired, self._cold_unarmed, self._observation_generation = True, True, None
        try:
            self._read_frozen_applying()
        except BaseException:
            self._uncertain = True
            raise CommitUnknown('LEGACY_APPLYING_RETIREMENT_UNKNOWN') from None
        return LegacyApplyingRetirementReceipt(command.operation_id, source.content_digest, False)

    async def retire_legacy_applying(self, command, *, source, approval, joint=None):
        if type(command) is not RetireLegacyApplying or type(source) is not LegacyApplyingSource or type(approval) is not OperatorApproval:
            raise StorageError('LEGACY_APPLYING_COMMAND_INVALID')
        try:
            return await self._call(self._retire_legacy_applying, command, source, approval, joint)
        except OSError:
            raise StorageUnavailable('LEGACY_APPLYING_ARTIFACT_PUBLICATION_FAILED') from None
        except asyncio.CancelledError:
            self._uncertain = True
            raise CommitUnknown('LEGACY_APPLYING_RETIREMENT_UNKNOWN') from None
