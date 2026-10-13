"""Async contracts on one existing PolicyState checkpoint, with bounded admission.

This is a new explicit offline aggregate format, not an import or atomic wrapper
around the controller's multiple existing journals. All PolicyState objects stay
on one dedicated worker. Caller cancellation retains an admitted operation until
its physical completion; no thread is killed and no rollback is inferred.
"""
import asyncio
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping
from uuid import UUID

from hummingbot.strategy_v2.life_liquidity.policy_state import PolicyState
from hummingbot.strategy_v2.life_liquidity.storage.contracts import (
    AtomicScope,
    Backend,
    CommitReceipt,
    CommitUnknown,
    OwnerMode,
    OwnershipSnapshot,
    OwnerToken,
    ResourceVersion,
    StateBinding,
    StateCut,
    StateRepository,
    StateSnapshot,
    StorageConflict,
    StorageError,
    StorageUnavailable,
    UnsupportedBackend,
    digest,
    encode,
    fingerprint,
    identifier,
    utc,
)


@dataclass(frozen=True)
class FileLimits:
    max_pending: int
    max_checkpoint_bytes: int
    max_operations: int

    def __post_init__(self):
        if any(type(v) is not int or v < 1 for v in (
                self.max_pending, self.max_checkpoint_bytes, self.max_operations)):
            raise StorageError('STORAGE_CAPACITY_INVALID')


class _FileUnitOfWork:
    atomic_scope = AtomicScope.CHECKPOINT

    def __init__(self, store, cut, owner):
        self._store, self._cut, self._owner = store, cut, owner
        self._changes, self._closed = {}, False

    def put(self, resource, value):
        if self._closed:
            raise StorageError('STORAGE_UOW_CLOSED')
        identifier(resource)
        self._changes[resource] = encode(value)  # Detach caller data before any await.

    def rollback(self):
        if self._closed:
            raise StorageError('STORAGE_UOW_CLOSED')
        self._closed = True
        self._changes.clear()

    async def commit(self, operation_id):
        if self._closed or not self._changes:
            raise StorageError('STORAGE_UOW_CLOSED_OR_EMPTY')
        identifier(operation_id)
        changes = tuple(sorted(self._changes.items()))
        self._closed = True  # No retry/rollback on this possibly begun instance.
        try:
            return await self._store._call(self._store._commit, self._cut, self._owner, operation_id, changes)
        except asyncio.CancelledError:
            # The admitted thread may already have published. Require cold readback.
            self._store._uncertain = True
            raise


