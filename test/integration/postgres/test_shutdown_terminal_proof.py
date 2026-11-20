"""Shutdown reuses one strong proof per phase, never across the checkpoint."""
import asyncio
import copy
from decimal import Decimal
from test.integration.postgres.test_ownership import owned as _owned, reconciliation_core
from test.integration.postgres.test_spot_outflow import buy, setup
from types import MethodType
from unittest.mock import AsyncMock

import pytest

from hummingbot.connector.exchange.okx.okx_exchange import OkxExchange
from hummingbot.core.data_type.in_flight_order import OrderState
from hummingbot.core.data_type.trade_fee import TradeFeeSchema
from hummingbot.core.order_submission_guard import SubmissionBlocked
from hummingbot.core.rate_oracle.rate_oracle import RateOracle
from hummingbot.model.trade_fill import TradeFill

owned = _owned


def canceled_buy(owned, monkeypatch, *, delayed=False):
    session, _ = setup(owned, window_seconds=3600)
    order = buy(owned, session)
    order.update_exchange_order_id('42')
    order.current_state = OrderState.CANCELED
    # Use the projection's database clock so setup latency cannot move the
    # simulated fill ahead of the end of its observed cashflow window.
    stamp = str(owned.owner.spot_outflow.projection.now_ms - 100)
    state = dict(ordId='42', clOrdId='a', instId='LIFE-USDT', side='buy', sz='5', px='10',
                 state='canceled', accFillSz='2', uTime=stamp)
    fills = [dict(tradeId='1', billId='1', ordId='42', fillSz='2', fillPx='10', fee='-.2', feeCcy='USDT', ts=stamp)]
    requests = []
    elapsed = 0
    clock_origin = owned.loop.time()

    async def read(kind, data):
        nonlocal elapsed
        requests.append(kind)
        if delayed:
            # Deterministic venue latency on the native 8s deadline; no wall
            # sleeps and no change to the financial/database monotonic clocks.
            elapsed += 1.25
        await asyncio.sleep(0)
        return dict(code='0', data=copy.deepcopy(data))

    if delayed:
        monkeypatch.setattr(owned.loop, 'time', lambda: clock_origin + elapsed)

    async def status(order):
        return await read('status', [state])

    async def trades(*args, **kwargs):
        return await read('fills', fills)
    owned.market._request_order_update = AsyncMock(side_effect=status)
    owned.market._request_order_fills = AsyncMock(side_effect=trades)
    for name in ('_request_order_status', '_order_update_from_data', '_all_trade_updates_for_order', '_trade_update_from_fill'):
        setattr(owned.market, name, MethodType(getattr(OkxExchange, name), owned.market))
    owned.market.trade_fee_schema.return_value = TradeFeeSchema()
    monkeypatch.setattr(RateOracle.get_instance(), '_prices', {'LIFE-USDT': Decimal('10')})
    owned.market._order_tracker.start_tracking_order(order)

    async def terminal(update):
        order.update_with_order_update(update)
    owned.market._order_tracker.process_order_update = AsyncMock(side_effect=terminal)
    core = reconciliation_core(owned)
    core.strategy.executor_orchestrator = object()
    core.cancel_outstanding_orders = AsyncMock(return_value=True)
    return core, order, state, fills, requests


@pytest.mark.parametrize('native_state', [OrderState.CANCELED, OrderState.PENDING_CANCEL, OrderState.OPEN, OrderState.PARTIALLY_FILLED])
def test_one_strong_canceled_buy_proof_per_phase_fits_native_shutdown_deadline(owned, monkeypatch, native_state):
    core, order, _, _, requests = canceled_buy(owned, monkeypatch, delayed=True)
    order.current_state = native_state
    snapshots = []

    async def shutdown(**kwargs):
        proof = await core.before_position_checkpoint({'a': ('okx', order)})
        snapshots.append(proof['a'])
        await core.after_order_cancellation()
        return True

    core.shutdown = shutdown
    assert owned.loop.run_until_complete(owned.owner.shutdown_core(core, timeout=8))
    # Strong status/fills/status proof before the snapshot; fresh status/fills
    # again afterward. The old status/fills + status/fills/status duplicates
    # cost 8.75s before checkpoint validation has completed.
    assert requests == ['status', 'fills', 'status', 'status', 'fills']
    assert Decimal(snapshots[0]['executed_amount_base']) == 2
    assert order.executed_amount_quote == 20
    assert owned.owner.spot_outflow.status()['recorded_net_outflow_quote'] == Decimal('20.2')
    assert owned.owner.spot_outflow.status()['reserved_quote'] == 0
    assert not owned.store.read()['recovery_state'].get('halt')
    with owned.manager.get_new_session() as db:
        assert db.query(TradeFill).count() == 1


def test_fresh_post_checkpoint_proof_still_detects_a_late_fill(owned, monkeypatch):
    core, order, state, fills, requests = canceled_buy(owned, monkeypatch)

    async def shutdown(**kwargs):
        proof = await core.before_position_checkpoint({'a': ('okx', order)})
        assert Decimal(proof['a']['executed_amount_base']) == 2
        state['accFillSz'] = '3'
        state['uTime'] = str(int(state['uTime']) + 1)
        fills.append(dict(tradeId='2', billId='2', ordId='42', fillSz='1', fillPx='10', fee='-.1',
                          feeCcy='USDT', ts=state['uTime']))
        await core.after_order_cancellation()
        return True

    core.shutdown = shutdown
    with pytest.raises(SubmissionBlocked, match='Position checkpoint changed'):
        owned.loop.run_until_complete(owned.owner.shutdown_core(core, timeout=8))
    assert requests == ['status', 'fills', 'status', 'status', 'fills']
    assert order.executed_amount_base == 3
    assert not owned.owner.allow_new_orders
    assert owned.store.read()['recovery_state']['halt']


