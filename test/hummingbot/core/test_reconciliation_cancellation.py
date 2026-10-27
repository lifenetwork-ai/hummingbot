import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from hummingbot.core.bot_ownership import BotOwnership


@pytest.mark.asyncio
@pytest.mark.parametrize('phase,halt', [('readiness', False), ('open_orders_read', False), ('cancel_orders', True)])
async def test_reconciliation_cancel_distinguishes_readonly_startup_from_uncertain_exchange_effects(phase, halt):
    entered = asyncio.Event()

    async def blocked(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()
    order = SimpleNamespace(is_done=False)
    connector = SimpleNamespace(ready=phase != 'readiness', in_flight_orders={'order': order} if halt else {},
                                cancel_all=blocked)
    owner = BotOwnership.__new__(BotOwnership)
    owner.config_file_path = 'test.yml'
    owner.allow_new_orders = True
    owner.reconciling = True
    owner.check_owner = MagicMock(return_value={'recovery_state': {}})
    owner.require_recovery = MagicMock()
    owner._open_orders = blocked if phase == 'open_orders_read' else AsyncMock(return_value=[])
    owner._refresh_orders = AsyncMock()
    core = SimpleNamespace(strategy=SimpleNamespace(config=None),
                           connector_manager=SimpleNamespace(connectors={'okx': connector}))
    task = asyncio.create_task(owner.reconcile(core))
    if phase == 'readiness':
        await asyncio.sleep(0)
        await asyncio.sleep(0)
    else:
        await asyncio.wait_for(entered.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not owner.allow_new_orders and not owner.reconciling
    assert owner.require_recovery.call_count == int(halt)
