import asyncio
from test.integration.postgres.test_ownership import owned as _owned, reconciliation_core
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

owned = _owned


@pytest.mark.parametrize('outcome', ['recover', 'stop', 'prior_halt'])
def test_readiness_outage_does_not_persist_new_halt_and_preserves_prior_halt(owned, monkeypatch, outcome):
    from hummingbot.core.order_submission_guard import SubmissionBlocked

    core = reconciliation_core(owned)
    owned.market.ready = False
    owned.owner._open_orders = AsyncMock(return_value=[])
    owned.owner._refresh_orders = AsyncMock()
    if outcome == 'prior_halt':
        owned.owner.require_recovery('existing unresolved send')
    before = owned.owner.check_owner()['recovery_state']
    owned.owner.reconciling = True
    wait_for = asyncio.wait_for

    async def short_deadline(awaitable, timeout):
        return await wait_for(awaitable, .25 if timeout == 30 else timeout)

    monkeypatch.setattr('hummingbot.core.bot_ownership.asyncio.wait_for', short_deadline)

    async def exercise():
        task = asyncio.create_task(owned.owner.reconcile(core))
        try:
            await asyncio.sleep(.8)
            assert not task.done()
            assert not owned.owner.allow_new_orders
            assert owned.owner.check_owner()['recovery_state'] == before
            owned.owner._open_orders.assert_not_awaited()
            if outcome == 'stop':
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                owned.market.ready = True
                if outcome == 'prior_halt':
                    with pytest.raises(SubmissionBlocked, match='prior unresolved'):
                        await wait_for(task, 2)
                else:
                    await wait_for(task, 2)
            assert owned.owner.check_owner()['recovery_state'] == before
            assert owned.owner.allow_new_orders is (outcome == 'recover')
            assert not owned.owner.reconciling
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    owned.loop.run_until_complete(exercise())


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
