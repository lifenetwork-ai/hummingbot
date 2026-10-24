import pytest

from hummingbot.client.config.environment_credentials import load_connector_credentials
from hummingbot.client.config.runtime_config import RuntimeConfigurationError


@pytest.mark.parametrize("name,prefix", [("okx", "HBOT_OKX_"), ("okx_perpetual", "HBOT_OKX_PERPETUAL_")])
def test_demo_credentials_never_fall_back_to_live(name, prefix):
    values = {"OKX_TRADING_ENV": "demo", prefix + "API_KEY": "live-key", prefix + "SECRET_KEY": "live-secret", prefix + "PASSPHRASE": "live-pass"}
    with pytest.raises(RuntimeConfigurationError, match="DEMO"):
        load_connector_credentials(name, values)
    values.update({prefix + "DEMO_API_KEY": "demo-key", prefix + "DEMO_SECRET_KEY": "demo-secret", prefix + "DEMO_PASSPHRASE": "demo-pass"})
    loaded = load_connector_credentials(name, values)
    assert getattr(loaded, name + "_api_key").get_secret_value() == "demo-key"


def test_demo_site_applies_to_spot_credential_model():
    values = dict(OKX_TRADING_ENV="demo", OKX_SITE="app", HBOT_OKX_DEMO_API_KEY="demo-key", HBOT_OKX_DEMO_SECRET_KEY="demo-secret", HBOT_OKX_DEMO_PASSPHRASE="demo-pass")
    assert load_connector_credentials("okx", values).okx_registration_sub_domain == "app"
