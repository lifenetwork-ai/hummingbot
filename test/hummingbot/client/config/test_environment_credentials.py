import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hummingbot.client.config import config_helpers
from hummingbot.client.config.environment_credentials import load_connector_credentials
from hummingbot.client.config.runtime_config import RuntimeConfigurationError
from hummingbot.client.config.security import Security

VALUES = {"K_SERVICE": "offline-test", "DATABASE_URL": "postgresql://unused/db",
          "HBOT_OKX_API_KEY": "test-key", "HBOT_OKX_SECRET_KEY": "test-secret", "HBOT_OKX_PASSPHRASE": "test-pass"}


def teardown_module():
    # Legacy unittest modules still use get_event_loop after pytest-asyncio / the
    # CLI's asyncio.run closes its loop. Match existing runner-test isolation.
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())


@pytest.fixture
def env_credentials():
    original = (Security._secure_configs, Security._environment_mode, Security.secrets_manager, Security._decryption_done)
    with patch.dict(os.environ, VALUES, clear=True):
        try:
            yield
        finally:
            for name in Security._secure_configs:
                config_helpers.reset_connector_hb_config(name)
            Security._secure_configs, Security._environment_mode, Security.secrets_manager, Security._decryption_done = original
            for config in Security._secure_configs.values():
                config_helpers.update_connector_hb_config(config)


@pytest.mark.parametrize("connector,prefix", [("okx", "HBOT_OKX_"), ("okx_perpetual", "HBOT_OKX_PERPETUAL_")])
def test_existing_models_validate_environment_input(connector, prefix):
    cfg = load_connector_credentials(connector, {prefix + "API_KEY": "key", prefix + "SECRET_KEY": "credential-secret-value",
                                                 prefix + "PASSPHRASE": "pass"})
    keys = config_helpers.api_keys_from_connector_config_map(cfg)
    assert keys[connector + "_api_key"] == "key"
    assert keys[connector + "_secret_key"] == "credential-secret-value"
    assert "credential-secret-value" not in repr(cfg.hb_config)


def test_missing_or_partial_input_does_not_fall_back(env_credentials):
    Security.initialize_from_environment([])
    assert Security.is_decryption_done()
    assert Security.api_keys("okx")["okx_api_key"] == "test-key"
    with pytest.raises(RuntimeConfigurationError, match="HBOT_OKX_PERPETUAL_API_KEY"):
        Security.api_keys("okx_perpetual")
    os.environ["HBOT_OKX_API_KEY"] = ""
    with pytest.raises(RuntimeConfigurationError, match="HBOT_OKX_API_KEY"):
        load_connector_credentials("okx")


def test_validation_errors_never_echo_secret_input():
    values = {**VALUES, "HBOT_OKX_REGISTRATION_SUB_DOMAIN": "do-not-log-this-input"}
    with pytest.raises(RuntimeConfigurationError) as error:
        load_connector_credentials("okx", values)
    assert "do-not-log-this-input" not in str(error.value)
    assert "okx_registration_sub_domain" in str(error.value)


def test_environment_initialization_and_use_never_touch_keystore(env_credentials):
    with patch("hummingbot.client.config.security.validate_password", side_effect=AssertionError("No password")), \
            patch("hummingbot.client.config.security.list_connector_configs", side_effect=AssertionError("No secret files")), \
            patch("hummingbot.client.config.security.save_to_yml", side_effect=AssertionError("No file writes")):
        Security.initialize_from_environment(["okx"])
        assert Security.secrets_manager is None
        assert Security.api_keys("okx")["okx_passphrase"] == "test-pass"
        with pytest.raises(RuntimeError, match="read-only"):
            Security.update_secure_config(Security.decrypted_value("okx"))
        with pytest.raises(RuntimeError, match="serialized"):
            Security.decrypted_value("okx").generate_yml_output_str_with_comments()
        with pytest.raises(RuntimeError, match="decryption"):
            Security.decrypted_value("okx").decrypt_all_secure_data()


def test_readonly_client_and_ssl_loading_preserves_files(tmp_path):
    client = tmp_path / "client.yml"
    ssl = tmp_path / "ssl.yml"
    client.write_text("# immutable client config\n")
    ssl.write_text("# immutable SSL config\n")
    with patch.object(config_helpers, "CLIENT_CONFIG_PATH", client), \
            patch.object(config_helpers, "GATEWAY_SSL_CONF_FILE", ssl), \
            patch.object(config_helpers, "save_to_yml", side_effect=AssertionError("No write-back")):
        config_helpers.load_client_config_map_from_file(read_only=True)
        config_helpers.load_ssl_config_map_from_file(read_only=True)
    assert client.read_text() == "# immutable client config\n"
    assert ssl.read_text() == "# immutable SSL config\n"


