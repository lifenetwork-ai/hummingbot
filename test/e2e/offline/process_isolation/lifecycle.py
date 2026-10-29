"""Test-only child supervision policy; never bypasses engine recovery gates."""
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
                    child.kill()
                if child.poll() is None:
                    if role not in self.terminating and now - self.started_at[role] >= 60:
                        self.failures[role] = 0
                    continue
                # Physical termination/reaping must precede another launch.
                code = child.wait(timeout=0)
                self.children[role] = None
                self.terminating.pop(role, None)
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

    def unresponsive(self, role):
        child = self.children[role]
        if self.stopping or child is None or child.poll() is not None or role in self.terminating:
            return
        self.emit(dict(severity='ERROR', event='child_unresponsive', role=role))
        self.terminating[role] = self.clock() + 3
        child.terminate()

    def shutdown(self, budget=8):
        self.stopping = True
        deadline = self.clock() + budget
        for child in self.children.values():
            if child is not None and child.poll() is None:
                child.terminate()
        for child in self.children.values():
            if child is None:
                continue
            try:
                child.wait(timeout=max(.01, deadline - self.clock()))
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=.4)


class HealthWatchdog:
    """Allow initial imports/bootstrap; later failures need 15s of bad probes."""

    def __init__(self, supervisor):
        self.supervisor = supervisor
        self.observations = {}

    def observe(self, role, healthy):
        child = self.supervisor.children[role]
        if child is None:
            self.observations.pop(role, None)
            return
        now = self.supervisor.clock()
        previous = self.observations.get(role)
        if previous is None or previous['child'] is not child:
            previous = self.observations[role] = dict(child=child, first_seen=now, healthy_once=False, failed_since=None)
        if healthy:
            previous.update(healthy_once=True, failed_since=None)
            return
        if previous['failed_since'] is None:
            previous['failed_since'] = now
        deadline = previous['failed_since'] + 15
        if not previous['healthy_once']:
            deadline = max(deadline, previous['first_seen'] + 180)
        if now >= deadline:
            self.supervisor.unresponsive(role)
