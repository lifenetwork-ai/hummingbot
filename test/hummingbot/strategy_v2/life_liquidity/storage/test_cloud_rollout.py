"""No deployment: verify release ordering against a recording subprocess port."""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from deploy.life_cloud_run.rollout import RolloutConfig, rollout


def config(tmp_path):
    module = tmp_path / 'module'
    module.mkdir()
    (module / 'main.tf').write_text('synthetic')
    (module / '.terraform.lock.hcl').write_text('synthetic-pinned-provider')
    variables = tmp_path / 'profile.tfvars.json'
    variables.write_text('{}')
    return RolloutConfig('fixture-project', 'asia-southeast1', 'life-fixture-migrate', module, variables, True, 60)


def test_start_waits_for_original_automigration_and_has_no_schema_owner_secrets(tmp_path):
    cfg, calls = config(tmp_path), []

    def run(command, **kwargs):
        calls.append(command)
        assert kwargs['stdin'] == kwargs['stdout'] == kwargs['stderr'] == -3  # DEVNULL
        return SimpleNamespace(returncode=0)
    rollout(cfg, runner=run)
    assert len(calls) == 3
    assert calls[0][-1] == '-var=worker_instances=0'
    assert calls[1][:4] == ('gcloud', 'run', 'jobs', 'execute') and '--wait' in calls[1]
    assert calls[2][-1] == '-var=worker_instances=1'
    assert all('DSN' not in arg and 'password' not in arg for command in calls for arg in command)


@pytest.mark.parametrize('failed', [0, 1])
def test_failed_or_ambiguous_migration_never_starts_a_worker(tmp_path, failed):
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        if len(calls) - 1 == failed:
            return SimpleNamespace(returncode=1)
        return SimpleNamespace(returncode=0)
    with pytest.raises(RuntimeError):
        rollout(config(tmp_path), runner=run)
    assert len(calls) == failed + 1
    assert all(command[-1] != '-var=worker_instances=1' for command in calls)


def test_disabled_migrations_use_same_bounded_stopped_rollout(tmp_path):
    cfg, calls = replace(config(tmp_path), auto_migrate=False), []

    def run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0)
    rollout(cfg, runner=run)
    assert len(calls) == 2 and all(c[0] == 'terraform' for c in calls)


def test_changed_input_during_migration_cannot_be_reinterpreted_as_reviewed_release(tmp_path):
    cfg, calls = config(tmp_path), []

    def run(command, **kwargs):
        calls.append(command)
        if len(calls) == 2:
            cfg.variables.write_text('{"changed":true}')
        return SimpleNamespace(returncode=0)
    with pytest.raises(ValueError, match='SOURCE_CHANGED'):
        rollout(cfg, runner=run)
    assert len(calls) == 2
