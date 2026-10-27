from copy import deepcopy
from random import Random
from test.hummingbot.client.config.test_mm_profiles import CATALOGUE

import pytest

from hummingbot.cli.admin_models import COMMAND_REQUEST
from hummingbot.client.config.mm_profiles import MMProfilesConfig
from hummingbot.core.mm_session import MMSession, MMSessionRequest, session_permits


async def catalogue(enabled=True):
    original = await MMProfilesConfig.load(CATALOGUE)
    return MMProfilesConfig.model_validate({**original.model_dump(mode='json'), 'allow_until_stopped': enabled})


def request(**changes):
    return MMSessionRequest(market_mode='both', selection='random', duration_mode='until_stopped', **changes)


def anchors(session):
    return {config.id: {'life': '.1', 'sources': {ref.pair: '100' for ref in config.references}}
            for config in session.catalogue.controller_configs(session.profile_id, session.request.market_mode, None)}


@pytest.mark.asyncio
async def test_random_until_stopped_survives_long_gaps_without_extending_or_replaying_windows():
    session = MMSession.create(await catalogue(), request(), rng=Random(1))
    session.ready(now=1000, anchors=anchors(session))
    assert session.expires_at is None and session.step_expires_at == 1600
    assert session.permits(session_id=session.id, generation=0, now=1599)
    assert not session.permits(session_id=session.id, generation=0, now=1600)
    saved = session.model_dump_json()
    restored = MMSession.model_validate_json(saved)
    restored.ready(now=1200, anchors=anchors(restored))
    assert restored.model_dump_json() == saved
    assert restored.advance(now=1000 + 86400 * 30 + 1, rng=Random(2))
    assert restored.status == 'preparing' and restored.step_index == 4320
    assert restored.expires_at is None and restored.step_expires_at == 1000 + 86400 * 30 + 600
    assert not restored.permits(session_id=session.id, generation=0, now=1000 + 86400 * 30 + 1)
    restored.ready(now=1000 + 86400 * 30 + 2, anchors=anchors(restored))
    assert restored.permits(session_id=session.id, generation=1, now=1000 + 86400 * 30 + 2)


@pytest.mark.asyncio
async def test_fixed_until_stopped_has_no_session_or_step_deadline_and_stop_revokes_it():
    selected = MMSessionRequest(market_mode='spot', selection='fixed', profile_id='sideways', duration_mode='until_stopped')
    session = MMSession.create(await catalogue(), selected, rng=Random(1))
    session.ready(now=1000, anchors=anchors(session))
    assert session.expires_at is session.step_expires_at is None
    assert session.permits(session_id=session.id, generation=0, now=1000 + 86400 * 365)
    assert not session.advance(now=1000 + 86400 * 365, rng=Random(2))
    session.status = 'stopping'
    assert not session.permits(session_id=session.id, generation=0, now=1001)


@pytest.mark.asyncio
async def test_yaml_permission_and_mutually_exclusive_duration_controls_are_enforced():
    with pytest.raises(ValueError):
        MMSession.create(await catalogue(False), request(), rng=Random(1))
    for fields in ({'duration_mode': 'timed'}, {'duration_mode': 'until_stopped', 'duration_seconds': 600}):
        with pytest.raises(ValueError):
            MMSessionRequest(market_mode='spot', selection='random', **fields)
    cfg = await catalogue()
    body = dict(action='start', expected_version=0, market_mode='both', selection='random', duration_mode='until_stopped')
    selected = COMMAND_REQUEST.validate_python(body).session_request(cfg)
    assert selected.duration_seconds is None and selected.duration_mode == 'until_stopped'
    with pytest.raises(ValueError):
        COMMAND_REQUEST.validate_python({**body, 'duration_seconds': 600}).session_request(cfg)


@pytest.mark.asyncio
async def test_missing_timed_deadlines_or_permission_cannot_become_unlimited_send_authority():
    session = MMSession.create(await catalogue(), request(), rng=Random(1))
    session.ready(now=1000, anchors=anchors(session))
    raw = session.model_dump(mode='json')
    for change in ('permission', 'step', 'mode', 'duration', 'selection'):
        broken = deepcopy(raw)
        if change == 'permission':
            broken['catalogue']['allow_until_stopped'] = False
        elif change == 'step':
            broken['step_expires_at'] = None
        elif change == 'mode':
            broken['request']['duration_mode'] = 'timed'
        elif change == 'duration':
            broken['request']['duration_seconds'] = 600
        else:
            broken['request']['selection'] = 'unknown'
        assert not session_permits(broken, session_id=session.id, generation=0, now=1001), change


@pytest.mark.asyncio
async def test_old_timed_snapshots_keep_original_hash_and_deadlines():
    cfg = await MMProfilesConfig.load(CATALOGUE)
    session = MMSession.create(cfg, MMSessionRequest(market_mode='spot', profile_id='sideways', duration_seconds=60), rng=Random(1))
    session.ready(now=1000, anchors=anchors(session))
    saved = session.model_dump(mode='json')
    saved['request'].pop('duration_mode', None)
    saved['catalogue'].pop('allow_until_stopped', None)
    restored = MMSession.model_validate(saved)
    assert restored.request.duration_mode == 'timed'
    assert not restored.catalogue.allow_until_stopped
    assert restored.config_hash == session.config_hash
    assert restored.expires_at == 1060 and restored.permits(session_id=session.id, generation=0, now=1059)
    assert not restored.permits(session_id=session.id, generation=0, now=1060)


@pytest.mark.parametrize('selection', ['fixed', 'random'])
def test_until_stopped_never_removes_or_renews_bootstrap_deadline(selection):
    from test.hummingbot.client.config.test_bootstrap_profiles import catalogue as bootstrap_catalogue
    from types import SimpleNamespace

    from hummingbot.core.listing_bootstrap import ListingBootstrapGuard
    raw = bootstrap_catalogue()
    raw['allow_until_stopped'] = True
    cfg = MMProfilesConfig.model_validate(raw)
    selected = MMSessionRequest(market_mode='spot', selection=selection, duration_mode='until_stopped',
                                **({'profile_id': 'sideways'} if selection == 'fixed' else {'profile_ids': ['sideways']}))
    session = MMSession.create(cfg, selected, rng=Random(1))
    session.ready(now=1000, anchors=anchors(session))
    policies = cfg.bootstrap_policies()
    initial = {'episodes': {name: dict(started_at=None, expires_at=None, market_qualified=False) for name in policies}}
    guard = SimpleNamespace(policies=policies, controllers={'life_spot': SimpleNamespace(processed_data={'quote_guard': {'can_admit': True}})},
                            _validated=lambda state: deepcopy(state.get('listing_bootstrap', initial)))
    recovery = {}
    ListingBootstrapGuard.start_admission(guard, recovery, session, 1000)
    assert recovery['listing_bootstrap']['episodes']['life_spot']['expires_at'] == 1030
    ListingBootstrapGuard.start_admission(guard, recovery, session, 1010)
    assert recovery['listing_bootstrap']['episodes']['life_spot']['expires_at'] == 1030
