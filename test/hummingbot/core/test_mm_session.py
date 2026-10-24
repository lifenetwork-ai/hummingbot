import random
from test.hummingbot.client.config.test_mm_profiles import CATALOGUE

import pytest

from hummingbot.client.config.mm_profiles import MMProfilesConfig
from hummingbot.core.mm_session import MMSession, MMSessionRequest


@pytest.fixture
def rng():
    return random.Random(123)


@pytest.mark.asyncio
async def test_session_clock_starts_at_readiness_and_restart_does_not_extend_it(rng):
    catalogue = await MMProfilesConfig.load(CATALOGUE)
    request = MMSessionRequest(market_mode="both", profile_id="market_follow", duration_seconds=3600)
    session = MMSession.create(catalogue, request, rng=rng, session_id="session-1")
    assert not session.permits(session_id=session.id, generation=0, now=1000)
    anchors = {"life_spot": {"life": "0.1", "sources": {"BTC-USDT": "100", "ETH-USDT": "10"}},
               "life_perpetual": {"life": "0.11", "sources": {"BTC-USDT": "100", "ETH-USDT": "10"}}}
    session.ready(now=1000, anchors=anchors)
    restored = MMSession.model_validate_json(session.model_dump_json())
    restored.ready(now=2000, anchors=anchors)
    assert restored.started_at == 1000
    assert restored.expires_at == 4600
    assert restored.anchors == anchors
    assert restored.permits(session_id="session-1", generation=0, now=4599)
    assert not restored.permits(session_id="session-1", generation=0, now=4600)
    restored.advance(now=4600, rng=rng)
    assert restored.status == "expired"


@pytest.mark.asyncio
async def test_random_restart_preserves_draw_skips_elapsed_windows_and_revokes_old_step(rng):
    catalogue = await MMProfilesConfig.load(CATALOGUE)
    request = MMSessionRequest(market_mode="both", selection="random", duration_seconds=3600,
                               step_duration_seconds=600, profile_ids=["sideways_default"])
    session = MMSession.create(catalogue, request, rng=rng, session_id="random-1")
    anchors = {"life_spot": {"life": "0.1", "sources": {}},
               "life_perpetual": {"life": "0.1", "sources": {}}}
    session.ready(now=1000, anchors=anchors)
    restored = MMSession.model_validate_json(session.model_dump_json())
    same_draw = restored.profile_id
    assert not restored.advance(now=1500, rng=rng)
    assert restored.profile_id == same_draw
    assert restored.advance(now=2900, rng=rng)
    assert restored.step_index == 3
    assert restored.generation != 0
    assert restored.anchors == {}
    assert restored.status == "preparing"
    assert restored.expires_at == 4600
    assert restored.step_expires_at == 3400
    assert not restored.permits(session_id=restored.id, generation=0, now=2900)
    restored.ready(now=3000, anchors=anchors)
    assert restored.expires_at == 4600
    assert restored.permits(session_id=restored.id, generation=restored.generation, now=3000)


@pytest.mark.asyncio
async def test_missing_market_anchor_does_not_admit_both(rng):
    catalogue = await MMProfilesConfig.load(CATALOGUE)
    session = MMSession.create(catalogue, MMSessionRequest(
        market_mode="both", profile_id="sideways_default", duration_seconds=60), rng=rng)
    with pytest.raises(ValueError):
        session.ready(now=1000, anchors={"life_spot": {"life": "0.1", "sources": {}}})
    assert session.started_at is None


@pytest.mark.asyncio
@pytest.mark.parametrize("updates", [
    {"selection": "fixed", "profile_id": None},
    {"selection": "random", "references": [{"pair": "ETH-USDT", "weight": "1"}]},
    {"selection": "random", "profile_ids": []},
    {"profile_id": "unknown"},
    {"duration_seconds": 30},
])
async def test_invalid_session_selection_is_rejected(rng, updates):
    catalogue = await MMProfilesConfig.load(CATALOGUE)
    values = dict(market_mode="spot", profile_id="sideways_default", duration_seconds=3600)
    values.update(updates)
    with pytest.raises(ValueError):
        MMSession.create(catalogue, MMSessionRequest(**values), rng=rng)


@pytest.mark.asyncio
async def test_configuration_hash_covers_effective_api_basket_and_duration(rng):
    catalogue = await MMProfilesConfig.load(CATALOGUE)
    request = MMSessionRequest(market_mode="spot", profile_id="market_follow", duration_seconds=3600,
                               references=[{"pair": "BTC-USDT", "weight": "1"}])
    first = MMSession.create(catalogue, request, rng=rng)
    same = MMSession.create(catalogue, request, rng=rng)
    changed = MMSession.create(catalogue, request.model_copy(update={"duration_seconds": 600}), rng=rng)
    assert first.config_hash == same.config_hash
    assert first.config_hash != changed.config_hash