def test_changed_terminal_terms_keep_reservation_and_checkpoint_fence(owned, monkeypatch):
    core, order, state, _, requests = canceled_buy(owned, monkeypatch)
    state['px'] = '11'

    async def shutdown(**kwargs):
        await core.before_position_checkpoint({'a': ('okx', order)})
        return True

    core.shutdown = shutdown
    with pytest.raises((ValueError, SubmissionBlocked)):
        owned.loop.run_until_complete(owned.owner.shutdown_core(core, timeout=8))
    assert owned.owner.spot_outflow.projection.reservations['a'].committed == 0
    assert not owned.owner.allow_new_orders
    assert owned.store.read()['recovery_state']['halt']


@pytest.mark.parametrize('fault', ['changed_final', 'incomplete', 'duplicate', 'binding', 'replacement', 'owner'])
def test_shared_proof_cannot_accept_partial_or_foreign_evidence(owned, monkeypatch, fault):
    from hummingbot.core.bot_ownership import OwnershipLost
    core, order, state, fills, requests = canceled_buy(owned, monkeypatch)
    raw_read = owned.market._request_order_update.side_effect
    raw_fills = owned.market._request_order_fills.side_effect

    async def status(order):
        if fault == 'changed_final' and requests.count('status'):
            state['uTime'] = str(int(state['uTime']) + 1)
        return await raw_read(order)

    async def trades(*args, **kwargs):
        result = await raw_fills(*args, **kwargs)
        if fault == 'binding':
            owned.market.account_uid = 'different-account'
        elif fault == 'replacement':
            owned.owner.spot_outflow.orders['a'] = (owned.market, object())
        elif fault == 'owner':
            owned.owner.release()
        return result

    if fault == 'incomplete':
        state['accFillSz'] = '3'
    if fault == 'duplicate':
        fills.append(copy.deepcopy(fills[0]))
    owned.market._request_order_update.side_effect = status
    owned.market._request_order_fills.side_effect = trades

    async def shutdown(**kwargs):
        await core.before_position_checkpoint({'a': ('okx', order)})
        return True
    core.shutdown = shutdown
    with pytest.raises((ValueError, SubmissionBlocked, OwnershipLost)):
        owned.loop.run_until_complete(owned.owner.shutdown_core(core, timeout=8))
    assert 'a' in owned.owner.spot_outflow.orders
    assert owned.owner.spot_outflow.projection.reservations['a'].committed == 0
    assert not owned.owner.allow_new_orders
    assert owned.store.read()['recovery_state']['halt']
    with owned.manager.get_new_session() as db:
        assert db.query(TradeFill).count() == 0


def test_shared_terminal_proof_retains_future_committed_fill_reservations(owned, monkeypatch):
    _, order, _, fills, _ = canceled_buy(owned, monkeypatch)
    guard = owned.owner.spot_outflow
    now = guard.projection.now_ms
    fills[0]['ts'] = str(now + 2000)
    assert owned.loop.run_until_complete(guard.refresh_canceled_order(owned.market, order))
    assert not guard.orders
    assert guard.projection.reservations['a'].terminal_amount == 2
    assert guard.projection.reservations['a'].committed == 0
    assert guard.projection.reserved() > 20  # No premature removal of future cash risk.
    guard.projection.advance(now + 2001)
    assert not guard.projection.reservations and guard.projection.net == Decimal('20.2')


def test_cancelled_shared_proof_drains_its_request_without_releasing_reservation(owned, monkeypatch):
    from hummingbot.core.bot_ownership import BotOwnership
    _, order, _, _, _ = canceled_buy(owned, monkeypatch)
    entered = asyncio.Event()
    active = 0

    async def unavailable(order):
        nonlocal active
        active += 1
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            active -= 1
    owned.market._request_order_update.side_effect = unavailable

    async def run():
        task = asyncio.create_task(BotOwnership._refresh_orders(
            owned.market, [order], terminal_guard=owned.owner.spot_outflow))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    owned.loop.run_until_complete(run())
    assert active == 0
    assert 'a' in owned.owner.spot_outflow.orders and 'a' in owned.owner.spot_outflow.terminal_pending
    assert owned.owner.spot_outflow.projection.reservations['a'].committed == 0


@pytest.mark.parametrize('fault', ['binding', 'owner', 'native_quantity'])
def test_callback_yield_cannot_release_a_shared_proof_after_authority_changes(owned, monkeypatch, fault):
    from hummingbot.core.bot_ownership import OwnershipLost
    _, order, _, _, _ = canceled_buy(owned, monkeypatch)
    native_update = owned.market._order_tracker.process_order_update.side_effect

    async def update(status):
        await native_update(status)
        if fault == 'binding':
            owned.market.account_uid = 'different-account'
        elif fault == 'owner':
            owned.owner.release()
        else:
            order.executed_amount_base = Decimal('1')
        await asyncio.sleep(0)
    owned.market._order_tracker.process_order_update.side_effect = update
    with pytest.raises((ValueError, SubmissionBlocked, OwnershipLost)):
        owned.loop.run_until_complete(owned.owner.spot_outflow.refresh_canceled_order(owned.market, order))
    assert 'a' in owned.owner.spot_outflow.orders
    assert owned.owner.spot_outflow.projection.reservations['a'].terminal_amount is None
    # The actual fill was committed before the callback; its original fee is
    # retained even though authority to release the remaining cash was lost.
    with owned.manager.get_new_session() as db:
        assert db.query(TradeFill).count() == 1
