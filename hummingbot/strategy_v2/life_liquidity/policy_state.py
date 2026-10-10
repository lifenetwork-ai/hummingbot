"""Host-local atomic risk checkpoints with immutable policy and writer fencing."""

import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path


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
                self._state = self._read()

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

    @contextmanager
    def locked(self):
        if self._locked:
            raise ValueError("RISK_STATE_NESTED_LOCK")
        with self._file_lock():
            if self._uncertain or self._read() != self._state or self._locked:
                raise ValueError("RISK_STATE_STALE_OR_UNCERTAIN")
            self._locked = True
            try:
                yield json.loads(json.dumps(self._state))
            finally:
                self._locked = False

    def _write(self, state):
        descriptor, temporary = tempfile.mkstemp(prefix="." + self.path.name, dir=self.path.parent)
        replace_attempted = False
        try:
            with os.fdopen(descriptor, "w") as handle:
                json.dump({"schema_version": 1, "policy": self.policy, "state": state}, handle, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            replace_attempted = True
            os.replace(temporary, self.path)
            directory = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except OSError:
            if replace_attempted:
                self._uncertain = True
            raise
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def commit(self, state: dict):
        if not self._locked:
            raise ValueError("RISK_STATE_LOCK_REQUIRED")
        if state != self._state:
            copied = json.loads(json.dumps(state))
            self._write(copied)
            self._state = copied
