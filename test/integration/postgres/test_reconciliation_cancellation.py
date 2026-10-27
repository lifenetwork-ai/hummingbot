import asyncio
from test.integration.postgres.test_ownership import owned as _owned, reconciliation_core
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

owned = _owned


@pytest.mark.parametrize('phase,prior_halt,expected_halt', [('readiness', False, False),
                                                            ('open_orders_read', False, False),
                                                            ('cancel_orders', False, True),
                                                            ('readiness', True, True)])
def test_durable_recovery_is_only_added_for_interrupted_exchange_effects(owned, phase, prior_halt, expected_halt):
    core = reconciliation_core(owned)
    if prior_halt:
        owned.owner.require_recovery('existing review required')

    async def exercise():
        entered = asyncio.Event()

        async def blocked(*args, **kwargs):
            entered.set()
            await asyncio.Event().wait()
        if phase == 'readiness':
            owned.market.ready = False
        elif phase == 'open_orders_read':
            owned.owner._open_orders = blocked
        else:
            owned.market.in_flight_orders = {'order-1': SimpleNamespace(is_done=False)}
            owned.owner._open_orders = AsyncMock(return_value=[{'clOrdId': 'order-1'}])
            owned.owner._refresh_orders = AsyncMock()
            owned.market.cancel_all = blocked
        task = asyncio.create_task(owned.owner.reconcile(core))
        if phase == 'readiness':
            await asyncio.sleep(0)
            await asyncio.sleep(0)
        else:
            await asyncio.wait_for(entered.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    owned.loop.run_until_complete(exercise())
    state = owned.owner.check_owner()['recovery_state']
    assert bool(state.get('halt')) is expected_halt
    assert not owned.owner.allow_new_orders
    if prior_halt:
        assert state['reason'] == 'existing review required'
