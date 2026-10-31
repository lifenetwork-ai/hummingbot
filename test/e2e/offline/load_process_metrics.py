"""Read Linux process diagnostics without spawning a Python process per sample."""
import gc
import json
import math
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


def distribution(values):
    ordered = sorted(values)
    if not ordered:
        return dict(count=0, p50=None, p95=None, p99=None, max=None)
    return {"count": len(ordered), **{key: ordered[max(0, math.ceil(len(ordered) * fraction) - 1)]
            for key, fraction in (("p50", .5), ("p95", .95), ("p99", .99))}, "max": ordered[-1]}


def write_metrics_snapshot(path, data, values):
    """Sort and publish immutable measurements outside the measured event loop."""
    data['distributions'] = {key: distribution(items) for key, items in values.items()}
    fields = {}
    for line in Path('/proc/self/status').read_text().splitlines():
        if line.startswith(('VmRSS:', 'VmHWM:')):
            name, amount, _ = line.split()
            fields[name.rstrip(':')] = int(amount) * 1024
    data['memory'] = fields
    cpu = Path('/sys/fs/cgroup/cpu.stat')
    if cpu.is_file():
        data['cgroup_cpu'] = {key: int(value) for key, value in (line.split() for line in cpu.read_text().splitlines())}
    if data['process_role'] in ('engine', 'api'):
        data['processes'] = process_snapshot(role=data['process_role'], identity=data['process_id'])
        memory_stat = dict((key, int(value)) for key, value in
                           (line.split() for line in Path('/sys/fs/cgroup/memory.stat').read_text().splitlines()))
        data['container_memory'] = dict(current_bytes=int(Path('/sys/fs/cgroup/memory.current').read_text()),
                                        peak_bytes=int(Path('/sys/fs/cgroup/memory.peak').read_text()),
                                        file_bytes=memory_stat['file'], shmem_bytes=memory_stat['shmem'])
    temporary = path.with_suffix('.new')
    temporary.write_text(json.dumps(data, default=str))
    temporary.replace(path)


class MetricsExporter:
    """One in-flight snapshot; skip busy exports instead of retaining a backlog."""

    def __init__(self, path, *, writer=write_metrics_snapshot):
        self.path, self.writer = path, writer
        self.worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix='load-metrics')
        self.pending, self.skipped, self.closed = None, 0, False

    def submit(self, data, samples):
        if self.closed:
            raise RuntimeError('Metrics exporter is closed')
        if self.pending is not None:
            if not self.pending.done():
                self.skipped += 1
                return False
            self.pending.result()  # A failed measurement must not silently look healthy.
        snapshot = {key: tuple(values) for key, values in list(samples.items())}
        self.pending = self.worker.submit(self.writer, self.path, data, snapshot)
        return True

    def close(self):
        self.closed = True
        self.worker.shutdown(wait=True)
        if self.pending is not None:
            self.pending.result()


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
