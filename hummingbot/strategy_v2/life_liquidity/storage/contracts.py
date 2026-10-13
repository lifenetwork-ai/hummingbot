"""Backend-neutral checkpoint contracts. Snapshots/cuts never grant order permission."""
import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from enum import Enum
from types import MappingProxyType
from typing import Mapping, Protocol
from uuid import UUID


class StorageError(RuntimeError):
    """Stable reason code; never include stored payloads in error messages."""

    def __init__(self, code='STORAGE_INVALID'):
        self.code = code
        super().__init__(code)


class StorageConflict(StorageError):
    pass


class StorageUnavailable(StorageError):
    pass


class CommitUnknown(StorageUnavailable):
    """Publication may have committed; fresh operation readback is required."""


class UnsupportedBackend(StorageError):
    pass


class Backend(str, Enum):
    FILE = 'file'
    POSTGRES = 'postgres'


class AtomicScope(str, Enum):
    CHECKPOINT = 'checkpoint'
    ACCOUNT = 'account'
    RECOVERABLE_SAGA = 'recoverable_saga'


class OwnerMode(str, Enum):
    UNARMED = 'UNARMED'
    ACTIVE = 'ACTIVE'
    DRAINING = 'DRAINING'
    RECOVERY_ONLY = 'RECOVERY_ONLY'


def identifier(value):
    if type(value) is not str or re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', value) is None:
        raise StorageError('STORAGE_IDENTITY_INVALID')
    return value


def digest(value):
    if type(value) is not str or re.fullmatch(r'[0-9a-f]{64}', value) is None:
        raise StorageError('STORAGE_DIGEST_INVALID')


