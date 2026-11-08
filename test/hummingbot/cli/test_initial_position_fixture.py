"""The seeded both-market E2E must provide credentials before its first boot."""
from unittest.mock import MagicMock

import pytest

from hummingbot.client.config.environment_credentials import load_connector_credentials
from scripts.test_e2e_offline import ROOT, Harness


@pytest.mark.parametrize('environment', ['live', 'demo'])
def test_seeded_both_market_fixture_has_both_credentials_before_start(tmp_path, monkeypatch, environment):
    (tmp_path / 'life_mm_profiles.yaml').write_text(
        (ROOT / 'deploy/cloudrun/conf/life_mm_profiles.yaml').read_text())
    harness = Harness('unused-offline-image', tmp_path, tmp_path / 'report', environment)
    harness.control = MagicMock()

    class FixtureReady(Exception):
        pass

    def first_start(revision, **kwargs):
        assert revision == 'initial-seed'
        # Use the real adapter with an explicit fixture mapping. Never read
        # operator .env values or create a container/network/exchange request.
        for connector in ('okx', 'okx_perpetual'):
            load_connector_credentials(connector, environ=harness.env)
        raise FixtureReady

    monkeypatch.setattr(harness, 'start', first_start)
    with pytest.raises(FixtureReady):
        harness.initial_position_restart_scenario()
    assert harness.control.call_count == 1
    assert {row['posSide'] for row in harness.control.call_args.kwargs['initial_positions']} == {'long', 'short'}
