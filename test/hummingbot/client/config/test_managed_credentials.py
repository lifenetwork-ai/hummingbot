from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from hummingbot.client.config.managed_credentials import ManagedCredentialSource
from hummingbot.client.config.runtime_config import RuntimeConfigurationError
from hummingbot.client.config.security import Security
from hummingbot.connector.credential_adapters import CredentialAdapters, OKXCredentialAdapter
from hummingbot.core.credential_encryption import CredentialCipher, CredentialContext, LocalKeyProvider


@pytest.fixture
def source():
    cipher = CredentialCipher(LocalKeyProvider(b"K" * 32, "local-v1"))
    adapter = OKXCredentialAdapter(environ={"OKX_TRADING_ENV": "demo", "OKX_SITE": "www"})
    envelope = cipher.encrypt(dict(api_key="canary-key", secret_key="canary-secret", passphrase="canary-passphrase"),
                              CredentialContext("bot", "okx", "okx", "demo", 1, "www:account"))
    store = SimpleNamespace(bot_id="bot", load=Mock(return_value=dict(exchange="okx", connector="okx", environment="demo",
                                                                      version=1, state="active", account_id="www:account", envelope=envelope.data)))
    return ManagedCredentialSource(store, cipher, CredentialAdapters([adapter], enabled_connectors={"okx"}))


def test_source_reuses_connector_model_and_never_reads_environment_fallback(source, monkeypatch):
    monkeypatch.setenv("HBOT_OKX_DEMO_API_KEY", "ENV-CANARY")
    configs, versions = source.load_configs(["okx"])
    assert configs["okx"].okx_api_key.get_secret_value() == "canary-key"
    assert versions == {"okx": 1}
    source.store.load.return_value = None
    with pytest.raises(RuntimeConfigurationError, match="credentials_not_configured"):
        source.load_configs(["okx"])


def test_wrong_site_or_revoking_bundle_is_not_activated(source):
    source.store.load.return_value["account_id"] = "app:account"
    with pytest.raises(RuntimeConfigurationError):
        source.load_configs(["okx"])
    source.store.load.return_value["account_id"] = "www:account"
    source.store.load.return_value["state"] = "revoking"
    with pytest.raises(RuntimeConfigurationError):
        source.load_configs(["okx"])


def test_security_never_lazy_loads_an_env_key_in_managed_mode(source, monkeypatch):
    monkeypatch.setenv("HBOT_OKX_DEMO_API_KEY", "ENV-CANARY")
    old = (Security._secure_configs, Security._environment_mode, getattr(Security, "_managed_mode", False))
    try:
        configs, _ = source.load_configs(["okx"])
        Security.initialize_from_managed(configs)
        assert Security.api_keys("okx")["okx_api_key"] == "canary-key"
        Security.initialize_from_managed({})
        with pytest.raises(RuntimeConfigurationError, match="credentials_not_configured"):
            Security.api_keys("okx")
        with pytest.raises(RuntimeError):
            Security.decrypt_all()
    finally:
        Security.initialize_from_managed({})
        Security._secure_configs, Security._environment_mode, Security._managed_mode = old


@pytest.mark.parametrize("names", [["okx"], ["okx_perpetual"], ["okx", "okx_perpetual"]])
def test_spot_perpetual_and_both_load_existing_models_with_the_same_account(names):
    cipher = CredentialCipher(LocalKeyProvider(b"K" * 32, "local-v1"))
    adapter = OKXCredentialAdapter(environ={"OKX_TRADING_ENV": "demo", "OKX_SITE": "www"})

    def load(exchange, name, environment):
        envelope = cipher.encrypt(dict(api_key="key", secret_key="secret", passphrase="passphrase"),
                                  CredentialContext("bot", exchange, name, environment, 1, "www:account"))
        return dict(version=1, state="active", account_id="www:account", envelope=envelope.data)

    source = ManagedCredentialSource(SimpleNamespace(bot_id="bot", load=load), cipher,
                                     CredentialAdapters([adapter], enabled_connectors={"okx", "okx_perpetual"}))
    configs, versions = source.load_configs(names)
    assert set(configs) == set(versions) == set(names)
    assert all(getattr(configs[name], name + "_api_key").get_secret_value() == "key" for name in names)
