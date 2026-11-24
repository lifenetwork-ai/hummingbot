"""Clock catch-up must preserve exact risk while yielding between bounded batches."""
import asyncio
import copy
from test.hummingbot.core.test_spot_outflow import fill, order, policy
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from hummingbot.cli.service import ServiceHost
from hummingbot.client.config.runtime_config import RuntimeSettings
from hummingbot.core.data_type.common import OrderType, TradeType
from hummingbot.core.order_submission_guard import SubmissionBlocked
from hummingbot.core.spot_outflow import SpotOutflowProjection
from hummingbot.core.spot_outflow_guard import SpotOutflowGuard


def populated(count=4096):
    projection = SpotOutflowProjection(policy(), 'LIFE-USDT', coverage_start_ms=0)
    projection.advance(20000)
    for index in range(count):
        projection.add_fill(fill(f'order-{index}:fill', 15000 + index % 1000,
                                 'BUY' if index % 2 else 'SELL',
                                 amount='.1234567890123456789012345678',
                                 fees=[('USDT', '.00000000001'), ('LIFE', '-.00001')]))
    return projection


def guarded(projection):
    row = dict(control_version=1, recovery_state={}, config_type='mm-session', mm_session={'probe': True},
               database_now=30)
    owner = SimpleNamespace(version=1, connection=object(), check_owner=MagicMock(side_effect=lambda: row),
                            require_recovery=MagicMock())
    guard = SpotOutflowGuard(owner, dict(trading_pair='LIFE-USDT', **policy().model_dump()))
    guard.identity = lambda: {'account_id': 'same'}
    guard.binding, guard.reason_code, guard.projection = guard.identity(), None, projection
    owner.spot_outflow = guard
    return guard, row


@pytest.mark.asyncio
@pytest.mark.parametrize('count', [4096, 100000])
async def test_service_bulk_expiry_yields_before_finishing_and_never_authorizes_partial_risk(monkeypatch, count):
    from hummingbot.core.mm_session import MMSession

    projection = populated(count)
    reference = copy.deepcopy(projection)
    reference.advance(30000)
    guard, row = guarded(projection)
    guard.owner.observe_state = AsyncMock(side_effect=lambda: dict(row))
    host = ServiceHost(RuntimeSettings.from_environment({'DATABASE_URL': 'postgresql://unused/test'}),
                       asyncio.Event(), MagicMock())
    host.owner = guard.owner
    host.hb = SimpleNamespace(trading_core=SimpleNamespace(strategy=object()))
    host.observe_recovery_operation = AsyncMock(return_value=False)
    host.observe_credential_operation = AsyncMock(return_value=False)
    monkeypatch.setattr('hummingbot.core.order_submission_guard.install_guard', lambda owner: None)

    class ObservationBoundary(Exception):
        pass

    def after_expiry(*args):
        raise ObservationBoundary

    monkeypatch.setattr(MMSession, 'model_validate', after_expiry)
    applied, observations = [], []
    original = projection._apply

    def callback():
        observations.append(dict(applied=len(applied), status=guard.status()))
        candidate = order('new-buy', amount='1')
        candidate.trading_pair, candidate.order_type, candidate.trade_type = 'LIFE-USDT', OrderType.LIMIT_MAKER, TradeType.BUY
        connector = SimpleNamespace(name='okx')
        with pytest.raises(SubmissionBlocked, match='spot_outflow_projection_catching_up'):
            guard.before_order(connector, candidate, {'database_now': 30})
        candidate.trade_type = TradeType.SELL
        guard.before_order(connector, candidate, {'database_now': 30})
        assert not guard.orders

    def apply(value, direction):
        original(value, direction)
        applied.append(direction)
        if len(applied) == 64:
            asyncio.get_running_loop().call_soon(callback)

    monkeypatch.setattr(projection, '_apply', apply)
    with pytest.raises(ObservationBoundary):
        await host.observe_once()
    await asyncio.sleep(0)
    assert observations and 64 <= observations[0]['applied'] <= 512
    assert observations[0]['status']['ready'] is False
    assert observations[0]['status']['recorded_net_outflow_quote'] is None
    assert observations[0]['status']['remaining_quote'] is None
    assert len(applied) == count and projection.status() == reference.status()
    assert projection.entries == reference.entries and projection.future == reference.future
    guard.owner.require_recovery.assert_not_called()


