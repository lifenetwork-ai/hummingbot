"""Host-local atomic risk checkpoints with immutable policy and writer fencing."""

import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path

from hummingbot.strategy_v2.life_liquidity.checkpoint_archive import CheckpointArchive, decode


class _FrozenDict(dict):
    def _deny(self, *args, **kwargs):
        raise TypeError("verified state is read-only")
    __setitem__ = __delitem__ = clear = pop = popitem = setdefault = update = __ior__ = _deny

    def __deepcopy__(self, memo):
        import copy
        return {key: copy.deepcopy(value, memo) for key, value in self.items()}

    def __reduce__(self):
        return _freeze, (dict(self),)


class _FrozenList(list):
    def _deny(self, *args, **kwargs):
        raise TypeError("verified state is read-only")
    __setitem__ = __delitem__ = append = clear = extend = insert = pop = remove = reverse = sort = __iadd__ = __imul__ = _deny

    def __deepcopy__(self, memo):
        import copy
        return [copy.deepcopy(value, memo) for value in self]

    def __reduce__(self):
        return _freeze, (list(self),)


def _freeze(value):
    if isinstance(value, (_FrozenDict, _FrozenList)):
        return value
    if isinstance(value, dict):
        return _FrozenDict({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return _FrozenList(_freeze(item) for item in value)
    return value


class PolicyState:
    def __init__(self, path: Path, *, policy: dict, initial: dict, create: bool, archive=None):
        if type(create) is not bool or not isinstance(policy, dict) or not isinstance(initial, dict):
            raise ValueError("RISK_STATE_POLICY_INVALID")
        self.path = Path(path)
        self._policy_blob = json.dumps(policy, sort_keys=True)
        self._state = json.loads(json.dumps(initial))
        self._uncertain = False
        self._locked = False
        self.archive = None if archive is None else CheckpointArchive(self.path, archive)
        self._archive_authority = self.archive
        self._archive_policy_blob = None if archive is None else json.dumps(archive.json(), sort_keys=True)
        with self._file_lock():
            if create:
                if self.path.exists() or self.path.is_symlink():
                    raise ValueError("RISK_STATE_EXISTS_USE_RESTORE")
                self._write(self._state)
            else:
                self._state = _freeze(self._read())

    @property
    def policy(self):
        return json.loads(self._policy_blob)

    def _raw(self):
        if (self.archive is not self._archive_authority
                or self.archive is not None and (
                    json.dumps(self.archive.policy.json(), sort_keys=True) != self._archive_policy_blob
                    or self.archive.directory != self.path.with_name(self.path.name + ".segments"))):
            raise ValueError("RISK_STATE_STALE_OR_UNCERTAIN")
        return self.path.read_bytes() if self.archive is None else self.archive.read_manifest(self.path)

    @contextmanager
    def _file_lock(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.path.with_name(self.path.name + ".lock"),
                             os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def _read(self):
        if not self.path.is_file() or self.path.is_symlink():
            raise ValueError("RISK_STATE_UNAVAILABLE")
        raw = self._raw()
        data = decode(raw)
        if (not isinstance(data, dict) or data.get("policy") != self.policy
                or not isinstance(data.get("state"), dict)):
            raise ValueError("RISK_STATE_POLICY_MISMATCH")
        if data.get("schema_version") == 2:
            if self.archive is None:
                raise ValueError("ARCHIVE_POLICY_REQUIRED")
            return self.archive.read(raw, freeze=_freeze)
        if self.archive is not None or data.get("schema_version") != 1:
            raise ValueError("ARCHIVE_POLICY_MISMATCH")
        return data["state"]

    def _verified(self):
        if self._uncertain or not self.path.is_file() or self.path.is_symlink():
            raise ValueError("RISK_STATE_STALE_OR_UNCERTAIN")
        raw = self._raw()
        cache = getattr(self, "_verified_image", None)
        if self.archive is not None:
            durable = self.archive.read(raw, freeze=_freeze)
            if self._raw() != raw:
                raise ValueError("RISK_STATE_STALE_OR_UNCERTAIN")
            if cache is None or cache[0] != raw or cache[2] != self._policy_blob:
                if decode(raw)["policy"] != self.policy:
                    raise ValueError("RISK_STATE_POLICY_MISMATCH")
                cache = (raw, durable, self._policy_blob)
                self._verified_image = cache
        if cache is None or cache[0] != raw or cache[2] != self._policy_blob:
            durable = _freeze(self._read())
            if self._raw() != raw:
                raise ValueError("RISK_STATE_STALE_OR_UNCERTAIN")
            cache = (raw, durable, self._policy_blob)
            self._verified_image = cache
        # Both may be the very same recursively immutable verified image.
        # Different objects still need the complete logical comparison.
        if cache[1] is not self._state and cache[1] != self._state:
            raise ValueError("RISK_STATE_STALE_OR_UNCERTAIN")
        return cache

    def assert_current(self):
        """Fence root/memory changes inside an already verified locked scope.

        This is not segment verification or allocation authority. Scope entry
        verifies historical bytes; publication verifies its resulting cut.
        """
        if not self._locked:
            raise ValueError("RISK_STATE_LOCK_REQUIRED")
        cache = getattr(self, "_verified_image", None)
        if (self._uncertain or cache is None or not self.path.is_file() or self.path.is_symlink()
                or cache[2] != self._policy_blob or self._raw() != cache[0]
                or cache[1] is not self._state and cache[1] != self._state):
            raise ValueError("RISK_STATE_STALE_OR_UNCERTAIN")

    @contextmanager
    def inspect(self):
        """Exact-byte checked, immutable view; cannot commit or change a checkpoint."""
        if self._locked:
            raise ValueError("RISK_STATE_NESTED_LOCK")
        with self._file_lock():
            cache = self._verified()
            if getattr(self, "_frozen_image", (None,))[0] is not cache:
                self._frozen_image = (cache, cache[1] if isinstance(cache[1], _FrozenDict) else _freeze(cache[1]))
            self._locked = True
            self._read_only = True
            try:
                yield self._frozen_image[1]
            finally:
                self._read_only = False
                self._locked = False

    @contextmanager
    def locked(self, *, shallow=False, observe=None):
        """Optionally read external facts before verification under the same lock.

        The observer receives no checkpoint and cannot publish. Its result
        grants no authority: all durable bytes and memory are verified after
        it returns, before the caller receives any checkpoint state.
        """
        if self._locked:
            raise ValueError("RISK_STATE_NESTED_LOCK")
        with self._file_lock():
            if observe is not None:
                self._locked = self._read_only = True
                try:
                    observe()
                finally:
                    self._locked = self._read_only = False
            self._verified()
            self._locked = True
            try:
                if shallow:
                    cache = self._verified_image
                    if getattr(self, "_frozen_image", (None,))[0] is not cache:
                        self._frozen_image = (cache, cache[1] if isinstance(cache[1], _FrozenDict) else _freeze(cache[1]))
                    yield dict(self._frozen_image[1])
                else:
                    yield json.loads(json.dumps(self._state))
            finally:
                self._locked = False

    def _write(self, state):
        if self.archive is None:
            payload = json.dumps({"schema_version": 1, "policy": self.policy, "state": state},
                                 sort_keys=True, separators=(",", ":")).encode()
        else:
            payload, pending = self.archive.prepare(state, self.policy)
            self.archive.publish_segments(pending)
        descriptor, temporary = tempfile.mkstemp(prefix="." + self.path.name, dir=self.path.parent)
        replace_attempted = False
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            replace_attempted = True
            os.replace(temporary, self.path)
            directory = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
            # Independent decoded image: caller mutations cannot poison it.
            durable = (_freeze(decode(payload)["state"]) if self.archive is None
                       else self.archive.read(payload, freeze=_freeze))
            self._verified_image = (payload, durable, self._policy_blob)
        except Exception:
            if replace_attempted:
                self._uncertain = True
            raise
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def commit(self, state: dict):
        if not self._locked or getattr(self, "_read_only", False):
            raise ValueError("RISK_STATE_LOCK_REQUIRED")
        if state != self._state:
            self._write(state)
            # Sharing is safe only because the independently decoded image is
            # recursively immutable, including nested lists and dictionaries.
            self._state = self._verified_image[1]

    def migrate_archive(self, policy):
        """Explicit schema-1 → schema-2 publication under the existing writer lock.

        No retention capacity is inferred, and no old segment is removed.
        An interrupted publication restores either the legacy cut or the new
        complete manifest; ambiguous replacement fences the current instance.
        """
        if self._locked or self.archive is not None:
            raise ValueError("ARCHIVE_MIGRATION_UNSAFE")
        with self._file_lock():
            try:
                self._verified()
            except ValueError as exc:
                raise ValueError("RISK_STATE_STALE_OR_UNCERTAIN") from exc
            candidate = CheckpointArchive(self.path, policy)
            self.archive = candidate
            self._archive_authority = candidate
            self._archive_policy_blob = json.dumps(policy.json(), sort_keys=True)
            try:
                self._write(self._state)
                self._state = self._verified_image[1]
            except Exception:
                if not self._uncertain:
                    self.archive = None
                    self._archive_authority = None
                    self._archive_policy_blob = None
                raise
