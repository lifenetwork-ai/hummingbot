"""Bulk fixture writes must never compete with the running service's owner."""
import ast
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import mm_load_preparation as preparation


class Fixture:
    def __init__(self):
        self.actions = []
        self.physical_running = True
        self.row = dict(owner_uuid='old-owner', owner_generation=1, desired_state='stopped',
                        control_version=2, acknowledged_version=2,
                        mm_session=dict(id='session', status='stopped', generation=1),
                        recovery_state={'spot_outflow': {'coverage_start_ms': 100, 'binding': {'account': 'dummy'}}},
                        status=dict(running=False, phase='stopped', engine=dict(strategy_running=False, clock_running=False)))
        self.clients = dict(application_connections=0, ownership_locks=0, pending_orders=0)
        self.history_count = 0
        self.seed_error = self.checkpoint_error = None
        self.unreleased = False
        self.change_intent = self.restart_halt = False
        self.readiness = True
        self.exit_code = 0
        self.post_restart_logs = ''
        self.clear_outflow = False

    def state(self):
        return copy.deepcopy(self.row)

    def ledger(self):
        return {'orders': [], 'unknown': []}

    def terminate(self, bot):
        self.actions.append('terminate')
        self.physical_running = False
        if not self.unreleased:
            self.row['owner_uuid'] = None

    def sql(self, statement, *, timeout=60):
        if statement == 'CHECKPOINT':
            self.actions.append('checkpoint')
            assert not self.physical_running
            assert timeout == 180
            if self.checkpoint_error:
                raise self.checkpoint_error
            return ''
        if 'pg_stat_activity' in statement:
            return json.dumps(self.clients)
        if 'load-history.yaml' in statement:
            return json.dumps(dict(orders=self.history_count, fills=self.history_count))
        raise AssertionError('Unexpected preparation SQL')

    def docker(self, *arguments, **kwargs):
        if arguments == ('start', 'bot'):
            self.actions.append('restart')
            self.physical_running = True
            self.row.update(owner_uuid='new-owner', owner_generation=2)
            if self.restart_halt:
                self.row['recovery_state']['halt'] = True
            if self.clear_outflow:
                self.row['recovery_state'].pop('spot_outflow')
        elif arguments == ('inspect', 'bot'):
            state = dict(Status='running' if self.physical_running else 'exited', ExitCode=self.exit_code, OOMKilled=False)
            return SimpleNamespace(stdout=json.dumps([{'State': state}]))
        elif arguments == ('logs', 'bot'):
            return SimpleNamespace(stdout=self.post_restart_logs if 'restart' in self.actions else '', stderr='')
        else:
            raise AssertionError('Unexpected Docker operation')
        return SimpleNamespace(stdout='bot')

    def seed(self, harness, count, *, active_outflow, sql_timeout=60):
        self.actions.append('seed')
        assert not self.physical_running, 'Bulk history seeded while service is still running'
        assert active_outflow is True and sql_timeout == 180
        if self.seed_error:
            raise self.seed_error
        self.history_count = count
        if self.change_intent:
            self.row['control_version'] += 1


@pytest.fixture
def fixture(monkeypatch):
    value = Fixture()
    # The module is given the same Docker/probe adapters used by the real harness.
    monkeypatch.setattr(preparation, 'docker', value.docker, raising=False)
    monkeypatch.setattr(preparation, 'http', lambda *args: (200 if value.readiness else 503, {}), raising=False)

    def poll(callback, *, timeout=90, description):
        result = callback()
        if not result:
            raise TimeoutError(description)
        return result
    monkeypatch.setattr(preparation, 'poll', poll, raising=False)
    return value


def run(fixture):
    evidence = {}
    preparation.prepare_history(fixture, 'bot', 100, active_outflow=True, seed=fixture.seed, evidence=evidence)
    return evidence


def test_bulk_seed_is_offline_and_restarts_with_new_fenced_owner(fixture):
    before = fixture.state()
    evidence = run(fixture)
    assert fixture.actions == ['terminate', 'seed', 'checkpoint', 'restart']
    assert fixture.row['recovery_state'] == before['recovery_state']
    assert fixture.row['mm_session'] == before['mm_session']
    assert fixture.row['control_version'] == 2
    assert evidence['stage'] == 'complete' and evidence['owner_generation_after'] == 2


@pytest.mark.parametrize('field,value', [('desired_state', 'running'), ('acknowledged_version', 1)])
def test_unfinished_stop_prevents_any_fixture_work(fixture, field, value):
    fixture.row[field] = value
    with pytest.raises(AssertionError, match='stopped|acknowledged'):
        run(fixture)
    assert not fixture.actions


def test_halt_cannot_be_reset_as_fixture_preparation(fixture):
    fixture.row['recovery_state']['halt'] = True
    with pytest.raises(AssertionError, match='recovery'):
        run(fixture)
    assert fixture.row['recovery_state']['halt'] is True
    assert not fixture.actions


def test_owner_release_failure_does_not_seed(fixture):
    fixture.unreleased = True
    with pytest.raises(AssertionError, match='owner'):
        run(fixture)
    assert fixture.actions == ['terminate']


@pytest.mark.parametrize('field', ['application_connections', 'ownership_locks', 'pending_orders'])
def test_remaining_runtime_effects_prevent_bulk_writes(fixture, field):
    fixture.clients[field] = 1
    with pytest.raises(AssertionError, match='offline|pending|connection|ownership'):
        run(fixture)
    assert fixture.actions == ['terminate']