@pytest.mark.asyncio
async def test_readonly_system_config_does_not_upgrade_files(tmp_path):
    fee = tmp_path / "fees.yml"
    fee.write_text("template_version: -1\n")
    with patch.object(config_helpers, "TRADE_FEES_CONFIG_PATH", fee), \
            patch.object(config_helpers.shutil, "copy", side_effect=AssertionError("No template writes")), \
            patch.object(config_helpers, "save_system_configs_to_yml", side_effect=AssertionError("No config writes")):
        await config_helpers.read_system_configs_from_yml(read_only=True)
    assert fee.read_text() == "template_version: -1\n"


@pytest.mark.asyncio
async def test_environment_bootstrap_reuses_application_without_password_or_config_writes(env_credentials):
    from hummingbot.client import runner
    from hummingbot.client.config.client_config_map import ClientConfigMap
    ccm = config_helpers.ClientConfigAdapter(ClientConfigMap())
    with patch.object(Security, "login", side_effect=AssertionError("No encrypted login")), \
            patch.object(config_helpers, "create_yml_files_legacy", side_effect=AssertionError("No template writes")), \
            patch.object(config_helpers, "read_system_configs_from_yml", new=AsyncMock()) as read, \
            patch("hummingbot.init_logging") as logging, \
            patch.object(runner.HummingbotApplication, "main_application", return_value="shared-app") as application:
        result = await runner.bootstrap_application(ccm, None, headless=True, environment_connectors=["okx"],
                                                    read_only_configuration=True)
    assert result == "shared-app"
    read.assert_awaited_once_with(read_only=True)
    assert logging.call_args.kwargs["console_only"]
    application.assert_called_once_with(client_config_map=ccm, headless_mode=True, read_only_configuration=True)


@pytest.mark.asyncio
async def test_controller_wrapper_runs_in_memory(tmp_path):
    from hummingbot.client import runner
    hb = MagicMock()
    hb.trading_core.start_strategy = AsyncMock(return_value=True)
    (tmp_path / "controller.yml").write_text("controller_name: pmm_simple\ncontroller_type: market_making\n")
    with patch("hummingbot.client.settings.CONTROLLERS_CONF_DIR_PATH", tmp_path):
        assert await runner.load_and_start_strategy(hb, controller_conf="controller.yml", headless=True,
                                                    read_only_configuration=True)
    hb.trading_core.start_strategy.assert_awaited_once_with(
        "v2_with_controllers", runner.controller_runner_config("controller.yml"), "controller.yml")
    assert [path.name for path in tmp_path.iterdir()] == ["controller.yml"]


def test_env_cli_login_uses_no_password(env_credentials):
    from hummingbot.cli.password import login
    with patch("hummingbot.cli.password.resolve_password", side_effect=AssertionError("No password")), \
            patch("hummingbot.cli.password.unlock_keystore", side_effect=AssertionError("No keystore")), \
            patch.object(config_helpers, "load_client_config_map_from_file", return_value="config") as load:
        assert login(connectors=["okx"]) == ("config", None)
    load.assert_called_once_with(read_only=True)


def test_help_and_version_work_without_db_or_credentials(env_credentials):
    from typer.testing import CliRunner

    from hummingbot.cli.main import app
    with patch.dict(os.environ, {}, clear=True):
        for args in (["--help"], ["--version"]):
            result = CliRunner().invoke(app, args)
            assert result.exit_code == 0, result.output


def test_environment_connect_uses_existing_balance_check_without_storing_keys(env_credentials):
    from typer.testing import CliRunner

    from hummingbot.cli.main import app
    from hummingbot.client.config.client_config_map import ClientConfigMap
    ccm = config_helpers.ClientConfigAdapter(ClientConfigMap())
    user_balances = MagicMock()
    user_balances.update_exchanges = AsyncMock(return_value={"okx": "test-secret must not be echoed"})
    with patch("hummingbot.cli.commands.connect._connectable_exchanges", return_value=["okx"]), \
            patch("hummingbot.user.user_balances.UserBalances.instance", return_value=user_balances), \
            patch.object(config_helpers, "load_client_config_map_from_file", return_value=ccm), \
            patch.object(Security, "update_secure_config", side_effect=AssertionError("No secret writes")):
        result = CliRunner().invoke(app, ["connect", "okx"])
    assert result.exit_code == 0, result.output
    assert "test-secret" not in result.output
    assert "connection check failed" in result.output
    user_balances.update_exchanges.assert_awaited_once()


def test_environment_balance_uses_selected_connector_and_existing_fetch(env_credentials):
    from typer.testing import CliRunner

    from hummingbot.cli.main import app
    from hummingbot.client.config.client_config_map import ClientConfigMap
    ccm = config_helpers.ClientConfigAdapter(ClientConfigMap())
    payload = {"okx": {"assets": [], "allocated_total": 0, "usd_total": 0}}
    with patch.object(config_helpers, "load_client_config_map_from_file", return_value=ccm), \
            patch("hummingbot.cli.commands.balance._fetch_one", new=AsyncMock(return_value=(payload, None))) as fetch:
        result = CliRunner().invoke(app, ["balance", "okx", "--units-only", "--json"])
    assert result.exit_code == 0, result.output
    assert '"okx"' in result.output
    assert "test-secret" not in result.output
    fetch.assert_awaited_once()
