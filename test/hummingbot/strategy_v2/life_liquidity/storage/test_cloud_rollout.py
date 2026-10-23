"""No deployment: verify release ordering against a recording subprocess port."""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from deploy.life_cloud_run.rollout import RolloutConfig, rollout


def answer(command, kwargs, cfg):
    if 'output' in command:
        import json
        image = 'asia-docker.pkg.dev/fixture/worker/life@sha256:' + 'a' * 64
        kwargs['stdout'].write(json.dumps(dict(project=cfg.project, region=cfg.region, migration_job=cfg.migration_job,
                                               worker_instances=0, migration_image=image, worker_image=image)).encode())
    return SimpleNamespace(returncode=0)


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
        assert kwargs['stdin'] == kwargs['stderr'] == -3  # DEVNULL
        if 'output' not in command:
            assert kwargs['stdout'] == -3
        return answer(command, kwargs, cfg)
    rollout(cfg, runner=run)
    assert len(calls) == 4
    assert calls[0][-1] == '-var=worker_instances=0'
    assert calls[1][-3:] == ('output', '-json', 'life_rollout_identity')
    assert calls[2][:4] == ('gcloud', 'run', 'jobs', 'execute') and '--wait' in calls[2]
    assert calls[3][-1] == '-var=worker_instances=1'
    assert all('DSN' not in arg and 'password' not in arg for command in calls for arg in command)


@pytest.mark.parametrize('failed', [0, 2])
def test_failed_or_ambiguous_migration_never_starts_a_worker(tmp_path, failed):
    cfg, calls = config(tmp_path), []

    def run(command, **kwargs):
        calls.append(command)
        if len(calls) - 1 == failed:
            return SimpleNamespace(returncode=1)
        return answer(command, kwargs, cfg)
    with pytest.raises(RuntimeError):
        rollout(cfg, runner=run)
    assert len(calls) == failed + 1
    assert all(command[-1] != '-var=worker_instances=1' for command in calls)


def test_disabled_migrations_use_same_bounded_stopped_rollout(tmp_path):
    cfg, calls = replace(config(tmp_path), auto_migrate=False), []

    def run(command, **kwargs):
        calls.append(command)
        return answer(command, kwargs, cfg)
    rollout(cfg, runner=run)
    assert len(calls) == 3 and all(c[0] == 'terraform' for c in calls)


def test_changed_input_during_migration_cannot_be_reinterpreted_as_reviewed_release(tmp_path):
    cfg, calls = config(tmp_path), []

    def run(command, **kwargs):
        calls.append(command)
        if command[0] == 'gcloud':
            cfg.variables.write_text('{"changed":true}')
        return answer(command, kwargs, cfg)
    with pytest.raises(ValueError, match='SOURCE_CHANGED'):
        rollout(cfg, runner=run)
    assert len(calls) == 3


@pytest.mark.parametrize('name', ['extra.tf.json', 'terraform.tfvars', 'extra.auto.tfvars.json', 'extra.auto.tfvars'])
def test_implicitly_loaded_input_changes_stop_rollout(tmp_path, name):
    cfg, calls = config(tmp_path), []
    file = cfg.module / name
    file.write_text('{}')

    def run(command, **kwargs):
        calls.append(command)
        file.write_text('{"changed":true}')
        return SimpleNamespace(returncode=0)
    with pytest.raises(ValueError, match='SOURCE_CHANGED'):
        rollout(cfg, runner=run)
    assert len(calls) == 1


@pytest.mark.parametrize('key', ['TF_CLI_ARGS', 'TF_CLI_ARGS_apply', 'TF_VAR_worker_instances', 'TF_WORKSPACE', 'TF_DATA_DIR'])
def test_ambient_terraform_overrides_cannot_change_reviewed_rollout(tmp_path, monkeypatch, key):
    calls = []
    monkeypatch.setenv(key, 'unreviewed')

    def run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0)
    with pytest.raises(ValueError, match='AMBIENT_TERRAFORM_OVERRIDE'):
        rollout(config(tmp_path), runner=run)
    assert calls == []


def test_subprocess_environment_is_frozen_before_first_stage(tmp_path, monkeypatch):
    cfg, calls = config(tmp_path), []
    monkeypatch.setenv('GOOGLE_APPLICATION_CREDENTIALS', '/synthetic/original')

    def run(command, **kwargs):
        calls.append(command)
        assert kwargs['env']['GOOGLE_APPLICATION_CREDENTIALS'] == '/synthetic/original'
        assert kwargs['env']['TF_IN_AUTOMATION'] == '1'
        monkeypatch.setenv('GOOGLE_APPLICATION_CREDENTIALS', '/synthetic/changed')
        return answer(command, kwargs, cfg)
    rollout(cfg, runner=run)
    assert len(calls) == 4


def test_existing_nondefault_workspace_is_rejected_before_commands(tmp_path):
    cfg = config(tmp_path)
    folder = cfg.module / '.terraform'
    folder.mkdir()
    (folder / 'environment').write_text('another-account')
    with pytest.raises(ValueError, match='WORKSPACE_INVALID'):
        rollout(cfg, runner=lambda *a, **k: pytest.fail('No command may run'))


@pytest.mark.parametrize('fault', ['project', 'region', 'job', 'active_worker', 'image', 'floating_image', 'missing', 'invalid_json', 'too_large', 'command_failed'])
def test_reviewed_drain_identity_must_match_before_migration_or_start(tmp_path, fault):
    import json
    cfg, calls = config(tmp_path), []
    image = 'asia-docker.pkg.dev/fixture/worker/life@sha256:' + 'a' * 64
    identity = dict(project=cfg.project, region=cfg.region, migration_job=cfg.migration_job,
                    worker_instances=0, migration_image=image, worker_image=image)
    if fault in ('project', 'region'):
        identity[fault] = 'different-target'
    elif fault == 'job':
        identity['migration_job'] = 'unrelated-migration'
    elif fault == 'active_worker':
        identity['worker_instances'] = 1
    elif fault == 'image':
        identity['migration_image'] = image[:-1] + 'b'
    elif fault == 'floating_image':
        identity['worker_image'] = identity['migration_image'] = 'fixture:latest'
    elif fault == 'missing':
        del identity['region']

    def run(command, **kwargs):
        calls.append(command)
        if 'output' in command:
            raw = b'not-json' if fault == 'invalid_json' else b'x' * 8193 if fault == 'too_large' else json.dumps(identity).encode()
            kwargs['stdout'].write(raw)
            return SimpleNamespace(returncode=1 if fault == 'command_failed' else 0)
        return SimpleNamespace(returncode=0)
    with pytest.raises((ValueError, RuntimeError), match='ROLLOUT_IDENTITY'):
        rollout(cfg, runner=run)
    assert len(calls) == 2
    assert all(c[0] != 'gcloud' and c[-1] != '-var=worker_instances=1' for c in calls)