@pytest.mark.parametrize('stage', ['seed', 'checkpoint'])
def test_failed_preparation_never_restarts_the_service(fixture, stage):
    setattr(fixture, stage + '_error', RuntimeError('fixture failed'))
    with pytest.raises(RuntimeError, match='fixture failed'):
        run(fixture)
    assert 'restart' not in fixture.actions and not fixture.physical_running


def test_changed_control_intent_is_not_silently_restored(fixture):
    fixture.change_intent = True
    with pytest.raises(AssertionError, match='state changed'):
        run(fixture)
    assert fixture.row['control_version'] == 3 and 'restart' not in fixture.actions


def test_restarted_halt_stops_workload_without_reset(fixture):
    fixture.restart_halt = True
    with pytest.raises(AssertionError, match='recovery'):
        run(fixture)
    assert fixture.row['recovery_state']['halt'] is True


def test_zero_history_needs_no_bounce_or_writes(fixture):
    evidence = {}
    preparation.prepare_history(fixture, 'bot', 0, active_outflow=True, seed=fixture.seed, evidence=evidence)
    assert not fixture.actions and evidence['stage'] == 'skipped'


@pytest.mark.parametrize('count', [-1, 1000001, True])
def test_invalid_count_is_rejected_before_stopping_service(fixture, count):
    with pytest.raises(AssertionError, match='count'):
        preparation.prepare_history(fixture, 'bot', count, active_outflow=True, seed=fixture.seed, evidence={})
    assert not fixture.actions


@pytest.mark.parametrize('field', ['strategy_running', 'clock_running'])
def test_live_trading_task_prevents_staging(fixture, field):
    fixture.row['status']['engine'][field] = True
    with pytest.raises(AssertionError, match='core must be stopped'):
        run(fixture)
    assert not fixture.actions


def test_abnormal_service_exit_prevents_seeding(fixture):
    fixture.exit_code = 1
    with pytest.raises(AssertionError, match='cleanly offline'):
        run(fixture)
    assert fixture.actions == ['terminate']


def test_incomplete_seed_never_restarts(fixture, monkeypatch):
    original = fixture.sql

    def sql(statement, **kwargs):
        if 'load-history.yaml' in statement:
            return json.dumps({'orders': 100, 'fills': 99})
        return original(statement, **kwargs)
    monkeypatch.setattr(fixture, 'sql', sql)
    with pytest.raises(AssertionError, match='Incomplete history'):
        run(fixture)
    assert not fixture.physical_running and 'restart' not in fixture.actions


def test_unexpected_child_restart_cannot_be_hidden_by_successful_boot(fixture):
    fixture.post_restart_logs = json.dumps({'event': 'child_restart_scheduled', 'scope': 'engine'})
    with pytest.raises(AssertionError, match='Unexpected service failure/restart'):
        run(fixture)


def test_startup_failure_prevents_workload_admission(fixture):
    fixture.readiness = False
    with pytest.raises(TimeoutError, match='post-seed startup'):
        run(fixture)


def test_restart_cannot_rebind_the_verified_outflow_origin(fixture):
    fixture.clear_outflow = True
    with pytest.raises(AssertionError, match='outflow'):
        run(fixture)


@pytest.mark.parametrize('entrypoint', [
    'test_mm_load.py', 'test_mm_reporting_isolation.py', 'test_mm_production_control.py',
    'test_mm_random_handover.py', 'test_mm_memory.py', 'test_mm_process_isolation.py'])
def test_load_entrypoints_cannot_bypass_offline_preparation(entrypoint):
    # This repository-level safety rule guards the call sites as well as the
    # shared lifecycle: a new diagnostic must not restore live bulk writes.
    root = Path(__file__).resolve().parents[3]
    tree = ast.parse((root / 'scripts' / entrypoint).read_text())
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)]
    assert not [node for node in calls if node.func.id == 'seed_history'], 'Direct live seed bypasses service shutdown'
    staged = [node for node in calls if node.func.id == 'prepare_history']
    assert len(staged) == 1
    assert any(k.arg == 'seed' and isinstance(k.value, ast.Name) and k.value.id == 'seed_history'
               for k in staged[0].keywords), 'Reuse the original fixture generator'


def test_process_experiment_fences_are_measured_after_planned_restart():
    root = Path(__file__).resolve().parents[3]
    tree = ast.parse((root / 'scripts/test_mm_process_isolation.py').read_text())
    staged = [node.lineno for node in ast.walk(tree) if isinstance(node, ast.Call)
              and isinstance(node.func, ast.Name) and node.func.id == 'prepare_history']
    generations = [node.lineno for node in ast.walk(tree) if isinstance(node, ast.Assign)
                   and any(isinstance(t, ast.Name) and t.id == 'generation' for t in node.targets)]
    assert len(staged) == len(generations) == 1 and generations[0] > staged[0]


@pytest.mark.parametrize('security', [False, True])
def test_shared_seed_supports_minimal_sql_adapter_and_opt_in_audit_history(security):
    from scripts.test_mm_load import seed_history
    statements = []
    fixture = SimpleNamespace(sql=statements.append)
    if security:
        fixture.env = {'HBOT_LOAD_GOOGLE_FIXTURE': '/test-assets'}
    seed_history(fixture, 1)
    assert len(statements) == (2 if security else 1)
    assert 'INSERT INTO orders' in statements[0]
    if security:
        assert 'INSERT INTO admin_audit_events' in statements[1]
