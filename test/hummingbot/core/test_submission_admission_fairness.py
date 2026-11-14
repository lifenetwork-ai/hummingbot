"""Queued order admission retains lifecycle authority without starving callbacks."""
import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from hummingbot.core import order_submission_guard as gates
from hummingbot.core.bot_ownership import BotOwnership


@pytest.fixture
def owner(monkeypatch):
    value = BotOwnership(None, 'test')
    monkeypatch.setattr(gates, 'postgres_requested', lambda: True)
    monkeypatch.setattr(gates, '_guard', value)
    return value


@pytest.mark.asyncio
async def test_shared_owner_yields_across_connectors_and_preserves_task_scopes(owner):
    recorded, probe = [], []
    loop = asyncio.get_running_loop()

    def admit(connector, order):
        recorded.append((connector, order, gates.current_session_scope(), gates.current_quote_level()))
        if len(recorded) == 1:
            loop.call_soon(lambda: probe.append(len(recorded)))

    owner.before_order = admit
    tasks = []
    for index in range(8):
        with gates.session_order_scope('session-' + str(index), index, level_id='level-' + str(index)):
            tasks.append(asyncio.create_task(gates.before_order('spot' if index % 2 else 'perpetual', index)))
    await asyncio.gather(*tasks)
    await asyncio.sleep(0)
    assert probe == [1]
    assert [row[1] for row in recorded] == list(range(8))
    assert all(scope == ('session-' + str(index), index) and level == 'level-' + str(index)
               for _, index, scope, level in recorded)
    assert not owner.submission_tasks


@pytest.mark.asyncio
@pytest.mark.parametrize('queued', [False, True])
async def test_cancel_before_admission_never_calls_writer_and_remains_tracked_until_done(owner, queued):
    owner.before_order = Mock()
    if queued:
        await owner.submission_admission.acquire()
    task = asyncio.create_task(gates.before_order(None, None))
    await asyncio.sleep(0)
    assert task in owner.submission_tasks and not task.done()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    if queued:
        owner.submission_admission.release()
    await asyncio.sleep(0)
    owner.before_order.assert_not_called()
    assert task.cancelled() and not owner.submission_tasks
    assert not owner.submission_admission.locked()
    # A canceled waiter must not leave the next admission permanently blocked.
    await gates.before_order(None, None)
    owner.before_order.assert_called_once()


@pytest.mark.asyncio
async def test_guard_replacement_while_waiting_cannot_call_old_owner_writer(owner, monkeypatch):
    owner.before_order = Mock()
    await owner.submission_admission.acquire()
    task = asyncio.create_task(gates.before_order(None, None))
    await asyncio.sleep(0)
    monkeypatch.setattr(gates, '_guard', object())
    owner.submission_admission.release()
    with pytest.raises(gates.SubmissionBlocked, match='owner changed'):
        await task
    await asyncio.sleep(0)
    owner.before_order.assert_not_called()
    assert not owner.submission_tasks and not owner.submission_admission.locked()


@pytest.mark.asyncio
async def test_admission_failure_does_not_poison_siblings_or_detach_tasks(owner):
    calls = []

    def admit(connector, identity):
        calls.append(identity)
        if identity == 0:
            raise OSError('test admission unavailable')

    owner.before_order = admit
    tasks = [asyncio.create_task(gates.before_order(None, index)) for index in range(3)]
    result = await asyncio.gather(*tasks, return_exceptions=True)
    await asyncio.sleep(0)
    assert isinstance(result[0], OSError) and result[1:] == [None, None]
    assert calls == [0, 1, 2] and not owner.submission_tasks


@pytest.mark.asyncio
async def test_waiting_for_transport_does_not_hold_the_shared_admission_slot(owner):
    owner.before_order = Mock()
    release, posted = asyncio.Event(), []

    async def submit(index):
        await gates.before_order(None, index)
        posted.append(index)
        await release.wait()

    tasks = [asyncio.create_task(submit(index)) for index in range(8)]
    try:
        async with asyncio.timeout(1):
            while len(posted) != 8:
                await asyncio.sleep(0)
        assert len(owner.submission_tasks) == 8 and not owner.submission_admission.locked()
    finally:
        release.set()
        await asyncio.gather(*tasks)
    await asyncio.sleep(0)
    assert not owner.submission_tasks


@pytest.mark.asyncio
async def test_no_postgres_guard_preserves_immediate_legacy_path(monkeypatch):
    monkeypatch.setattr(gates, 'active_guard', lambda: None)
    marker = SimpleNamespace(called=False)
    asyncio.get_running_loop().call_soon(lambda: setattr(marker, 'called', True))
    await gates.before_order(None, None)
    assert not marker.called
