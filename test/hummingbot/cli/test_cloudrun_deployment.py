import json
import re
from pathlib import Path

import pytest
import yaml

from scripts.review_cloudrun import review


@pytest.fixture
def candidate(tmp_path):
    template = Path(__file__).resolve().parents[3] / "deploy/cloudrun/service.example.yaml"
    data = yaml.safe_load(re.sub(r"REPLACE_[A-Z_]+", "filled", template.read_text()))
    digest = "sha256:" + "a" * 64
    data["spec"]["template"]["spec"]["containers"][0]["image"] = "example.invalid/project/bot@" + digest
    path = tmp_path / "service.yaml"
    evidence = tmp_path / "result.json"
    scenarios = {name: {"passed": True} for name in ("schema", "spot", "v2-spot", "v2-perpetual", "upgrade")}
    evidence.write_text(json.dumps({"passed": True, "architecture": "amd64", "registry_manifest_digest": digest,
                                   "release_scope_complete": True, "scenarios": scenarios}))
    return data, path, evidence


def write(data, path):
    path.write_text(yaml.safe_dump(data))


def test_concrete_deployment_is_bound_to_passing_tested_manifest(candidate):
    data, path, evidence = candidate
    write(data, path)
    assert review(path, evidence=evidence).endswith("@sha256:" + "a" * 64)
    with pytest.raises(ValueError, match="E2E result"):
        review(path)
    evidence.write_text(json.dumps({"passed": True, "architecture": "amd64", "registry_manifest_digest": "sha256:" + "b" * 64}))
    with pytest.raises(ValueError, match="differs"):
        review(path, evidence=evidence)


def test_partial_smoke_report_cannot_approve_release(candidate):
    data, path, evidence = candidate
    write(data, path)
    result = json.loads(evidence.read_text())
    result["release_scope_complete"] = False
    evidence.write_text(json.dumps(result))
    with pytest.raises(ValueError, match="Complete offline release scope"):
        review(path, evidence=evidence)
    result["release_scope_complete"] = True
    result["scenarios"].pop("upgrade")
    evidence.write_text(json.dumps(result))
    with pytest.raises(ValueError, match="Every required"):
        review(path, evidence=evidence)


@pytest.mark.parametrize("mutation", ["cpu", "secret", "tag", "file-storage", "env-file"])
def test_deploy_review_rejects_settings_that_break_the_tested_contract(candidate, mutation):
    data, path, evidence = candidate
    revision = data["spec"]["template"]
    container = revision["spec"]["containers"][0]
    if mutation == "cpu":
        revision["metadata"]["annotations"]["run.googleapis.com/cpu-throttling"] = "true"
    elif mutation == "secret":
        next(item for item in container["env"] if item["name"] == "DATABASE_URL")["valueFrom"]["secretKeyRef"]["key"] = "latest"
    elif mutation == "file-storage":
        next(item for item in container["env"] if item["name"] == "STORAGE_BACKEND")["value"] = "file"
    elif mutation == "env-file":
        container["env"].append({"name": "ENV_FILE", "value": "/tmp/env"})
    else:
        container["image"] = "example.invalid/project/bot:latest"
    write(data, path)
    with pytest.raises(ValueError):
        review(path, evidence=evidence)


@pytest.mark.parametrize('missing', ['STORAGE_BACKEND', 'ENV', 'OKX_TRADING_ENV', 'ADMIN_API_ENABLED',
                                     'ADMIN_API_USERNAME', 'ADMIN_API_PASSWORD', 'HBOT_OKX_API_KEY'])
def test_deployment_requires_explicit_operating_environment_and_admin_access(candidate, missing):
    data, path, evidence = candidate
    container = data['spec']['template']['spec']['containers'][0]
    container['env'] = [item for item in container['env'] if item['name'] != missing]
    write(data, path)
    with pytest.raises(ValueError):
        review(path, evidence=evidence)


@pytest.mark.parametrize('name,value', [('ENV', 'dev'), ('ENV', 'DEV'), ('ENV', 'development'),
                                        ('ADMIN_API_ENABLED', 'false'), ('OKX_TRADING_ENV', 'unknown')])
def test_production_review_rejects_development_docs_and_disabled_admin(candidate, name, value):
    data, path, evidence = candidate
    env = data['spec']['template']['spec']['containers'][0]['env']
    env[:] = [item for item in env if item['name'] != name] + [{'name': name, 'value': value}]
    write(data, path)
    with pytest.raises(ValueError):
        review(path, evidence=evidence)


@pytest.mark.parametrize('name', ['ADMIN_API_USERNAME', 'ADMIN_API_PASSWORD', 'HBOT_OKX_DEMO_API_KEY',
                                  'HBOT_OKX_PERPETUAL_DEMO_SECRET_KEY'])
