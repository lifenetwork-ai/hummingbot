"""Lifecycle policy must isolate failures without overlapping engine processes."""
import subprocess
import sys
import time

import pytest

from hummingbot.cli.process_supervision import ChildSupervisor, HealthWatchdog


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class Child:
    def __init__(self, role, *, ignores_term=False):
        self.role, self.returncode = role, None
        self.waited = self.terminated = self.killed = False
        self.ignores_term = ignores_term

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        if self.returncode is None:
            raise subprocess.TimeoutExpired('child', timeout)
        self.waited = True
        return self.returncode

    def terminate(self):
        self.terminated = True
        if not self.ignores_term:
            self.returncode = -15

    def kill(self):
        self.killed = True
        self.returncode = -9


def supervisor(*, launch_error=False):
    clock, launches, events = Clock(), [], []

    def launch(role):
        if launch_error:
            raise OSError('secret connection details must not be logged')
        previous = next((p for p in reversed(launches) if p.role == role), None)
        if previous is not None:
            assert previous.waited, 'Never replace an unreaped child'
        child = Child(role)
        launches.append(child)
        return child

    return ChildSupervisor(launch, clock=clock, emit=events.append), clock, launches, events


def test_api_crash_restarts_only_api_and_keeps_engine_identity():
    owner, clock, children, _ = supervisor()
    owner.tick()
    engine, api = owner.children['engine'], owner.children['api']
    api.returncode = -9
    owner.tick()
    assert api.waited and owner.children['api'] is None
    clock.now += .25
    owner.tick()
    assert owner.children['api'] is not api
    assert owner.children['engine'] is engine and not engine.terminated
    assert len(children) == 3


def test_engine_replacement_requires_physical_exit_and_reap():
    owner, clock, _, _ = supervisor()
    owner.tick()
    old, api = owner.children['engine'], owner.children['api']
    old.ignores_term = True
    owner.unresponsive('engine')
    assert old.terminated
    clock.now += 1
    owner.tick()
    assert owner.children['engine'] is old and not old.killed
    clock.now += 3
    owner.tick()
    assert old.killed and old.waited
    clock.now += 1
    owner.tick()
    assert owner.children['engine'] is not old
    assert owner.children['api'] is api and not api.terminated


def test_repeated_failures_back_off_with_bounded_role_state():
    owner, clock, _, _ = supervisor()
    owner.tick()
    delays = []
    for _ in range(100):
        owner.children['api'].returncode = 1
        owner.tick()
        delay = owner.next_start['api'] - clock.now
        delays.append(delay)
        assert owner.children['api'] is None and 0 < delay <= 15
        clock.now = owner.next_start['api']
        owner.tick()
    assert delays[:3] == [.25, .5, 1]
    assert delays[-1] == 15
    for mapping in (owner.children, owner.failures, owner.next_start, owner.started_at, owner.terminating):
        assert len(mapping) <= 2


def test_stable_child_resets_backoff_and_unresponsive_does_not_extend_deadline():
    owner, clock, _, _ = supervisor()
    owner.tick()
    owner.failures['api'] = 6
    clock.now = 61
    owner.tick()
    assert owner.failures['api'] == 0
    owner.children['api'].ignores_term = True
    owner.unresponsive('api')
    deadline = owner.terminating['api']
    clock.now += 1
    owner.unresponsive('api')
    assert owner.terminating['api'] == deadline


def test_shutdown_disables_restarts_and_reaps_both_children():
    owner, clock, children, _ = supervisor()
    owner.tick()
    children[0].ignores_term = True
    owner.shutdown()
    clock.now += 100
    owner.tick()
    assert len(children) == 2
    assert all(p.terminated and p.waited for p in children)
    assert children[0].killed


def test_stop_arriving_during_launch_does_not_start_the_next_role():
    launched = []

    def launch(role):
        launched.append(Child(role))
        owner.stopping = True  # A SIGTERM can arrive while Popen is running.
        return launched[-1]

    owner = ChildSupervisor(launch, emit=lambda event: None)
    try:
        owner.tick()
        assert [child.role for child in launched] == ['engine']
    finally:
        owner.shutdown()


def test_launch_errors_retry_without_logging_exception_messages():
    owner, clock, _, events = supervisor(launch_error=True)
    owner.tick()
    assert all(owner.children[role] is None for role in ('engine', 'api'))
    assert all(owner.next_start[role] > clock.now for role in ('engine', 'api'))
    assert events and all('secret' not in str(event) for event in events)
    assert {event['error_type'] for event in events} == {'OSError'}


def test_invalid_role_cannot_create_unbounded_supervision_state():
    owner, _, _, _ = supervisor()
    with pytest.raises(KeyError):
        owner.unresponsive('unknown-child')


def test_real_children_restart_independently_and_are_reaped():
    children = []

    def launch(role):
        child = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(60)'])
        children.append(child)
        return child

    owner = ChildSupervisor(launch, emit=lambda event: None)

    def replaced(role, old):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            owner.tick()
            child = owner.children[role]
            if child is not None and child is not old:
                return child
            time.sleep(.01)
        pytest.fail('Replacement child did not start')

    try:
        owner.tick()
        engine, api = owner.children['engine'], owner.children['api']
        api.kill()
        new_api = replaced('api', api)
        assert api.returncode is not None
        assert owner.children['engine'] is engine and engine.poll() is None
        engine.kill()
        new_engine = replaced('engine', engine)
        assert engine.returncode is not None
        assert new_engine.poll() is None and owner.children['api'] is new_api
    finally:
        owner.shutdown()
        assert all(child.poll() is not None for child in children)


def test_watchdog_tolerates_startup_but_restarts_a_sustained_stall():
    owner, clock, _, _ = supervisor()
    owner.tick()
    watchdog = HealthWatchdog(owner)
    watchdog.observe('engine', False)
    clock.now = 239
    watchdog.observe('engine', False)
    assert not owner.children['engine'].terminated
    clock.now = 240
    watchdog.observe('engine', False)
    assert owner.children['engine'].terminated


def test_watchdog_recovery_resets_failure_window_and_replacement_gets_startup_grace():
    owner, clock, _, _ = supervisor()
    owner.tick()
    watchdog = HealthWatchdog(owner)
    watchdog.observe('api', True)
    clock.now = 1
    watchdog.observe('api', False)
    clock.now = 15
    watchdog.observe('api', False)
    assert not owner.children['api'].terminated
    watchdog.observe('api', True)
    clock.now = 16
    watchdog.observe('api', False)
    clock.now = 31
    watchdog.observe('api', False)
    assert owner.children['api'].terminated
    owner.tick()
    clock.now += 1
    owner.tick()
    replacement = owner.children['api']
    watchdog.observe('api', False)
    clock.now += 30
    watchdog.observe('api', False)
    assert not replacement.terminated
    assert len(watchdog.observations) <= 2


def test_fatal_configuration_exit_stops_parent_without_relaunch():
    owner, clock, children, events = supervisor()
    owner.tick()
    owner.children['engine'].returncode = 78
    owner.tick()
    assert owner.stopping and owner.exit_code == 78
    owner.shutdown()
    clock.now += 100
    owner.tick()
    assert len(children) == 2 and children[1].terminated
    assert any(row['event'] == 'child_configuration_failed' for row in events)


def test_child_exiting_during_termination_still_reaps_and_stops_peer():
    owner, _, children, _ = supervisor()
    owner.tick()

    def disappear():
        children[0].returncode = -15
        raise ProcessLookupError()

    children[0].terminate = disappear
    owner.shutdown()
    assert children[0].waited and children[1].terminated and children[1].waited
