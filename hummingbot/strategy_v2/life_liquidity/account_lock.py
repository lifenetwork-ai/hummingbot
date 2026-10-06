"""Single-host ownership for a dedicated OKX LIFE risk account."""

import errno
import fcntl
import os
import re
import stat
import threading
from pathlib import Path

# All LIFE runner IDs under one OS user use the same root. Do not put this under
# a per-strategy recovery directory: different IDs must contend for one UID.
ACCOUNT_LOCK_ROOT = Path.home() / ".hummingbot" / "life_liquidity" / "account_locks"
_guard = threading.Lock()
_owned_paths: set[Path] = set()


class AccountLockUnavailable(RuntimeError):
    pass


class AccountRiskPoolLock:
    def __init__(self, uid: str, root: Path = ACCOUNT_LOCK_ROOT):
        if not isinstance(uid, str) or re.fullmatch(r"[0-9]+", uid) is None:
            raise ValueError("ACCOUNT_UID_INVALID")
        root = Path(root)
        if not root.is_absolute():
            raise ValueError("ACCOUNT_LOCK_ROOT_INVALID")
        self.path = root / f"okx-{uid}.lock"
        self._fd: int | None = None

    def acquire(self) -> None:
        if self._fd is not None:
            return
        root = self.path.parent
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if root.is_symlink() or not root.is_dir():
            raise AccountLockUnavailable("ACCOUNT_LOCK_ROOT_UNSAFE")
        with _guard:
            if self.path in _owned_paths:
                raise AccountLockUnavailable("ACCOUNT_LOCK_HELD_ELSEWHERE")
            flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(self.path, flags, 0o600)
            try:
                file_stat = os.fstat(fd)
                if file_stat.st_uid != os.geteuid() or not stat.S_ISREG(file_stat.st_mode):
                    raise AccountLockUnavailable("ACCOUNT_LOCK_FILE_UNSAFE")
                os.fchmod(fd, 0o600)
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError as exc:
                    if exc.errno in (errno.EACCES, errno.EAGAIN):
                        raise AccountLockUnavailable("ACCOUNT_LOCK_HELD_ELSEWHERE") from exc
                    raise
            except BaseException:
                os.close(fd)
                raise
            self._fd = fd
            _owned_paths.add(self.path)

    def release(self) -> None:
        with _guard:
            if self._fd is not None:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
                os.close(self._fd)
                self._fd = None
                _owned_paths.discard(self.path)