@pytest.mark.parametrize('size', [1, 7, 512])
def test_budgeted_future_expiry_and_late_arrivals_match_unlimited_reference(monkeypatch, size):
    from hummingbot.core import spot_outflow

    projection = populated(1024)
    # Keep both future cash and a terminal reservation through clock catch-up.
    assert projection.reserve(order('future', amount='.1'))
    projection.add_fill(fill('future:1', 20000, amount='.1', fees=[('ETH', '-.00001')]))
    projection.confirm_terminal('future', order('future', amount='.1').amount)
    reference = copy.deepcopy(projection)
    reference.advance(30000)
    original_pop = spot_outflow.heapq.heappop
    popped = []

    def pop(heap):
        popped.append(1)
        return original_pop(heap)

    monkeypatch.setattr(spot_outflow.heapq, 'heappop', pop)
    projection.advance(30000, max_work=size)
    assert len(popped) <= size and projection.catching_up()
    assert not projection.reserve(order('blocked'))
    assert projection.status()['reserved_quote'] is None
    arrived = fill('arriving:1', 29999, 'SELL', amount='.00000001234567890123456789',
                   fees=[('USDT', '-.000000001'), ('ETH', '.00000001')])
    reference.add_fill(copy.deepcopy(arrived))
    projection.add_fill(arrived)
    arrived['trade_fee']['flat_fees'][0]['amount'] = 'private-later-mutation'
    # Replay a same-identity equivalent fill after the snapshot.
    projection.add_fill(fill('arriving:1', 29999, 'SELL', amount='.00000001234567890123456789',
                             fees=[('USDT', '-.000000001'), ('ETH', '.00000001')]))
    for _ in range(2000):
        before = len(popped)
        projection.advance(30000, max_work=size)
        assert len(popped) - before <= size
        if not projection.catching_up():
            break
    assert not projection.catching_up()
    assert projection.status() == reference.status()
    assert projection.entries == reference.entries
    assert projection.reservations == reference.reservations
    assert projection.future_costs == reference.future_costs
    for now in (30001, 40000, 40001):
        projection.advance(now)
        reference.advance(now)
        assert projection.status() == reference.status()


@pytest.mark.parametrize('budget', [0, -1, True, 1.5])
def test_invalid_budget_cannot_move_the_clock_or_mutate_risk(budget):
    projection = populated(4)
    before = copy.deepcopy(projection)
    with pytest.raises(ValueError, match='work budget'):
        projection.advance(30000, max_work=budget)
    assert projection.__dict__ == before.__dict__


@pytest.mark.asyncio
@pytest.mark.parametrize('interruption', ['cancel', 'control', 'ownership', 'halt'])
async def test_interrupted_catchup_remains_blocked_and_resumes_exactly(interruption):
    projection = populated()
    reference = copy.deepcopy(projection)
    reference.advance(30000)
    guard, row = guarded(projection)
    task = asyncio.create_task(guard.advance(30000))
    await asyncio.sleep(0)
    assert projection.catching_up() and not guard.status()['buy_allowed']
    if interruption == 'cancel':
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    elif interruption == 'ownership':
        guard.owner.check_owner.side_effect = OSError('owner connection lost')
        with pytest.raises(OSError):
            await task
        guard.owner.check_owner.side_effect = lambda: row
    else:
        if interruption == 'control':
            row['control_version'] = 2
        else:
            row['recovery_state'] = {'halt': True}
        assert await task is True
        assert len(projection.entries) == len(reference.entries) + 4096 - 512
        row['recovery_state'], row['control_version'] = {}, 1
    assert not guard.status()['ready'] and guard.status()['stale']
    await guard.advance(30000)
    assert projection.status() == reference.status()
    guard.owner.require_recovery.assert_not_called()


def test_pending_arrival_overflow_is_bounded_and_never_grants_capacity(monkeypatch):
    from hummingbot.core import spot_outflow

    projection = populated(8)
    projection.advance(30000, max_work=1)
    monkeypatch.setattr(spot_outflow, 'MAX_FILLS', 9)
    for index in range(10):
        projection.add_fill(fill(f'pending-{index}:fill', 29999))
    assert len(projection.pending_fills) == 2
    assert projection.reason() == 'spot_outflow_projection_limit'
    projection.advance(30000)
    assert not projection.pending_fills
    assert not projection.reserve(order('blocked'))
    assert projection.status()['recorded_net_outflow_quote'] is None


