"""YAML defaults select new sessions; recorded deadlines keep their authority."""
import hashlib
import json
from pathlib import Path
from random import Random
from test.hummingbot.client.config.test_mm_profiles import CATALOGUE
from test.hummingbot.core.test_mm_until_stopped import anchors

import pytest

from hummingbot.cli.admin_models import COMMAND_REQUEST
from hummingbot.client.config.mm_profiles import MMProfilesConfig
from hummingbot.core.mm_session import MMSession, MMSessionRequest


async def continuous_catalogue():
    raw = (await MMProfilesConfig.load(CATALOGUE)).model_dump(mode='json')
    return MMProfilesConfig.model_validate({**raw, 'allow_until_stopped': True, 'default_duration_mode': 'until_stopped'})


@pytest.mark.asyncio
@pytest.mark.parametrize('action', ['start', 'switch'])
@pytest.mark.parametrize('fields,mode,seconds', [
    ({}, 'until_stopped', None),
    ({'duration_mode': None}, 'until_stopped', None),
    ({'duration_mode': 'until_stopped'}, 'until_stopped', None),
    ({'duration_mode': 'timed'}, 'timed', 3600),
    ({'duration_seconds': 60}, 'timed', 60),
    ({'duration_mode': 'timed', 'duration_seconds': 60}, 'timed', 60),
])
async def test_omitted_mode_uses_yaml_but_explicit_time_controls_win(action, fields, mode, seconds):
    body = dict(action=action, expected_version=0, market_mode='both', selection='random', **fields)
    if action == 'switch':
        body['session_id'] = 'prior-session'
    selected = COMMAND_REQUEST.validate_python(body).session_request(await continuous_catalogue())
    assert (selected.duration_mode, selected.duration_seconds) == (mode, seconds)


@pytest.mark.asyncio
async def test_continuous_default_requires_yaml_permission_and_rejects_unknown_values():
    raw = (await MMProfilesConfig.load(CATALOGUE)).model_dump(mode='json')
    for changes in ({'default_duration_mode': 'until_stopped', 'allow_until_stopped': False},
                    {'default_duration_mode': 'forever'}, {'default_duration_mode': None}):
        with pytest.raises(ValueError):
            MMProfilesConfig.model_validate({**raw, **changes})
    with pytest.raises(ValueError):
        COMMAND_REQUEST.validate_python(dict(action='start', expected_version=0, market_mode='both', selection='random',
                                             duration_mode='until_stopped', duration_seconds=60)).session_request(await continuous_catalogue())


@pytest.mark.asyncio
async def test_legacy_timed_catalogue_hash_and_restored_session_do_not_change():
    cfg = await MMProfilesConfig.load(CATALOGUE)
    raw = cfg.model_dump(mode='json')
    assert 'default_duration_mode' not in raw
    expected = hashlib.sha256(json.dumps(raw, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
    explicit = MMProfilesConfig.model_validate({**raw, 'default_duration_mode': 'timed'})
    assert explicit.catalogue_hash() == expected
    selected = COMMAND_REQUEST.validate_python(dict(action='start', expected_version=0, market_mode='spot', profile_id='sideways')).session_request(cfg)
    assert selected.duration_mode == 'timed' and selected.duration_seconds == cfg.default_duration_seconds
    session = MMSession.create(cfg, MMSessionRequest(market_mode='spot', profile_id='sideways', duration_seconds=60), rng=Random(1))
    session.ready(now=1000, anchors=anchors(session))
    saved = session.model_dump(mode='json')
    assert 'default_duration_mode' not in saved['catalogue']
    restored = MMSession.model_validate(saved)
    assert restored.expires_at == 1060 and restored.config_hash == session.config_hash
    assert restored.request.duration_mode == 'timed'


@pytest.mark.asyncio
async def test_demo_preset_defaults_to_continuous_without_enabling_reduction():
    cfg = await MMProfilesConfig.load(Path('deploy/cloudrun/demo/life_mm_profiles.yaml'))
    assert cfg.default_duration_mode == 'until_stopped' and cfg.allow_until_stopped
    assert cfg.markets['perpetual'].inventory_reduction.mode == 'disabled'
