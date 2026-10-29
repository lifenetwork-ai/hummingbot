"""Read Linux process diagnostics without spawning a Python process per sample."""
import gc
from pathlib import Path


def collect_requested(role, marker):
    """Only engine/legacy probes consume the post-Stop engine GC request."""
    if role == 'api' or not marker.exists():
        return False
    marker.unlink()
    gc.collect()
    return True


def process_snapshot(root=Path('/proc')):
    roles = {'supervisor': 1}
    for identity in (root / '1/task/1/children').read_text().split():
        try:
            args = (root / identity / 'cmdline').read_bytes().decode().split('\0')
            if '--role' in args:
                role = args[args.index('--role') + 1]
                if role in ('engine', 'api'):
                    roles[role] = int(identity)
        except FileNotFoundError:
            continue
    result = {}
    for role, identity in roles.items():
        path = root / str(identity)
        try:
            row = dict(pid=identity, fds=len(list((path / 'fd').iterdir())))
            for line in (path / 'status').read_text().splitlines():
                key, _, rest = line.partition(':')
                if key in ('VmRSS', 'VmHWM', 'Threads'):
                    row[key] = int(rest.split()[0]) * (1024 if key != 'Threads' else 1)
            result[role] = row
        except FileNotFoundError:
            pass
    return result
