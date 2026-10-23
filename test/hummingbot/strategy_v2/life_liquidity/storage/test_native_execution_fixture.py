"""Execution setup must return the last observed risk generation, not its predecessor."""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_mode_setup_does_not_reuse_a_risk_receipt_from_before_signed_mode_selection(monkeypatch):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres import test_postgres_inventory_exit as fixtures
    repo, config, owner, risk, keys, body = (object() for _ in range(6))
    old = SimpleNamespace(risk_epoch=2)
    current = SimpleNamespace(risk_epoch=3)
    monkeypatch.setattr(fixtures, 'execution_store', AsyncMock(return_value=(repo, config, owner, risk, old, keys, body)))
    monkeypatch.setattr(fixtures, 'choose_mode', AsyncMock())
    refresh = AsyncMock(return_value=current)
    monkeypatch.setattr(fixtures, 'refresh', refresh)
    database = (SimpleNamespace(fetchval=AsyncMock(return_value=datetime.now(timezone.utc))),)
    values = await fixtures.mode_store(database)
    assert values[:4] == (repo, config, owner, risk) and values[5:] == (keys, body)
    assert values[4] is current and values[4].risk_epoch != old.risk_epoch
    refresh.assert_awaited_once_with(database, repo, config, owner, risk, 'mode-nav')
