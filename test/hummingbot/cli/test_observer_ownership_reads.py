"""Observer reads may be deduplicated; ownership authority is always fresh."""
from test.hummingbot.cli.test_service_publication import host
from types import MethodType
from unittest.mock import AsyncMock, MagicMock

import pytest

from hummingbot.core.bot_ownership import BotOwnership, OwnershipLost


@pytest.mark.asyncio
async def test_existing_owner_checks_fresh_state_once_on_entry_and_once_before_publication(monkeypatch):
    item = host(monkeypatch)
    item.owner.try_acquire.side_effect = MethodType(BotOwnership.try_acquire, item.owner)
    item.owner.connection = MagicMock(closed=False, invalidated=False)
    item.publish_status = AsyncMock()
    monkeypatch.setattr('hummingbot.core.mm_liquidity.capture_liquidity', lambda value: None)
    await item.observe_once()
    assert item.owner.check_owner.call_count == 2
    item.publish_status.assert_awaited_once_with(0)


@pytest.mark.asyncio
async def test_first_acquisition_still_checks_state_after_generation_is_acquired(monkeypatch):
    item = host(monkeypatch)
    item.owner.connection = None
    item.publish_status = AsyncMock()

    def acquired():
        item.owner.connection = MagicMock(closed=False, invalidated=False)
        return True
    item.owner.try_acquire.side_effect = acquired
    monkeypatch.setattr('hummingbot.core.mm_liquidity.capture_liquidity', lambda value: None)
    await item.observe_once()
    item.owner.try_acquire.assert_called_once()
    assert item.owner.check_owner.call_count == 2
    item.publish_status.assert_awaited_once_with(0)


@pytest.mark.parametrize('state', ['lost', 'closed', 'invalidated'])
@pytest.mark.asyncio
async def test_existing_unusable_owner_cannot_observe_or_publish(monkeypatch, state):
    item = host(monkeypatch)
    item.owner.connection = MagicMock(closed=state == 'closed', invalidated=state == 'invalidated')
    item.owner.lost = state == 'lost'
    item.owner.allow_new_orders = state != 'lost'
    item.owner._read_owner.side_effect = MethodType(BotOwnership._read_owner, item.owner)
    item.owner.check_owner.side_effect = MethodType(BotOwnership.check_owner, item.owner)
    item.owner.try_acquire.side_effect = MethodType(BotOwnership.try_acquire, item.owner)
    item.publish_status = AsyncMock()
    with pytest.raises(OwnershipLost):
        await item.observe_once()
    assert item.owner.lost and not item.owner.allow_new_orders
    item.publish_status.assert_not_awaited()
    item.snapshot.assert_not_called()
