"""Host-local atomic risk checkpoints with immutable policy and writer fencing."""

import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path


class _FrozenDict(dict):
    def _deny(self, *args, **kwargs):
        raise TypeError("verified state is read-only")
    __setitem__ = __delitem__ = clear = pop = popitem = setdefault = update = __ior__ = _deny

    def __deepcopy__(self, memo):
        import copy
        return {key: copy.deepcopy(value, memo) for key, value in self.items()}


class _FrozenList(list):
    def _deny(self, *args, **kwargs):
        raise TypeError("verified state is read-only")
    __setitem__ = __delitem__ = append = clear = extend = insert = pop = remove = reverse = sort = __iadd__ = __imul__ = _deny

    def __deepcopy__(self, memo):
        import copy
        return [copy.deepcopy(value, memo) for value in self]


def _freeze(value):
    if isinstance(value, dict):
        return _FrozenDict({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return _FrozenList(_freeze(item) for item in value)
    return value


class PolicyState:
    def __init__(self, path: Path, *, policy: dict, initial: dict, create: bool):
        if type(create) is not bool or not isinstance(policy, dict) or not isinstance(initial, dict):
            raise ValueError("RISK_STATE_POLICY_INVALID")
        self.path = Path(path)
        self._policy_blob = json.dumps(policy, sort_keys=True)
        self._state = json.loads(json.dumps(initial))
        self._uncertain = False
        self._locked = False
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
        data = json.loads(self.path.read_text())
        if (not isinstance(data, dict) or data.get("schema_version") != 1
                or data.get("policy") != self.policy or not isinstance(data.get("state"), dict)):
            raise ValueError("RISK_STATE_POLICY_MISMATCH")
        return data["state"]

    def _verified(self):
        if self._uncertain or not self.path.is_file() or self.path.is_symlink():
            raise ValueError("RISK_STATE_STALE_OR_UNCERTAIN")
        raw = self.path.read_bytes()
        cache = getattr(self, "_verified_image", None)
        if cache is None or cache[0] != raw or cache[2] != self._policy_blob:
            durable = _freeze(self._read())
            if self.path.read_bytes() != raw:
                raise ValueError("RISK_STATE_STALE_OR_UNCERTAIN")
            cache = (raw, durable, self._policy_blob)
            self._verified_image = cache
        if cache[1] != self._state:
            raise ValueError("RISK_STATE_STALE_OR_UNCERTAIN")
        return cache

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
    def locked(self, *, shallow=False):
        if self._locked:
            raise ValueError("RISK_STATE_NESTED_LOCK")
        with self._file_lock():
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
        descriptor, temporary = tempfile.mkstemp(prefix="." + self.path.name, dir=self.path.parent)
        replace_attempted = False
        try:
            with os.fdopen(descriptor, "w") as handle:
                payload = json.dumps({"schema_version": 1, "policy": self.policy, "state": state},
                                     sort_keys=True, separators=(",", ":"))
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
            self._verified_image = (payload.encode(), _freeze(json.loads(payload)["state"]), self._policy_blob)
        except OSError:
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