def utc(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise StorageError('STORAGE_TIME_INVALID')
    return value


def thaw(value):
    """Copy the deliberately restricted JSON domain; financial decimals use strings.

    Dedicated financial repositories must additionally enforce Decimal precision,
    units, transitions and economic limits. This generic port is not that policy.
    """
    if value is None or type(value) in (bool, int, str):
        return value
    if isinstance(value, Mapping):
        if any(type(key) is not str for key in value):
            raise StorageError('STORAGE_PAYLOAD_INVALID')
        return {key: thaw(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [thaw(item) for item in value]
    raise StorageError('STORAGE_PAYLOAD_INVALID')


def freeze(value):
    value = thaw(value)
    if isinstance(value, dict):
        return MappingProxyType({key: freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(freeze(item) for item in value)
    return value


def encode(value):
    return json.dumps(thaw(value), sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def fingerprint(value):
    return hashlib.sha256(encode(value)).hexdigest()


@dataclass(frozen=True)
class StateBinding:
    environment: str
    exchange: str
    account_uid: str
    risk_pool: str
    lineage: UUID
    schema_version: int
    policy_hash: str
    config_hash: str

    def __post_init__(self):
        if self.environment not in {'OFFLINE_SIMULATION', 'DEMO', 'PRODUCTION'} or self.exchange != 'okx':
            raise StorageError('STORAGE_NAMESPACE_INVALID')
        if type(self.account_uid) is not str or re.fullmatch(r'[0-9]{1,64}', self.account_uid) is None:
            raise StorageError('STORAGE_ACCOUNT_INVALID')
        identifier(self.risk_pool)
        if type(self.lineage) is not UUID or self.lineage.int == 0:
            raise StorageError('STORAGE_LINEAGE_INVALID')
        if type(self.schema_version) is not int or self.schema_version < 1:
            raise StorageError('STORAGE_SCHEMA_INVALID')
        digest(self.policy_hash)
        digest(self.config_hash)

    def json(self):
        return {**asdict(self), 'lineage': str(self.lineage)}

    @classmethod
    def from_json(cls, value):
        data = dict(value)
        data['lineage'] = UUID(data['lineage'])
        return cls(**data)


@dataclass(frozen=True)
class ResourceVersion:
    resource: str
    version: int

    def __post_init__(self):
        identifier(self.resource)
        if type(self.version) is not int or self.version < 0:
            raise StorageError('STORAGE_VERSION_INVALID')


@dataclass(frozen=True)
class StateCut:
    binding: StateBinding
    revision: int
    event_version: int
    resource_versions: tuple[ResourceVersion, ...]
    state_digest: str

    def __post_init__(self):
        if not isinstance(self.binding, StateBinding) or any(type(v) is not int or v < 0 for v in (
                self.revision, self.event_version)):
            raise StorageError('STORAGE_CUT_INVALID')
        if (type(self.resource_versions) is not tuple
                or any(not isinstance(v, ResourceVersion) for v in self.resource_versions)
                or len({v.resource for v in self.resource_versions}) != len(self.resource_versions)):
            raise StorageError('STORAGE_CUT_INVALID')
        digest(self.state_digest)

    def json(self):
        return {'binding': self.binding.json(), 'revision': self.revision, 'event_version': self.event_version,
                'resource_versions': [asdict(v) for v in self.resource_versions], 'state_digest': self.state_digest}

    @classmethod
    def from_json(cls, value):
        data = dict(value)
        data['binding'] = StateBinding.from_json(data['binding'])
        data['resource_versions'] = tuple(ResourceVersion(**row) for row in data['resource_versions'])
        return cls(**data)


@dataclass(frozen=True)
class OwnerToken:
    """Expected owner facts for a command; not an activation or send grant."""
    process_id: UUID
    generation: UUID
    epoch: int
    session_id: UUID
    valid_from: datetime
    expires_at: datetime

    def __post_init__(self):
        if any(type(v) is not UUID or v.int == 0 for v in (self.process_id, self.generation, self.session_id)):
            raise StorageError('STORAGE_OWNER_INVALID')
        if type(self.epoch) is not int or self.epoch < 1:
            raise StorageError('STORAGE_OWNER_INVALID')
        utc(self.valid_from)
        utc(self.expires_at)
        if self.expires_at <= self.valid_from:
            raise StorageError('STORAGE_OWNER_TIME_INVALID')

    def json(self):
        return {'process_id': str(self.process_id), 'generation': str(self.generation), 'epoch': self.epoch,
                'session_id': str(self.session_id), 'valid_from': self.valid_from.isoformat(),
                'expires_at': self.expires_at.isoformat()}

    @classmethod
    def from_json(cls, value):
        data = dict(value)
        for key in ('process_id', 'generation', 'session_id'):
            data[key] = UUID(data[key])
        for key in ('valid_from', 'expires_at'):
            data[key] = datetime.fromisoformat(data[key])
        return cls(**data)


@dataclass(frozen=True)
class OwnershipSnapshot:
    binding: StateBinding
    mode: OwnerMode
    token: OwnerToken | None

    def __post_init__(self):
        if (not isinstance(self.binding, StateBinding) or not isinstance(self.mode, OwnerMode)
                or self.token is not None and not isinstance(self.token, OwnerToken)
                or self.mode is OwnerMode.ACTIVE and self.token is None):
            raise StorageError('STORAGE_OWNER_INVALID')


@dataclass(frozen=True)
class StateSnapshot:
    cut: StateCut
    resources: Mapping
    ownership: OwnershipSnapshot

    def __post_init__(self):
        if not isinstance(self.cut, StateCut) or not isinstance(self.resources, Mapping):
            raise StorageError('STORAGE_SNAPSHOT_INVALID')
        object.__setattr__(self, 'resources', freeze(self.resources))
        if self.ownership.binding != self.cut.binding:
            raise StorageError('STORAGE_BINDING_MISMATCH')

    @property
    def owner_mode(self):
        return self.ownership.mode


@dataclass(frozen=True)
class CommitReceipt:
    binding: StateBinding
    operation_id: str
    revision: int
    replayed: bool

    def __post_init__(self):
        if not isinstance(self.binding, StateBinding):
            raise StorageError('STORAGE_RECEIPT_INVALID')
        identifier(self.operation_id)
        if type(self.revision) is not int or self.revision < 1 or type(self.replayed) is not bool:
            raise StorageError('STORAGE_RECEIPT_INVALID')


class UnitOfWork(Protocol):
    """Stage immutable updates within the adapter's explicitly declared atomic scope.

    commit() is storage publication, not external send or reservation release.
    Exceptions after publication may be CommitUnknown. rollback() only discards
    uncommitted staged work; it cannot undo a begun/unknown/durable commit.
    """
    atomic_scope: AtomicScope

    def put(self, resource: str, value) -> None:
        ...

    def rollback(self) -> None:
        ...

    async def commit(self, operation_id: str) -> CommitReceipt:
        ...


class StateRepository(Protocol):
    backend: Backend
    atomic_scope: AtomicScope

    async def read(self, binding: StateBinding) -> StateSnapshot:
        ...

    async def lookup_operation(self, operation_id: str, binding: StateBinding) -> CommitReceipt | None:
        ...

    def unit_of_work(self, cut: StateCut, owner: OwnerToken) -> UnitOfWork:
        ...

    async def close(self) -> None:
        ...


class OwnershipRepository(Protocol):
    """Read storage-bound owner facts; implementations do not fence exchange keys."""

    async def current_owner(self, binding: StateBinding) -> OwnershipSnapshot:
        ...