@pytest.mark.parametrize('source', ['plaintext', 'latest'])
def test_admin_and_demo_credentials_require_pinned_secrets(candidate, name, source):
    data, path, evidence = candidate
    env = data['spec']['template']['spec']['containers'][0]['env']
    item = {'name': name, 'value': 'not-a-real-secret'} if source == 'plaintext' else {
        'name': name, 'valueFrom': {'secretKeyRef': {'name': 'demo-secret', 'key': 'latest'}}}
    env[:] = [entry for entry in env if entry['name'] != name] + [item]
    write(data, path)
    with pytest.raises(ValueError):
        review(path, evidence=evidence)


def test_duplicate_environment_variables_are_rejected(candidate):
    data, path, evidence = candidate
    data['spec']['template']['spec']['containers'][0]['env'].append({'name': 'STORAGE_BACKEND', 'value': 'postgres'})
    write(data, path)
    with pytest.raises(ValueError, match='Duplicate'):
        review(path, evidence=evidence)


def test_explicit_demo_route_with_complete_demo_secret_trio_is_supported(candidate):
    data, path, evidence = candidate
    env = data['spec']['template']['spec']['containers'][0]['env']
    next(item for item in env if item['name'] == 'OKX_TRADING_ENV')['value'] = 'demo'
    for item in env:
        if re.fullmatch(r'HBOT_OKX_(PERPETUAL_)?(API_KEY|SECRET_KEY|PASSPHRASE)', item['name']):
            item['name'] = item['name'].replace('OKX_PERPETUAL_', 'OKX_PERPETUAL_DEMO_') if 'PERPETUAL' in item['name'] else item['name'].replace('OKX_', 'OKX_DEMO_')
    write(data, path)
    assert review(path, evidence=evidence)


def test_spot_only_deployment_does_not_require_perpetual_credentials(candidate):
    data, path, evidence = candidate
    env = data['spec']['template']['spec']['containers'][0]['env']
    env[:] = [item for item in env if not item['name'].startswith('HBOT_OKX_PERPETUAL_')]
    write(data, path)
    assert review(path, evidence=evidence)


@pytest.mark.parametrize('demo', [False, True])
def test_partial_perpetual_credentials_are_rejected(candidate, demo):
    data, path, evidence = candidate
    env = data['spec']['template']['spec']['containers'][0]['env']
    if demo:
        next(item for item in env if item['name'] == 'OKX_TRADING_ENV')['value'] = 'demo'
        for item in env:
            if item['name'].startswith('HBOT_OKX_'):
                item['name'] = item['name'].replace('OKX_PERPETUAL_', 'OKX_PERPETUAL_DEMO_') if 'PERPETUAL' in item['name'] else item['name'].replace('OKX_', 'OKX_DEMO_')
    env[:] = [item for item in env if item['name'] != 'HBOT_OKX_PERPETUAL_' + ('DEMO_' if demo else '') + 'PASSPHRASE']
    write(data, path)
    with pytest.raises(ValueError, match='complete perpetual'):
        review(path, evidence=evidence)


@pytest.mark.parametrize('project', ['', 'chosen-cloud-project'])
def test_deploy_target_requires_and_forwards_explicit_project(candidate, tmp_path, monkeypatch, project):
    import os
    import subprocess
    import sys

    data, path, evidence = candidate
    write(data, path)
    # Capture command arguments locally; never run a real gcloud command.
    shim = tmp_path / 'gcloud'
    calls = tmp_path / 'calls.txt'
    shim.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$GCLOUD_TEST_CALLS"\n')
    shim.chmod(0o755)
    monkeypatch.setenv('PATH', str(tmp_path) + os.pathsep + os.environ['PATH'])
    monkeypatch.setenv('GCLOUD_TEST_CALLS', str(calls))
    result = subprocess.run(['make', 'deploy-cloudrun', f'PYTHON={sys.executable}', f'CLOUDRUN_CONFIG={path}',
                             f'CLOUDRUN_EVIDENCE={evidence}', f'CLOUDRUN_PROJECT={project}'],
                            capture_output=True, text=True, cwd=Path(__file__).resolve().parents[3])
    if not project:
        assert result.returncode != 0
        assert not calls.exists()
        assert 'CLOUDRUN_PROJECT' in result.stderr + result.stdout
    else:
        assert result.returncode == 0, result.stderr
        args = calls.read_text().splitlines()
        assert args[args.index('--project') + 1] == project
        assert args[:3] == ['run', 'services', 'replace']


def test_perpetual_only_deployment_does_not_require_spot_credentials(candidate):
    data, path, evidence = candidate
    env = data['spec']['template']['spec']['containers'][0]['env']
    env[:] = [item for item in env if not re.fullmatch(r'HBOT_OKX_(API_KEY|SECRET_KEY|PASSPHRASE)', item['name'])]
    write(data, path)
    assert review(path, evidence=evidence)
