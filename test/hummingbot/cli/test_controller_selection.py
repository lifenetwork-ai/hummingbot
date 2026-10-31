"""Controller identity lookup reuses a validated catalogue, not an authority cache."""
import asyncio
import random
from decimal import Decimal
from test.hummingbot.client.config.test_mm_profiles import CATALOGUE
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from hummingbot.cli.service import ServiceHost
from hummingbot.client.config.mm_profiles import MMProfilesConfig
from hummingbot.client.config.runtime_config import RuntimeSettings
from hummingbot.core.mm_session import MMSession, MMSessionRequest


@pytest.fixture
def host():
    catalogue = asyncio.run(MMProfilesConfig.load(CATALOGUE))
    settings = RuntimeSettings.from_environment({'DATABASE_URL': 'postgresql://unused/test'})
    service = ServiceHost(settings, asyncio.Event(), MagicMock())
    service.mm_session = MMSession.create(catalogue, MMSessionRequest(
        market_mode='both', profile_id='follow_market', duration_seconds=60), rng=random.Random(1))
    service.owner = SimpleNamespace(volatility_checkpoints=None)
    service.hb = SimpleNamespace(trading_core=SimpleNamespace(strategy=SimpleNamespace(controllers={})))
    bind_controllers(service)
    return service


def bind_controllers(host):
    session = host.mm_session
    configs = session.catalogue.controller_configs(session.profile_id, session.request.market_mode, None,
                                                   references=session.request.references)
    values = {}
    for cfg in configs:
        cfg = cfg.model_copy(update=dict(mm_session_id=session.id, mm_generation=session.generation))
        values[cfg.id] = SimpleNamespace(config=cfg, update_reference=lambda: None,
                                         processed_data={'ready': True}, exposure_within_caps=lambda: True,
                                         market_data_provider=SimpleNamespace(ready=True), executors_info=[])
    host.hb.trading_core.strategy.controllers = values
    return list(values.values())


@pytest.mark.parametrize('mode', ['spot', 'perpetual', 'both'])
@pytest.mark.parametrize('profile', ['follow_market', 'sideways', 'follow_token'])
def test_valid_lookup_selects_exact_controller_set_without_rebuilding_configs(host, mode, profile):
    host.mm_session.request.market_mode, host.mm_session.profile_id = mode, profile
    expected = bind_controllers(host)
    with patch.object(MMProfilesConfig, 'controller_configs', side_effect=AssertionError('Redundant config build')):
        assert host._mm_controllers() == expected
    host.hb.trading_core.strategy.controllers['unexpected'] = object()
    assert host._mm_controllers() == []
    host.hb.trading_core.strategy.controllers.clear()
    assert host._mm_controllers() == []


@pytest.mark.parametrize('invalid', ['profile', 'mode', 'missing_market', 'combined_budget'])
def test_invalid_selection_is_still_rejected(host, invalid):
    if invalid == 'profile':
        host.mm_session.profile_id = 'unknown'
    elif invalid == 'mode':
        host.mm_session.request.market_mode = 'paper'
    elif invalid == 'missing_market':
        host.mm_session.catalogue.markets.pop('perpetual')
    else:
        host.mm_session.catalogue.max_total_amount_quote = Decimal('100')
    with pytest.raises(ValueError):
        host._mm_controllers()


def test_lookup_has_no_cross_session_or_market_mode_cache(host):
    original = host._mm_controllers()
    host.mm_session = MMSession.create(host.mm_session.catalogue, MMSessionRequest(
        market_mode='spot', profile_id='sideways', duration_seconds=60), rng=random.Random(2))
    assert host._mm_controllers() == []  # Old both-market set cannot match the new selection.
    current = bind_controllers(host)
    assert host._mm_controllers() == current and current != original
    host.mm_session = None
    assert host._mm_controllers() == []


def test_legacy_reference_override_keeps_full_selection_validation(host):
    from controllers.market_making.life_mm import ReferencePair
    host.mm_session.request.references = [ReferencePair(pair='AVAX-USDT', weight=Decimal('1'))]
    with patch.object(MMProfilesConfig, 'controller_configs', wraps=host.mm_session.catalogue.controller_configs) as build:
        assert len(host._mm_controllers()) == 2
        build.assert_called_once()
    host.mm_session.request.references = [ReferencePair(pair='AVAX-USDT', weight=Decimal('.5'))]
    with pytest.raises(ValueError, match='weights totaling 1'):
        host._mm_controllers()


def test_readiness_keeps_generation_exposure_and_fresh_collateral_checks(host):
    host._mm_available_quote = MagicMock(return_value=Decimal('200'))
    controllers = host._mm_controllers()
    assert host.mm_runtime_ready()
    host._mm_available_quote.assert_called_once_with(controllers)
    controllers[0].config.mm_generation += 1
    assert not host.mm_runtime_ready() and host.mm_readiness_reason == 'session_identity_mismatch'
    controllers[0].config.mm_generation -= 1
    controllers[0].exposure_within_caps = lambda: False
    assert not host.mm_runtime_ready() and host.mm_readiness_reason == 'exposure_limit'
    controllers[0].exposure_within_caps = lambda: True
    host._mm_available_quote.return_value = Decimal('-1')
    assert not host.mm_runtime_ready() and host.mm_readiness_reason == 'insufficient_collateral'


def test_invalid_yaml_still_fails_full_catalogue_admission(host):
    raw = host.mm_session.catalogue.model_dump(mode='json')
    raw['profiles']['follow_market']['references'][0]['weight'] = '.2'
    with pytest.raises(ValueError, match='weights totaling 1'):
        MMProfilesConfig.model_validate(raw)
