"""Test-only bounded scalar aggregation of completed physical admissions."""
from time import perf_counter


class RetainedFlowProfile:
    def __init__(self):
        self.previous_completed = 0
        self.reset()

    def reset(self):
        self.started = perf_counter()
        self.groups = {}
        self.dropped = 0

    def record(self, sample):
        # Background heartbeat can overlap the financial path. Its elapsed
        # duration must never be added to that path's serial total.
        key = ('background' if sample.operation == 'process_lease_heartbeat' else 'foreground', sample.operation)
        if key not in self.groups:
            if len(self.groups) >= 128:
                self.dropped += 1
                return
            self.groups[key] = dict(count=0, elapsed_ms=0.0, max_ms=0.0, errors=0, phases_ms={}, phase_max_ms={})
        group = self.groups[key]
        group['count'] += 1
        group['elapsed_ms'] += sample.elapsed_ms
        group['max_ms'] = max(group['max_ms'], sample.elapsed_ms)
        group['errors'] += sample.outcome != 'success'
        for phase, elapsed in sample.phases_ms:
            group['phases_ms'][phase] = group['phases_ms'].get(phase, 0.0) + elapsed
            group['phase_max_ms'][phase] = max(group['phase_max_ms'].get(phase, 0.0), elapsed)

    def checkpoint(self, completed):
        groups = []
        for (lane, operation), group in sorted(self.groups.items(), key=lambda item: -item[1]['elapsed_ms']):
            row = dict(lane=lane, operation=operation, **group)
            for key in ('elapsed_ms', 'max_ms'):
                row[key] = round(row[key], 3)
            for key in ('phases_ms', 'phase_max_ms'):
                row[key] = {phase: round(elapsed, 3) for phase, elapsed in row[key].items()}
            groups.append(row)
        result = dict(from_completed=self.previous_completed, to_completed=completed,
                      wall_ms=round((perf_counter() - self.started) * 1000, 3), groups=groups, dropped=self.dropped)
        self.previous_completed = completed
        return result
