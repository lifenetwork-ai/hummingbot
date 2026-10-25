import asyncio
import time
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from multidict import MultiDict

from hummingbot.cli.admin_api import AdminAPI
from hummingbot.client.config.admin_config import AdminSettings


@pytest.mark.asyncio
async def test_daily_polling_coalesces_misses_preserves_observation_time_and_expires():
    storage = MagicMock()
    observation = datetime.now(timezone.utc)

    def aggregate(**filters):
        time.sleep(.02)
        return dict(start_day=date(2026, 1, 1), end_day=date(2026, 1, 2), reporting_timezone="UTC", server_time=observation,
                    coverage="observed_recorder_events", balance_scope="account_last_observed", balances=[], markets=[])
    storage.daily.side_effect = aggregate
    settings = AdminSettings(reporting_timezone="UTC", statistics_cache_ttl_seconds=.1)
    api = AdminAPI(None, settings, store=storage)
    request = SimpleNamespace(query=MultiDict())
    responses = await asyncio.gather(*(api.daily(request) for _ in range(10)))
    assert storage.daily.call_count == 1
    assert len({response.text for response in responses}) == 1
    assert observation.isoformat().replace("+00:00", "Z") in responses[0].text
    await api.daily(SimpleNamespace(query=MultiDict(market="spot")))
    assert storage.daily.call_count == 2
    await asyncio.sleep(.12)
    await api.daily(request)
    assert storage.daily.call_count == 3


@pytest.mark.asyncio
async def test_failed_aggregate_is_not_cached():
    storage = MagicMock()
    storage.daily.side_effect = ValueError("failure")
    api = AdminAPI(None, AdminSettings(), store=storage)
    for _ in range(2):
        with pytest.raises(ValueError):
            await api.daily(SimpleNamespace(query={}))
    assert storage.daily.call_count == 2


@pytest.mark.asyncio
async def test_zero_ttl_disables_daily_cache():
    storage = MagicMock()
    storage.daily.return_value = dict(start_day=date(2026, 1, 1), end_day=date(2026, 1, 2), reporting_timezone="UTC",
                                      server_time=datetime.now(timezone.utc), coverage="observed_recorder_events",
                                      balance_scope="account_last_observed", balances=[], markets=[])
    api = AdminAPI(None, AdminSettings(statistics_cache_ttl_seconds=0), store=storage)
    for _ in range(2):
        await api.daily(SimpleNamespace(query=MultiDict()))
    assert storage.daily.call_count == 2
