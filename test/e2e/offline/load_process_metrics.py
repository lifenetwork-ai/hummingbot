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


def process_snapshot(root=Path('/proc'), *, role=None, identity=None):
    roles = {'supervisor': 1}
    children = (root / '1/task/1/children').read_text().split()
    if role in ('engine', 'api'):
        # The shipping supervisor has exactly these two direct children, and
        # physically reaps before replacement. Each probe already knows its own
        # role/PID. Avoid repeated cross-process cmdline reads: amd64 emulation
        # can retain an auxv descriptor on every inspection. Reject ambiguity
        # instead of guessing, so missing roles fail measurement admission.
        identities = {int(value) for value in children}
        if len(identities) == 2 and identity in identities:
            roles.update({role: identity, 'api' if role == 'engine' else 'engine': (identities - {identity}).pop()})
    else:
        # One-shot external diagnostics discover roles, then exit/release their
        # inspection descriptors. This path never runs in a long-lived probe.
        for child in children:
            try:
                args = (root / child / 'cmdline').read_bytes().decode().split('\0')
                if '--role' in args:
                    found = args[args.index('--role') + 1]
                    if found in ('engine', 'api'):
                        roles[found] = int(child)
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
