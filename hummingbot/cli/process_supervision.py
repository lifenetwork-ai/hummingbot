"""Bounded child supervision; engine recovery retains all trading authority."""
import subprocess
import time


class ChildSupervisor:
    ROLES = ('engine', 'api')

    def __init__(self, launch, *, clock=time.monotonic, emit):
        self.launch, self.clock, self.emit = launch, clock, emit
        self.children = dict.fromkeys(self.ROLES)
        self.failures = dict.fromkeys(self.ROLES, 0)
        self.next_start = dict.fromkeys(self.ROLES, 0.0)
        self.started_at = dict.fromkeys(self.ROLES, 0.0)
        self.terminating = {}
        self.stopping = False
        self.exit_code = 0

    @staticmethod
    def signal_child(child, *, kill=False):
        try:
            child.kill() if kill else child.terminate()
        except ProcessLookupError:
            # Exit can race poll()/the signal; wait() still reaps this child.
            pass

    def failed(self, role, **details):
        # All retained state is per role, not an append-only restart history.
        self.failures[role] = min(7, self.failures[role] + 1)
        delay = min(15, .25 * 2 ** (self.failures[role] - 1))
        self.next_start[role] = self.clock() + delay
        self.emit(dict(severity='WARNING', event='child_restart_scheduled',
                       role=role, retry_after_seconds=delay, **details))

    def tick(self):
        if self.stopping:
            return
        now = self.clock()
        for role in self.ROLES:
            if self.stopping:
                return
            child = self.children[role]
            if child is not None:
                if child.poll() is None and now >= self.terminating.get(role, float('inf')):
                    self.signal_child(child, kill=True)
                if child.poll() is None:
                    if role not in self.terminating and now - self.started_at[role] >= 60:
                        self.failures[role] = 0
                    continue
                # Physical termination/reaping must precede another launch.
                code = child.wait(timeout=0)
                self.children[role] = None
                self.terminating.pop(role, None)
                if code == 78:
                    self.exit_code, self.stopping = code, True
                    self.emit(dict(severity='ERROR', event='child_configuration_failed', role=role, exit_code=code))
                    return
                self.failed(role, exit_code=code)
            if self.stopping:
                return
            if now < self.next_start[role]:
                continue
            try:
                self.children[role] = self.launch(role)
            except OSError as error:
                self.failed(role, error_type=type(error).__name__)
            else:
                self.started_at[role] = now
                self.emit(dict(severity='INFO', event='child_started', role=role))

    def unresponsive(self, role, **diagnostics):
        child = self.children[role]
        if self.stopping or child is None or child.poll() is not None or role in self.terminating:
            return
        self.emit(dict(severity='ERROR', event='child_unresponsive', role=role, **diagnostics))
        self.terminating[role] = self.clock() + 3
        self.signal_child(child)

    def shutdown(self, budget=8):
        self.stopping = True
        deadline = self.clock() + budget
        for child in self.children.values():
            if child is not None and child.poll() is None:
                self.signal_child(child)
        for role, child in self.children.items():
            if child is None:
                continue
            try:
                code = child.wait(timeout=max(.01, deadline - self.clock()))
            except subprocess.TimeoutExpired:
                self.signal_child(child, kill=True)
                code = child.wait(timeout=.2)
            if code not in (0, -15, -2):
                self.exit_code = self.exit_code or 1
                self.emit(dict(severity='ERROR', event='child_shutdown_failed', role=role, exit_code=code))


class HealthWatchdog:
    """Allow imports/migrations; only sustained later liveness loss restarts."""

    def __init__(self, supervisor, *, startup_grace=240, failure_grace=15):
        self.supervisor = supervisor
        self.observations = {}
        self.startup_grace, self.failure_grace = startup_grace, failure_grace

    @staticmethod
    def diagnostics(previous, now):
        return dict(reason_code=previous['reason'], first_reason=previous['first_reason'],
                    probe_status=previous['status'], probe_elapsed_ms=previous['elapsed_ms'],
                    max_probe_elapsed_ms=previous['max_elapsed_ms'], failed_probes=previous['failed_probes'],
                    failure_seconds=round(now - previous['failed_since'], 3),
                    max_probe_gap_seconds=round(previous['max_gap'], 3))

    def observe(self, role, healthy, *, reason='unknown', status=None, elapsed_ms=0):
        child = self.supervisor.children[role]
        if child is None:
            self.observations.pop(role, None)
            return
        if self.supervisor.stopping or role in self.supervisor.terminating:
            return
        now = self.supervisor.clock()
        previous = self.observations.get(role)
        if previous is None or previous['child'] is not child:
            previous = self.observations[role] = dict(child=child, first_seen=now, healthy_once=False, failed_since=None,
                                                      last_at=now, max_gap=0, failed_probes=0, max_elapsed_ms=0)
        gap = now - previous['last_at']
        previous['last_at'] = now
        if previous['failed_since'] is not None:
            previous['max_gap'] = max(previous['max_gap'], gap)
        if healthy:
            if previous['healthy_once'] and previous['failed_since'] is not None:
                self.supervisor.emit(dict(severity='INFO', event='child_probe_recovered', role=role,
                                          **self.diagnostics(previous, now)))
            previous.update(healthy_once=True, failed_since=None)
            return
        first_failure = previous['failed_since'] is None
        if first_failure:
            previous.update(failed_since=now, first_reason=reason, failed_probes=0, max_gap=gap, max_elapsed_ms=0)
        previous.update(reason=reason, status=status, elapsed_ms=elapsed_ms,
                        failed_probes=previous['failed_probes'] + 1, max_elapsed_ms=max(previous['max_elapsed_ms'], elapsed_ms))
        # One transition log, not one log for every poll. Startup misses are expected.
        if first_failure and previous['healthy_once']:
            self.supervisor.emit(dict(severity='WARNING', event='child_probe_failed', role=role,
                                      **self.diagnostics(previous, now)))
        deadline = previous['failed_since'] + self.failure_grace
        if not previous['healthy_once']:
            deadline = max(deadline, previous['first_seen'] + self.startup_grace)
        if now >= deadline:
            self.supervisor.unresponsive(role, **self.diagnostics(previous, now))
