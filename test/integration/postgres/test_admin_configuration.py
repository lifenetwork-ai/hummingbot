import asyncio
from test.hummingbot.client.config.test_mm_profiles import CATALOGUE

import pytest

from hummingbot.client.config.mm_profiles import MMProfilesConfig
from hummingbot.core.mm_session import MMSession, MMSessionRequest
from hummingbot.model.admin_store import AdminStore
from hummingbot.model.bot_state_store import BotStateStore, ControlConflict
from hummingbot.model.db_migration.postgres.runner import bootstrap_postgres


def test_catalogue_precondition_and_restart_keep_pinned_configuration_and_retry(pg_engine):
    bootstrap_postgres(pg_engine, auto_migrate=True)
    state = BotStateStore(pg_engine, "hummingbot")
    state.ensure()
    catalogue = asyncio.run(MMProfilesConfig.load(CATALOGUE))
    store = AdminStore(state)
    request = MMSessionRequest(market_mode="spot", profile_id="sideways", duration_seconds=60)
    payload = dict(action="start", expected_version=0, **request.model_dump(exclude_none=True), expected_catalogue_hash="0" * 64)
    with pytest.raises(ControlConflict) as conflict:
        store.command("wrong-catalogue", payload, catalogue=catalogue, request=request)
    assert conflict.value.code == "catalogue_changed"
    assert state.read()["control_version"] == 0
    payload["expected_catalogue_hash"] = catalogue.catalogue_hash()
    receipt = store.command("correct-catalogue", payload, catalogue=catalogue, request=request)
    saved = state.read()["mm_session"]
    revised = catalogue.model_copy(deep=True)
    revised.markets["spot"].total_amount_quote *= 2
    # A new process/revision decodes the saved session, not today's YAML.
    restored = MMSession.model_validate(BotStateStore(pg_engine, "hummingbot").read()["mm_session"])
    assert restored.catalogue.catalogue_hash() == catalogue.catalogue_hash() != revised.catalogue_hash()
    assert restored.id == saved["id"] and restored.config_hash == saved["config_hash"]
    retry = store.command("correct-catalogue", payload, catalogue=revised, request=request)
    assert retry["id"] == receipt["id"]
    config = store.session_configuration(receipt["session_id"])
    assert config["catalogue_hash"] == catalogue.catalogue_hash()
    assert config["source"] == "current_session"
    assert config["legacy_overrides"] == []
    # Switching leaves the prior immutable creation configuration discoverable.
    switched = store.command("new-session", dict(action="switch", expected_version=1, session_id=receipt["session_id"],
                                                 **request.model_dump(exclude_none=True)), catalogue=revised, request=request)
    archived = store.session_configuration(receipt["session_id"])
    assert archived["source"] == "session_created_event"
    assert archived["catalogue_hash"] == catalogue.catalogue_hash()
    assert store.session_configuration(switched["session_id"])["catalogue_hash"] == revised.catalogue_hash()
