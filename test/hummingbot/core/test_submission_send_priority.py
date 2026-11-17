"""Bounded send priority must improve acceptance, not just a nested call timer."""
import asyncio
from types import SimpleNamespace

import pytest

from hummingbot.core import order_submission_guard as gates
from hummingbot.core.bot_ownership import BotOwnership


class FIFOAdmission(asyncio.Lock):
    def final_send(self):
        return self


@pytest.mark.asyncio
async def test_twelve_order_pipeline_improves_enqueue_to_acceptance_with_equal_work(monkeypatch):
    async def run(fifo):
        owner = BotOwnership(None, 'test')
        monkeypatch.setattr(gates, 'active_guard', lambda: owner)
        if fifo:
            owner.submission_admission = FIFOAdmission()
        clock, accepted, events, health = [0], [], [], []

        def before(connector, order):
            clock[0] += 1
            events.append(('admit', order))
            asyncio.get_running_loop().call_soon(lambda: health.append(len(events)))

        async def authorize(request):
            clock[0] += 1
            events.append(('send', request.identity))

        owner.before_order, owner._authorize_rest = before, authorize

        async def order(identity):
            await owner.admit_order(None, identity)
            await owner.authorize_rest(SimpleNamespace(
                url='https://offline.invalid/api/v5/trade/order', identity=identity))
            accepted.append((identity, clock[0]))

        await asyncio.gather(*(order(i) for i in range(12)))
        await asyncio.sleep(0)
        assert not owner.submission_tasks and not owner.submission_admission.locked()
        assert [i for kind, i in events if kind == 'admit'] == list(range(12))
        assert [i for i, _ in accepted] == list(range(12))
        assert health[0] == 1
        return [cost for _, cost in accepted], events

    before, fifo_events = await run(True)
    after, priority_events = await run(False)
    assert len(fifo_events) == len(priority_events) == 24
    assert min(after) < min(before)
    assert sum(after) <= sum(before) * .8
    assert max(after) <= max(before)


@pytest.mark.asyncio
async def test_two_sends_then_waiting_admission_preserves_fifo_in_each_queue():
    gate = BotOwnership(None, 'test').submission_admission
    await gate.acquire()
    granted = []

    async def take(label, final):
        async with gate.final_send() if final else gate:
            granted.append(label)
            await asyncio.sleep(0)

    tasks = [asyncio.create_task(take('admit-' + str(i), False)) for i in range(2)]
    tasks += [asyncio.create_task(take('send-' + str(i), True)) for i in range(6)]
    await asyncio.sleep(0)
    gate.release()
    await asyncio.gather(*tasks)
    assert granted == ['send-0', 'send-1', 'admit-0', 'send-2', 'send-3', 'admit-1', 'send-4', 'send-5']
    assert not gate.locked()


@pytest.mark.asyncio
@pytest.mark.parametrize('final', [False, True])
@pytest.mark.parametrize('granted_before_cancel', [False, True])
async def test_cancel_waiting_or_granted_task_cannot_leak_the_shared_slot(final, granted_before_cancel):
    gate = BotOwnership(None, 'test').submission_admission
    await gate.acquire()
    calls = []

    async def take(label):
        async with gate.final_send() if final else gate:
            calls.append(label)

    canceled = asyncio.create_task(take('canceled'))
    following = asyncio.create_task(take('following'))
    await asyncio.sleep(0)
    if granted_before_cancel:
        gate.release()
    canceled.cancel()
    if not granted_before_cancel:
        await asyncio.gather(canceled, return_exceptions=True)
        gate.release()
    await asyncio.gather(canceled, following, return_exceptions=True)
    assert canceled.cancelled() and calls == ['following']
    assert not gate.locked()
    async with gate:
        assert gate.locked()


@pytest.mark.asyncio
async def test_failed_final_holder_releases_slot_for_both_queues():
    gate = BotOwnership(None, 'test').submission_admission
    with pytest.raises(OSError, match='test gate failure'):
        async with gate.final_send():
            raise OSError('test gate failure')
    async with gate:
        assert gate.locked()
    assert not gate.locked()
    with pytest.raises(RuntimeError):
        gate.release()


@pytest.mark.asyncio
async def test_cancel_all_queued_classes_before_handoff_leaves_no_stale_grant():
    gate = BotOwnership(None, 'test').submission_admission
    await gate.acquire()
    entered = []

    async def take(identity, final):
        async with gate.final_send() if final else gate:
            entered.append(identity)
            await asyncio.sleep(0)

    tasks = [asyncio.create_task(take(i, i < 2)) for i in range(3)]
    await asyncio.sleep(0)
    for task in tasks:
        task.cancel()
    # Release before the canceled tasks resume and unlink their waiters.
    gate.release()
    fresh = asyncio.create_task(take('fresh', False))
    await asyncio.gather(*tasks, fresh, return_exceptions=True)
    assert all(task.cancelled() for task in tasks)
    assert entered == ['fresh'] and not gate.locked()


@pytest.mark.asyncio
async def test_final_context_exit_never_yields_after_fresh_send_permission(monkeypatch):
    owner = BotOwnership(None, 'test')
    monkeypatch.setattr(gates, 'active_guard', lambda: owner)
    callbacks = []

    async def fresh_permission(request):
        asyncio.get_running_loop().call_soon(callbacks.append, 'control callback')
        return 'fresh send permission'

    owner._authorize_rest = fresh_permission
    request = SimpleNamespace(url='https://offline.invalid/api/v5/trade/order')
    assert await owner.authorize_rest(request) == 'fresh send permission'
    assert not callbacks
    assert not owner.submission_admission.locked()
    await asyncio.sleep(0)
    assert callbacks == ['control callback']