@pytest.mark.asyncio
async def test_queued_maker_buy_retires_without_exchange_retry_during_clock_catchup(monkeypatch):
    from test.hummingbot.strategy_v2.controllers.test_inventory_cap_lifecycle import controller_actions, setup_lifecycle

    from hummingbot.core.data_type.trade_fee import AddedToCostTradeFee
    from hummingbot.core.event.events import MarketOrderFailureEvent
    from hummingbot.strategy_v2.models.base import RunnableStatus
    from hummingbot.strategy_v2.models.executor_actions import CreateExecutorAction

    state = await setup_lifecycle(monkeypatch, 'okx', TradeType.BUY,
                                  mm_session_id='session', mm_generation=1)
    state.controller.config = state.controller.config.model_copy(update={'runtime_config_managed': True})
    guard, _ = guarded(populated())
    fee = AddedToCostTradeFee(percent=policy().fee_allowance_rate)
    state.connector.get_fee.return_value = fee
    candidate = state.order
    with pytest.raises(SubmissionBlocked, match='spot_outflow_projection_catching_up') as denied:
        guard.before_order(SimpleNamespace(name='okx', get_fee=lambda *args, **kwargs: fee), candidate, {'database_now': 30})
    assert type(denied.value).__name__ == 'SpotOutflowPending'
    monkeypatch.setattr('hummingbot.core.order_submission_guard.spot_buy_preview', guard.preview)
    state.executor.process_order_failed_event(None, state.connector, MarketOrderFailureEvent(
        30, candidate.client_order_id, OrderType.LIMIT_MAKER, str(denied.value), type(denied.value).__name__))
    assert state.executor._current_retries == 0
    assert state.executor.status == RunnableStatus.SHUTTING_DOWN
    await state.executor.control_task()
    assert state.executor.status == RunnableStatus.TERMINATED
    actions = await controller_actions(state.controller, state.executor)
    assert not any(isinstance(a, CreateExecutorAction) and a.executor_config.side == TradeType.BUY for a in actions)
    assert any(isinstance(a, CreateExecutorAction) and a.executor_config.side == TradeType.SELL for a in actions)
    await guard.advance(30000)
    actions = await controller_actions(state.controller, state.executor)
    assert any(isinstance(a, CreateExecutorAction) and a.executor_config.side == TradeType.BUY for a in actions)
    guard.owner.require_recovery.assert_not_called()


@pytest.mark.asyncio
async def test_malformed_deferred_fill_remains_a_buy_only_failure_not_a_service_exception():
    projection = populated()
    projection.advance(30000, max_work=1)
    bad = fill('bad:fill', 29999)
    bad['timestamp'] = 'invalid-committed-row'
    projection.add_fill(bad)
    guard, _ = guarded(projection)
    await guard.advance(30000)
    assert guard.status()['reason_code'] == 'spot_outflow_fill_invalid'
    assert not guard.status()['buy_allowed'] and not projection.pending_fills
    guard.owner.require_recovery.assert_not_called()


@pytest.mark.asyncio
async def test_resume_finishes_prior_clock_and_arrivals_before_a_later_target():
    projection = SpotOutflowProjection(policy(), 'LIFE-USDT', coverage_start_ms=0)
    projection.advance(20000)
    for index in range(1024):
        projection.add_fill(fill(f'history-{index}:fill', 15000 if index < 512 else 20000,
                                 amount='.1234567890123456789012345678'))
    reference = copy.deepcopy(projection)
    reference.advance(25001)
    projection.advance(25001, max_work=1)
    arrival = fill('arrival:1', 25000, price='10000000000000000000000000')
    reference.add_fill(arrival)
    projection.add_fill(arrival)
    reference.advance(31000)
    guard, _ = guarded(projection)
    await guard.advance(31000)
    assert projection.status() == reference.status()
    assert projection.entries == reference.entries


@pytest.mark.asyncio
@pytest.mark.parametrize('pending', [False, True])
async def test_bad_clock_is_not_hidden_by_resuming_a_pending_window(pending):
    projection = populated()
    if pending:
        projection.advance(30000, max_work=1)
    guard, _ = guarded(projection)
    before = projection.now_ms
    assert await guard.advance(before - 1) is False
    assert projection.now_ms == before
    assert guard.status()['reason_code'] == 'spot_outflow_clock_unavailable'
    assert not guard.status()['buy_allowed']
    await guard.advance(before)
    assert guard.status()['ready']


@pytest.mark.parametrize('problem', ['failure', 'clock'])
def test_catchup_does_not_reclassify_genuine_projection_failure_as_normal_wait(problem):
    projection = populated()
    projection.advance(30000, max_work=1)
    if problem == 'failure':
        projection.failure = 'spot_outflow_fill_conflict'
    else:
        projection.clock_failure = True
    guard, _ = guarded(projection)
    candidate = order('bad-buy')
    candidate.trading_pair, candidate.order_type, candidate.trade_type = 'LIFE-USDT', OrderType.LIMIT_MAKER, TradeType.BUY
    with pytest.raises(SubmissionBlocked) as denied:
        guard.before_order(SimpleNamespace(name='okx'), candidate, {'database_now': 30})
    assert type(denied.value) is SubmissionBlocked
    assert str(denied.value) == projection.reason()
