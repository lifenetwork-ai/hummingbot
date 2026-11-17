"""Positive dual-market fixtures must supply both reviewed connector models."""
from importlib import import_module
from unittest.mock import Mock

import pytest

from hummingbot.client.config.environment_credentials import load_connector_credentials
from hummingbot.client.config.runtime_config import RuntimeConfigurationError
from scripts.test_e2e_offline import Harness


class StartBoundary(Exception):
    pass


@pytest.mark.parametrize('module', ['mm_quote_refresh_checks', 'mm_feed_recovery_checks', 'mm_post_only_checks', 'mm_pending_terminal_checks'])
@pytest.mark.parametrize('environment', ['live', 'demo'])
def test_dual_market_fixture_is_fully_configured_before_start(tmp_path, module, environment):
    path = tmp_path / 'life_mm_profiles.yaml'
    original = 'markets:\n  spot: {}\n  perpetual: {}\n'
    path.write_text(original)
    harness = Harness('unused-image', tmp_path, tmp_path, trading_environment=environment)
    harness.control = Mock()

    def start(*args, **kwargs):
        for connector in ('okx', 'okx_perpetual'):
            load_connector_credentials(connector, environ=harness.env)
        raise StartBoundary()

    harness.start = start
    with pytest.raises(StartBoundary):
        import_module('scripts.' + module).scenario(harness)
    assert path.read_text() == original
    if environment == 'demo':
        assert 'HBOT_OKX_PERPETUAL_API_KEY' not in harness.env


@pytest.mark.parametrize('environment', ['live', 'demo'])
def test_missing_perpetual_fixture_bundle_still_fails_without_demo_live_fallback(tmp_path, environment):
    harness = Harness('unused-image', tmp_path, tmp_path, trading_environment=environment)
    prefix = 'HBOT_OKX_PERPETUAL_DEMO_' if environment == 'demo' else 'HBOT_OKX_PERPETUAL_'
    for field in ('API_KEY', 'SECRET_KEY', 'PASSPHRASE'):
        harness.env.pop(prefix + field, None)
        if environment == 'demo':
            harness.env['HBOT_OKX_PERPETUAL_' + field] = 'unused-live-dummy'
    with pytest.raises(RuntimeConfigurationError, match='Missing connector environment values'):
        load_connector_credentials('okx_perpetual', environ=harness.env)
