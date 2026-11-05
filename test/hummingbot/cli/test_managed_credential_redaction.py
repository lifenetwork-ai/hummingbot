import logging

from hummingbot.client.config.security import Security
from hummingbot.connector.credential_adapters import OKXCredentialAdapter
from hummingbot.core.credential_redaction import redact_candidate
from hummingbot.logger.service_logging import ServiceFormatter


def test_formatter_created_before_managed_load_redacts_current_values_without_retaining_old_values():
    config = OKXCredentialAdapter(environ={"OKX_TRADING_ENV": "demo"}).config("okx", dict(
        api_key="managed-key-canary", secret_key="managed-secret-canary", passphrase="managed-passphrase-canary"))
    old = Security._secure_configs, Security._environment_mode, Security._managed_mode, Security._redaction_secrets
    try:
        formatter = ServiceFormatter("json", {})
        Security.initialize_from_managed({"okx": config})
        record = logging.LogRecord("upstream", logging.ERROR, "test", 1, "Network error: managed-secret-canary", (), None)
        assert "managed-secret-canary" not in formatter.format(record)
        assert formatter.secrets == []
        Security.initialize_from_managed({})
        assert not Security._redaction_secrets
    finally:
        Security._secure_configs, Security._environment_mode, Security._managed_mode, Security._redaction_secrets = old


def test_candidate_redaction_is_local_to_its_request_context():
    formatter = ServiceFormatter("json", {})
    record = logging.LogRecord("upstream", logging.ERROR, "test", 1, "candidate-canary", (), None)
    with redact_candidate(["candidate-canary"]):
        assert "candidate-canary" not in formatter.format(record)
    assert formatter.secrets == []
