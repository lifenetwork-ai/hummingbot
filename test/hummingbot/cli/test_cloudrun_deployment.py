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