class FileCheckpointStore:
    backend = Backend.FILE
    atomic_scope = AtomicScope.CHECKPOINT

    def __init__(self, path, binding, limits, clock):
        if not isinstance(binding, StateBinding) or not isinstance(limits, FileLimits):
            raise StorageError('STORAGE_CONFIG_INVALID')
        if binding.schema_version != 1:
            raise StorageError('STORAGE_SCHEMA_UNSUPPORTED')
        self._path, self._binding, self._limits = Path(path), binding, limits
        self._clock = clock
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='life-file-store')
        self._pending, self._closed, self._uncertain = set(), False, False
        self._worker_thread_id = None
        self._policy = {'storage_contract': 1, 'binding': binding.json(), 'capacity': {
            'max_checkpoint_bytes': limits.max_checkpoint_bytes, 'max_operations': limits.max_operations}}

    @property
    def binding(self):
        return self._binding

    @property
    def limits(self):
        return self._limits

    async def _call(self, function, *args):
        if self._closed or self._uncertain:
            raise StorageUnavailable('STORAGE_CLOSED_OR_UNCERTAIN')
        if len(self._pending) >= self.limits.max_pending:
            raise StorageUnavailable('STORAGE_BACKPRESSURE')

        def invoke():
            current = threading.get_ident()
            if self._worker_thread_id is None:
                self._worker_thread_id = current
            self.assert_worker_thread()
            return function(*args)
        future = asyncio.get_running_loop().run_in_executor(self._executor, invoke)
        self._pending.add(future)

        def completed(item):
            self._pending.discard(item)
            if not item.cancelled():
                item.exception()  # Observe exceptions even if the caller cancelled.

        future.add_done_callback(completed)
        return await asyncio.shield(future)

    def assert_worker_thread(self):
        if self._worker_thread_id is None or threading.get_ident() != self._worker_thread_id:
            raise StorageError('STORAGE_WORKER_REQUIRED')

    def _initialize(self, create, initial, owner):
        if type(create) is not bool:
            raise StorageError('STORAGE_CONFIG_INVALID')
        self._created_offline = create and self.binding.environment == 'OFFLINE_SIMULATION'
        if create and self.binding.environment != 'OFFLINE_SIMULATION':
            raise StorageError('STORAGE_BOOTSTRAP_OFFLINE_ONLY')
        if create and (initial is None or owner is not None and not isinstance(owner, OwnerToken)):
            raise StorageError('STORAGE_CONFIG_INVALID')
        resources = {} if initial is None else json.loads(initial)
        if not isinstance(resources, dict):
            raise StorageError('STORAGE_PAYLOAD_INVALID')
        for key in resources:
            identifier(key)
        data = {'revision': 0, 'event_version': 0, 'resources': resources,
                'versions': {key: 0 for key in resources}, 'operations': {},
                'checked_at': owner.valid_from.isoformat() if owner else None,
                'ownership': {'mode': OwnerMode.ACTIVE.value if owner else OwnerMode.UNARMED.value,
                              'token': owner.json() if owner else None}}
        self._capacity(data)
        if not create:
            self._check_file()
        try:
            self._state = PolicyState(self._path, policy=self._policy, initial=data, create=create)
            with self._state.inspect() as value:
                self._validate(value)
        except (OSError, ValueError, KeyError, TypeError):
            raise StorageUnavailable('STORAGE_FILE_UNVERIFIED') from None

    def _capacity(self, state):
        payload = {'schema_version': 1, 'policy': self._policy, 'state': state}
        if (len(encode(payload)) > self.limits.max_checkpoint_bytes
                or len(state['operations']) > self.limits.max_operations):
            raise StorageUnavailable('STORAGE_CAPACITY_EXCEEDED')

    def _check_file(self):
        try:
            if (not self._path.is_file() or self._path.is_symlink()
                    or self._path.stat().st_size > self.limits.max_checkpoint_bytes):
                raise StorageUnavailable('STORAGE_FILE_UNAVAILABLE')
        except OSError:
            raise StorageUnavailable('STORAGE_FILE_UNAVAILABLE') from None

    def _validate(self, state):
        if set(state) != {'revision', 'event_version', 'resources', 'versions', 'operations', 'ownership', 'checked_at'}:
            raise StorageUnavailable('STORAGE_CHECKPOINT_INVALID')
        if any(not isinstance(state[key], Mapping) for key in ('resources', 'versions', 'operations', 'ownership')):
            raise StorageUnavailable('STORAGE_CHECKPOINT_INVALID')
        for key in ('revision', 'event_version'):
            if type(state[key]) is not int or state[key] < 0:
                raise StorageUnavailable('STORAGE_CHECKPOINT_INVALID')
        if (state['revision'] != state['event_version'] or state['revision'] != len(state['operations'])
                or set(state['resources']) != set(state['versions'])):
            raise StorageUnavailable('STORAGE_CHECKPOINT_INVALID')
        for key, value in state['versions'].items():
            ResourceVersion(key, value)
            if value > state['revision']:
                raise StorageUnavailable('STORAGE_CHECKPOINT_INVALID')
        revisions = set()
        for key, record in state['operations'].items():
            identifier(key)
            if (not isinstance(record, Mapping) or set(record) != {'fingerprint', 'revision'} or type(record['revision']) is not int
                    or not 1 <= record['revision'] <= state['revision']):
                raise StorageUnavailable('STORAGE_CHECKPOINT_INVALID')
            if record['revision'] in revisions:
                raise StorageUnavailable('STORAGE_CHECKPOINT_INVALID')
            revisions.add(record['revision'])
            digest(record['fingerprint'])
        self._ownership(state)
        if state['checked_at'] is not None:
            utc(datetime.fromisoformat(state['checked_at']))
        self._capacity(state)

    def _ownership(self, state):
        raw = state['ownership']
        if set(raw) != {'mode', 'token'}:
            raise StorageUnavailable('STORAGE_OWNER_INVALID')
        mode = OwnerMode(raw['mode'])
        token = raw['token']
        if token is not None:
            if not isinstance(token, Mapping) or set(token) != {'process_id', 'generation', 'epoch', 'session_id', 'valid_from', 'expires_at'}:
                raise StorageUnavailable('STORAGE_OWNER_INVALID')
            token = OwnerToken(UUID(token['process_id']), UUID(token['generation']), token['epoch'],
                               UUID(token['session_id']), datetime.fromisoformat(token['valid_from']),
                               datetime.fromisoformat(token['expires_at']))
        if mode is OwnerMode.ACTIVE and token is None:
            raise StorageUnavailable('STORAGE_OWNER_INVALID')
        return OwnershipSnapshot(self.binding, mode, token)

    def _cut(self, state):
        return StateCut(self.binding, state['revision'], state['event_version'],
                        tuple(ResourceVersion(k, v) for k, v in sorted(state['versions'].items())), fingerprint(state))

    def _assert_binding(self, binding):
        if binding != self.binding:
            raise StorageError('STORAGE_BINDING_MISMATCH')

    def _read(self):
        self._check_file()
        try:
            with self._state.inspect() as state:
                self._validate(state)
                return StateSnapshot(self._cut(state), state['resources'], self._ownership(state))
        except (OSError, ValueError, KeyError, TypeError):
            raise StorageUnavailable('STORAGE_FILE_UNVERIFIED') from None

    async def read(self, binding):
        self._assert_binding(binding)
        return await self._call(self._read)

    async def current_owner(self, binding):
        return (await self.read(binding)).ownership

    def _lookup(self, operation_id):
        self._check_file()
        try:
            with self._state.inspect() as state:
                self._validate(state)
                record = state['operations'].get(operation_id)
                return None if record is None else CommitReceipt(self.binding, operation_id, record['revision'], True)
        except (OSError, ValueError, KeyError, TypeError):
            raise StorageUnavailable('STORAGE_FILE_UNVERIFIED') from None

    async def lookup_operation(self, operation_id, binding):
        self._assert_binding(binding)
        identifier(operation_id)
        return await self._call(self._lookup, operation_id)

    def unit_of_work(self, cut, owner):
        if not isinstance(cut, StateCut) or not isinstance(owner, OwnerToken):
            raise StorageError('STORAGE_UOW_INVALID')
        self._assert_binding(cut.binding)
        return _FileUnitOfWork(self, cut, owner)

    def _commit(self, cut, owner, operation_id, changes, *, observation=False):
        # Private observational publication for domain adapters only. Generic UoW
        # always uses the normal active/unexpired owner check. An observation can
        # persist late ACK/fill accounting without granting new-risk admission
        # or changing ownership. Qualified release is a separate domain command.
        if self._uncertain:
            raise StorageUnavailable('STORAGE_COMMIT_UNCERTAIN')
        self._check_file()
        values = {key: json.loads(raw) for key, raw in changes}
        command_digest = fingerprint({'cut': cut.json(), 'owner': owner.json(), 'changes': values})
        if observation:
            command_digest = fingerprint({'observation': True, 'payload': command_digest})
        try:
            with self._state.locked() as state:
                self._validate(state)
                actual_owner = self._ownership(state)
                now = utc(self._clock())  # Recheck after thread queue and file-lock waits.
                checked_at = state['checked_at']
                modes = (OwnerMode.ACTIVE, OwnerMode.DRAINING, OwnerMode.RECOVERY_ONLY) if observation else (OwnerMode.ACTIVE,)
                if (actual_owner.mode not in modes or actual_owner.token != owner
                        or now < owner.valid_from or not observation and now >= owner.expires_at
                        or checked_at is not None and now < datetime.fromisoformat(checked_at)):
                    raise StorageError('STORAGE_OWNER_REVOKED_OR_EXPIRED')
                record = state['operations'].get(operation_id)
                if record is not None:
                    if record['fingerprint'] != command_digest:
                        raise StorageConflict('STORAGE_OPERATION_CONFLICT')
                    return CommitReceipt(self.binding, operation_id, record['revision'], True)
                if self._cut(state) != cut:
                    raise StorageConflict('STORAGE_CUT_STALE')
                revision = state['revision'] + 1
                for key, value in values.items():
                    state['resources'][key] = value
                    state['versions'][key] = state['versions'].get(key, 0) + 1
                state['revision'] = state['event_version'] = revision
                state['checked_at'] = now.isoformat()
                state['operations'][operation_id] = {'fingerprint': command_digest, 'revision': revision}
                self._capacity(state)
                try:
                    self._state.commit(state)
                except BaseException:
                    self._uncertain = True
                    raise CommitUnknown('STORAGE_COMMIT_UNKNOWN') from None
                return CommitReceipt(self.binding, operation_id, revision, False)
        except (OSError, ValueError, KeyError, TypeError):
            raise StorageUnavailable('STORAGE_FILE_UNVERIFIED') from None

    async def close(self):
        self._closed = True
        try:
            if self._pending:
                await asyncio.shield(asyncio.gather(*tuple(self._pending), return_exceptions=True))
        finally:
            # Even cancelled close waits leave the worker scheduled to drain and exit.
            self._executor.shutdown(wait=False, cancel_futures=False)


async def open_store(backend, *, path, binding, limits, create=False, initial=None, owner=None,
                     clock=lambda: datetime.now(timezone.utc)) -> StateRepository:
    if backend is not Backend.FILE:
        raise UnsupportedBackend('STORAGE_BACKEND_UNSUPPORTED')
    store = FileCheckpointStore(path, binding, limits, clock)
    try:
        await store._call(store._initialize, create, None if initial is None else encode(initial), owner)
        return store
    except BaseException:
        await store.close()
        raise
