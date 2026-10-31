"""Incomplete terminal proofs must not starve later independently provable orders."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from hummingbot.core.order_submission_guard import SubmissionBlocked
from hummingbot.core.spot_outflow_guard import SpotOutflowGuard


def guard_with_pending(count=9):
    guard = SpotOutflowGuard(SimpleNamespace(), dict(trading_pair='LIFE-USDT', window_seconds=60,
                                                     max_net_outflow_quote='100', fee_allowance_rate='.01'))
    for index in range(count):
        identity = f'order-{index}'
        guard.orders[identity] = (None, SimpleNamespace(is_done=True))
        guard.mark_terminal_pending(identity)
    return guard


@pytest.mark.parametrize('failure', [None, ValueError, OSError, asyncio.TimeoutError])
def test_bounded_polls_reach_later_successes_behind_four_incomplete_proofs(failure):
    guard = guard_with_pending()
    blocked = set(list(guard.terminal_pending)[:4])
    ready = set(guard.orders) - blocked
    attempts = []

    async def confirm(identity):
        attempts.append(identity)
        if identity in blocked:
            if failure:
                raise failure('Unconfirmed terminal proof')
            return False
        return True

    guard._confirm = confirm

    async def poll():
        for _ in range(3):
            before = len(attempts)
            await guard.reconcile_terminal()
            assert len(attempts) - before <= 4

    asyncio.run(poll())
    assert ready <= set(attempts)
    assert set(guard.terminal_pending) == set(guard.orders) == blocked


def test_cancellation_retains_pending_order_and_next_poll_starts_elsewhere():
    guard = guard_with_pending(5)
    first = next(iter(guard.terminal_pending))
    guard._confirm = AsyncMock(side_effect=asyncio.CancelledError)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(guard.reconcile_terminal())
    assert set(guard.terminal_pending) == set(guard.orders)
    assert len(guard.terminal_pending) == 5
    attempted = []

    async def confirm(identity):
        attempted.append(identity)
        return False

    guard._confirm = confirm
    asyncio.run(guard.reconcile_terminal())
    assert first not in attempted  # Cancellation did not pin the head indefinitely.


def test_unsent_removal_and_new_arrival_during_proof_do_not_resurrect_queue_entries():
    guard = guard_with_pending(5)
    original = list(guard.terminal_pending)
    removed, first = original[1], original[0]
    attempts = []

    async def confirm(identity):
        attempts.append(identity)
        if identity == first:
            guard.release_unsent(removed)
            guard.orders['new'] = (None, SimpleNamespace(is_done=True))
            guard.mark_terminal_pending('new')
        return False

    guard._confirm = confirm
    asyncio.run(guard.reconcile_terminal())
    assert removed not in guard.orders and removed not in guard.terminal_pending
    assert removed not in attempts
    assert 'new' not in attempts  # This poll's four-candidate snapshot is bounded.
    asyncio.run(guard.reconcile_terminal())
    assert 'new' in attempts
    assert set(guard.terminal_pending) == set(guard.orders)


def test_repeated_terminal_observation_preserves_retry_order_without_duplicates():
    guard = guard_with_pending(6)
    original = list(guard.terminal_pending)
    guard._confirm = AsyncMock(return_value=False)
    asyncio.run(guard.reconcile_terminal())
    assert list(guard.terminal_pending) == original[4:] + original[:4]
    for identity in original:
        guard.mark_terminal_pending(identity)
    assert list(guard.terminal_pending) == original[4:] + original[:4]
    assert len(guard.terminal_pending) == len(guard.orders) == 6


def test_all_orders_checks_entire_batch_but_still_rejects_incomplete_recovery():
    guard = guard_with_pending(9)
    blocked = next(iter(guard.terminal_pending))
    attempted = []

    async def confirm(identity):
        attempted.append(identity)
        return identity != blocked

    guard._confirm = confirm
    with pytest.raises(SubmissionBlocked, match='terminal fill reconciliation is incomplete'):
        asyncio.run(guard.reconcile_terminal(all_orders=True))
    assert len(attempted) == 9 and len(set(attempted)) == 9
    assert set(guard.orders) == set(guard.terminal_pending) == {blocked}
    guard._confirm = AsyncMock(return_value=True)
    asyncio.run(guard.reconcile_terminal(all_orders=True))
    assert not guard.orders and not guard.terminal_pending
