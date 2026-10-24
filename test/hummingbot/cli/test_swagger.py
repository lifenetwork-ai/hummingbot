import gzip
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from hummingbot.cli.admin_api import ADMIN_ENDPOINTS, ADMIN_PREFIX, AdminAPI
from hummingbot.cli.swagger import build_spec


def test_spec_uses_handler_annotations_and_shared_pydantic_contracts():
    spec = build_spec()
    assert set(spec["paths"]) == {ADMIN_PREFIX + path for _, path, _ in ADMIN_ENDPOINTS}
    command = spec["paths"][ADMIN_PREFIX + "/commands"]["post"]
    assert command["operationId"] == "submitCommand"
    assert command["security"] == [{"basicAuth": []}]
    assert "202" in command["responses"]
    assert command["parameters"][0]["name"] == "Idempotency-Key"
    assert command["parameters"][0]["required"]
    assert "StartCommand" in spec["components"]["schemas"]
    assert spec["components"]["securitySchemes"]["basicAuth"] == {"type": "http", "scheme": "basic"}


def test_missing_handler_annotation_fails_generation(monkeypatch):
    monkeypatch.setattr(AdminAPI.status, "api_documentation", None)
    with pytest.raises(ValueError, match="annotation"):
        build_spec()


def test_generation_is_deterministic_without_runtime_database_or_dotenv():
    with patch("hummingbot.client.config.runtime_config.load_environment", side_effect=AssertionError("No dotenv")), \
            patch("hummingbot.model.bot_state_store.BotStateStore.read", side_effect=AssertionError("No database")):
        assert json.dumps(build_spec(), sort_keys=True) == json.dumps(build_spec(), sort_keys=True)


def test_offline_cli_generation_and_drift_check(tmp_path):
    output = tmp_path / "openapi.json"
    env = {**os.environ, "ENV_FILE": str(tmp_path / "must-not-read.env"), "DATABASE_URL": "invalid-unused"}
    command = [sys.executable, "-m", "hummingbot.cli.swagger", "--output", str(output)]
    generated = subprocess.run(command, env=env, capture_output=True, timeout=30)
    assert generated.returncode == 0
    assert output.is_file()
    assert subprocess.run([*command, "--check"], env=env, capture_output=True, timeout=30).returncode == 0
    output.write_text("{}\n")
    assert subprocess.run([*command, "--check"], env=env, capture_output=True, timeout=30).returncode != 0


def test_response_money_fields_are_documented_as_decimal_strings():
    spec = build_spec()

    def resolve(schema):
        return spec["components"]["schemas"][schema["$ref"].rsplit("/", 1)[-1]] if "$ref" in schema else schema
    response = spec["paths"][ADMIN_PREFIX + "/fills"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    fill = resolve(resolve(response)["properties"]["items"]["items"])
    assert fill["properties"]["amount"].get("type") == "string"
    assert fill["properties"]["price"].get("type") == "string"


def test_bundled_swagger_assets_match_pinned_manifest():
    root = Path(__file__).resolve().parents[3] / "hummingbot/cli/static/swagger_ui"
    manifest = json.loads((root / "manifest.json").read_text())
    for name, expected in manifest["files"].items():
        packaged = (root / (name + ".gz")).read_bytes()
        assert hashlib.sha256(packaged).hexdigest() == expected["gzip_sha256"]
        assert hashlib.sha256(gzip.decompress(packaged)).hexdigest() == expected["upstream_sha256"]
