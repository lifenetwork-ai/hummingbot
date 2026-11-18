"""Queued order admission retains lifecycle authority without starving callbacks."""
import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from hummingbot.core import order_submission_guard as gates
from hummingbot.core.bot_ownership import BotOwnership
from hummingbot.core.web_assistant.connections.data_types import RESTMethod


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


@pytest.mark.asyncio
async def test_final_gate_waits_for_its_turn_before_rechecking_guard_identity(owner, monkeypatch):
    owner._authorize_rest = AsyncMock()
    await owner.submission_admission.acquire()
    task = asyncio.create_task(owner.authorize_rest(SimpleNamespace(url='https://offline.invalid/api/v5/trade/order')))
    await asyncio.sleep(0)
    assert task in owner.submission_tasks
    monkeypatch.setattr(gates, '_guard', object())
    owner.submission_admission.release()
    with pytest.raises(gates.SubmissionBlocked, match='owner changed'):
        await task
    owner._authorize_rest.assert_not_awaited()
    await asyncio.sleep(0)
    assert not owner.submission_tasks and not owner.submission_admission.locked()


@pytest.mark.asyncio
async def test_cancel_and_account_paths_do_not_queue_behind_order_send_admission(owner):
    owner._authorize_rest = AsyncMock(return_value='original-authority-result')
    await owner.submission_admission.acquire()
    try:
        for path in ('/api/v5/trade/cancel-order', '/api/v5/trade/cancel-batch-orders', '/api/v5/account/set-leverage'):
            request = SimpleNamespace(url='https://offline.invalid' + path)
            assert await owner.authorize_rest(request) == 'original-authority-result'
        assert owner._authorize_rest.await_count == 3 and not owner.submission_tasks
    finally:
        owner.submission_admission.release()


@pytest.mark.asyncio
async def test_final_gate_releases_admission_before_network_wait_and_retains_scopes(owner):
    release, calls, authorized = asyncio.Event(), [], []

    async def authorize(request):
        calls.append((request.index, gates.current_session_scope()))

    owner._authorize_rest = authorize

    async def send(index):
        with gates.session_order_scope('session', index):
            context = await gates.before_rest(SimpleNamespace(
                url='https://offline.invalid/api/v5/trade/order', index=index, is_auth_required=True, method=RESTMethod.POST))
            try:
                authorized.append(index)
                await release.wait()
            finally:
                gates.completed_rest(context)

    tasks = [asyncio.create_task(send(i)) for i in range(8)]
    try:
        async with asyncio.timeout(1):
            while len(authorized) < 8:
                await asyncio.sleep(0)
        assert calls == [(i, ('session', i)) for i in range(8)]
        assert not owner.submission_admission.locked() and len(owner.transport_tasks) == 8
    finally:
        release.set()
        await asyncio.gather(*tasks)
    await asyncio.sleep(0)
    assert not owner.submission_tasks and not owner.transport_tasks


@pytest.mark.asyncio
async def test_direct_final_gate_does_not_retain_its_long_lived_caller_after_authorization(owner):
    owner._authorize_rest = AsyncMock()
    await owner.authorize_rest(SimpleNamespace(url='https://offline.invalid/api/v5/trade/order'))
    assert asyncio.current_task() not in owner.submission_tasks
    assert not owner.submission_admission.locked()


@pytest.mark.asyncio
async def test_repeated_proven_unsent_contention_waits_without_failure_or_holding_send_slot(owner, caplog):
    calls, posted, probes = [], [], []
    busy = asyncio.Event()

    def admit(connector, identity):
        calls.append(identity)
        if len(calls) <= 3:
            owner._intent_admission_retry_at = time.monotonic() + .1
            busy.set()
            raise gates.IntentAdmissionBusy('proven unsent rollback')

    owner.before_order = admit
    owner._authorize_rest = AsyncMock(return_value=None)

    async def submit():
        await gates.before_order(None, 'same-native-identity')
        posted.append('same-native-identity')

    task = asyncio.create_task(submit())
    try:
        await asyncio.wait_for(busy.wait(), 1)
        assert not task.done(), 'Contention must wait, not become an executor failure/retry'
        assert task in owner.submission_tasks and not owner.submission_admission.locked()
        # Already prepared final sends and health callbacks must not wait for
        # the new-intent cooldown. This gate still rechecks its own authority.
        await asyncio.wait_for(owner.authorize_rest(SimpleNamespace(url='https://offline.invalid/api/v5/trade/order')), 1)
        asyncio.get_running_loop().call_soon(lambda: probes.append(True))
        await asyncio.sleep(.01)
        assert probes and calls == ['same-native-identity'] and not posted
        await asyncio.wait_for(task, 1)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert calls == ['same-native-identity'] * 4 and posted == ['same-native-identity']
    assert not owner.submission_tasks and not owner.submission_admission.locked()
    warnings = [r for r in caplog.records if getattr(r, 'reason_code', None) == 'intent_admission_busy']
    assert len(warnings) == 1, 'Repeated contention must retain a bounded diagnostic'


@pytest.mark.asyncio
@pytest.mark.parametrize('interrupt', ['cancel', 'replace', 'fatal'])
async def test_cooldown_cancellation_and_authority_changes_never_replay_an_admitted_send(owner, monkeypatch, interrupt):
    owner._intent_admission_retry_at = time.monotonic() + .03
    owner.before_order = Mock(side_effect=OSError('unknown write outcome') if interrupt == 'fatal' else None)
    task = asyncio.create_task(gates.before_order(None, 'unsent'))
    await asyncio.sleep(.005)
    assert task in owner.submission_tasks and not task.done()
    owner.before_order.assert_not_called()
    assert not owner.submission_admission.locked()
    if interrupt == 'cancel':
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        owner.before_order.assert_not_called()
    elif interrupt == 'replace':
        monkeypatch.setattr(gates, '_guard', object())
        with pytest.raises(gates.SubmissionBlocked, match='owner changed'):
            await task
        owner.before_order.assert_not_called()
    else:
        with pytest.raises(OSError, match='unknown write outcome'):
            await task
        owner.before_order.assert_called_once()
    await asyncio.sleep(0)
    assert not owner.submission_tasks and not owner.submission_admission.locked()
