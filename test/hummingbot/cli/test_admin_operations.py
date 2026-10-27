"""Operator-facing contracts: YAML owns profiles, HTTP owns session intent."""
from test.hummingbot.cli.test_admin_api import PAYLOAD, client

import pytest

from hummingbot.cli.admin_api import ADMIN_PREFIX
from hummingbot.model.admin_store import IdempotencyConflict


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [
    ("references", [{"pair": "AVAX-USDT", "weight": "1"}]),
    ("references", None), ("profile_ids", ["sideways"]), ("step_duration_seconds", 600),
    ("total_amount_quote", "100"), ("leverage", 2),
    ('spread_policy', {'volatility': {'defense': {'reduced_size_factor': '.5'}}}),
])
async def test_api_rejects_yaml_overrides_with_safe_field_errors(field, value):
    async with client() as (http, storage):
        response = await http.post(ADMIN_PREFIX + "/commands", json={**PAYLOAD, field: value},
                                   headers={"Idempotency-Key": "readonly"})
        body = await response.json()
        assert response.status == 422
        assert body["code"] == "yaml_configuration_read_only"
        assert body["fields"][0]["path"] == field
        assert body["request_id"] == response.headers["X-Request-ID"]
        storage.command.assert_not_called()


@pytest.mark.asyncio
async def test_field_errors_never_echo_invalid_inputs_and_expose_request_identity():
    async with client() as (http, _):
        response = await http.post(ADMIN_PREFIX + "/commands", json={**PAYLOAD, "duration_seconds": "private-marker"},
                                   headers={"Idempotency-Key": "invalid", "Origin": "https://ui.example"})
        body = await response.json()
        assert response.status == 422 and body["error"] == "validation_failed"
        assert body["fields"][0]["path"] == "duration_seconds"
        assert "private-marker" not in str(body)
        assert "X-Request-ID" in response.headers["Access-Control-Expose-Headers"]
        assert body["retry"] == "fix_request"


@pytest.mark.asyncio
async def test_retry_key_conflict_is_distinct_and_legacy_receipt_still_readable():
    async with client() as (http, storage):
        storage.command.side_effect = IdempotencyConflict("private-error-message")
        response = await http.post(ADMIN_PREFIX + "/commands", json=PAYLOAD, headers={"Idempotency-Key": "same"})
        body = await response.json()
        assert response.status == 409 and body["code"] == "idempotency_key_reused"
        assert "private-error-message" not in str(body)
        storage.command_by_id.return_value["payload"] = {**PAYLOAD, "references": [{"pair": "AVAX-USDT", "weight": "1"}]}
        receipt = await http.get(ADMIN_PREFIX + "/commands/command-1")
        assert receipt.status == 200
        assert (await receipt.json())["payload"]["references"][0]["pair"] == "AVAX-USDT"
