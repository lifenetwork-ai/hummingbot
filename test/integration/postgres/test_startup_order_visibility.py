"""Real recorder/restore lifecycle across a delayed venue pending-order view."""
import asyncio
from copy import deepcopy
from decimal import Decimal
from test.integration.postgres.test_ownership import intent, owned as _owned, reconciliation_core
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select, text

from hummingbot.connector.client_order_tracker import ClientOrderTracker
from hummingbot.core import startup_order_reconciliation as visibility
from hummingbot.core.data_type.in_flight_order import OrderState
from hummingbot.core.event.events import MarketEvent, OrderCancelledEvent
from hummingbot.core.order_submission_guard import SubmissionBlocked
from hummingbot.model.market_state import MarketState
from hummingbot.model.order import Order

owned = _owned


def financial_rows(context):
    with context.manager.get_new_session() as session:
        return {table: session.execute(text(f'SELECT row_to_json(t)::text FROM {table} t ORDER BY row_to_json(t)::text')).scalars().all()
                for table in ('orders', 'trade_fills', 'positions', 'order_statuses', 'market_states')}


def cancelled_restart(context):
    order = intent(context)
    order.update_exchange_order_id('12345')
    order.current_state = OrderState.CANCELED
    context.market.tracking_states = {order.client_order_id: order.to_json()}
    context.recorder._did_cancel_order(
        MarketEvent.OrderCancelled.value, context.market,
        OrderCancelledEvent(timestamp=1700000001, order_id=order.client_order_id, exchange_order_id='12345'))
    with context.manager.get_new_session() as session:
        row = session.get(Order, order.client_order_id)
        assert row.last_status == 'OrderCancelled' and not row.creation_pending
        saved = session.execute(select(MarketState.saved_state).where(MarketState.market == context.market.name)).scalar_one()
        assert saved[order.client_order_id]['last_state'] == str(OrderState.CANCELED.value)
    tracker = ClientOrderTracker(context.market)
    context.market.restore_tracking_states.side_effect = tracker.restore_tracking_states
    context.recorder.restore_market_states('test.yml', context.market)
    context.market.in_flight_orders = tracker.active_orders
    assert not context.market.in_flight_orders  # Native restoration excludes terminal orders.
    symbol = 'BTC-USDT-SWAP' if context.market.name == 'okx_perpetual' else 'BTC-USDT'
    pending = dict(clOrdId=order.client_order_id, ordId='12345', instId=symbol, state='live')
    terminal = dict(clOrdId=order.client_order_id, ordId='12345', instId=symbol, state='canceled',
                    accFillSz='0', sz='0.1', px='100', side='buy', ordType='limit')

    async def api_get(**kwargs):
        return dict(code='0', data=[] if kwargs['path_url'].endswith('/fills') else [terminal])

    context.market._api_get = AsyncMock(side_effect=api_get)
    context.owner._refresh_orders = AsyncMock()
    context.market.cancel_all = AsyncMock()
    return pending, terminal


def test_cancel_ack_then_cold_restore_waits_for_pending_view_to_catch_up(owned, monkeypatch):
    monkeypatch.setattr(visibility, 'CANCEL_VISIBILITY_INTERVAL', 0)
    core = reconciliation_core(owned)
    pending, terminal = cancelled_restart(owned)
    owned.owner._open_orders = AsyncMock(side_effect=[[pending], []])
    before = deepcopy(owned.market.tracking_states)
    persisted = financial_rows(owned)
    owned.loop.run_until_complete(owned.owner.reconcile(core))
    assert owned.owner.allow_new_orders
    assert not owned.store.read()['recovery_state'].get('halt')
    assert owned.owner._open_orders.await_count == 2
    assert owned.market._api_get.await_count == 4
    assert owned.market._api_get.call_args_list[0].kwargs['params'] == {'instId': 'BTC-USDT', 'clOrdId': terminal['clOrdId']}
    owned.market.cancel_all.assert_not_awaited()
    assert owned.market.tracking_states == before
    assert financial_rows(owned) == persisted


@pytest.mark.parametrize('field,value', [
    ('state', 'live'), ('state', 'filled'), ('accFillSz', '0.01'), ('accFillSz', 'NaN'),
    ('ordId', 'different'), ('clOrdId', 'different'), ('instId', 'ETH-USDT'),
    ('sz', '0.2'), ('px', '101'), ('side', 'sell'), ('ordType', 'market'),
])
def test_untracked_terminal_proof_conflict_keeps_halt_and_identifiers(owned, monkeypatch, field, value, caplog):
    monkeypatch.setattr(visibility, 'CANCEL_VISIBILITY_INTERVAL', 0)
    core = reconciliation_core(owned)
    pending, terminal = cancelled_restart(owned)
    terminal[field] = value
    owned.owner._open_orders = AsyncMock(return_value=[pending])
    with pytest.raises(SubmissionBlocked, match='untracked'):
        owned.loop.run_until_complete(owned.owner.reconcile(core))
    state = owned.store.read()['recovery_state']
    assert state['halt'] and not owned.owner.allow_new_orders
    assert state['startup_untracked_orders']['orders'][0]['client_order_id'] == pending['clOrdId']
    assert pending['ordId'] in caplog.text
    owned.market.cancel_all.assert_not_awaited()


@pytest.mark.parametrize('change', ['config', 'exchange_id', 'pending', 'completed', 'side_ambiguous', 'local_fill'])
def test_untracked_local_history_does_not_grant_send_or_cancel_authority(owned, monkeypatch, change):
    monkeypatch.setattr(visibility, 'CANCEL_VISIBILITY_INTERVAL', 0)
    core = reconciliation_core(owned)
    pending, _ = cancelled_restart(owned)
    with owned.manager.get_new_session() as session, session.begin():
        row = session.get(Order, pending['clOrdId'])
        if change == 'config':
            row.config_file_path = 'another.yml'
        elif change == 'exchange_id':
            row.exchange_order_id = 'different'
        elif change == 'pending':
            row.creation_pending = True
        elif change == 'completed':
            row.last_status = 'BuyOrderCompleted'
        elif change == 'side_ambiguous':
            from hummingbot.model.order_status import OrderStatus
            session.add(OrderStatus(order_id=row.id, timestamp=1700000001000, status='PendingSell'))
        else:
            from hummingbot.model.trade_fill import TradeFill
            session.add(TradeFill(config_file_path='test.yml', strategy='test', market='okx', symbol='BTC-USDT',
                                  base_asset='BTC', quote_asset='USDT', timestamp=1700000001000,
                                  order_id=row.id, trade_type='BUY', order_type='LIMIT', price=100, amount='0.01',
                                  trade_fee={}, trade_fee_in_quote=0, exchange_trade_id='trade-1',
                                  leverage=1, position='NIL'))
    owned.owner._open_orders = AsyncMock(return_value=[pending])
    with pytest.raises(SubmissionBlocked, match='untracked'):
        owned.loop.run_until_complete(owned.owner.reconcile(core))
    assert owned.store.read()['recovery_state']['halt']
    owned.market._api_get.assert_not_awaited()
    owned.market.cancel_all.assert_not_awaited()


def test_terminal_order_still_pending_after_bounded_reads_halts(owned, monkeypatch):
    monkeypatch.setattr(visibility, 'CANCEL_VISIBILITY_INTERVAL', 0)
    core = reconciliation_core(owned)
    pending, _ = cancelled_restart(owned)
    owned.owner._open_orders = AsyncMock(return_value=[pending])
    with pytest.raises(SubmissionBlocked, match='untracked'):
        owned.loop.run_until_complete(owned.owner.reconcile(core))
    assert owned.owner._open_orders.await_count == 1 + visibility.CANCEL_VISIBILITY_READS
    assert owned.store.read()['recovery_state']['startup_untracked_orders']['reason_code'] == 'untracked_order_visibility_unconfirmed'
    owned.market.cancel_all.assert_not_awaited()


@pytest.mark.parametrize('response', [dict(code='0', data=[{'tradeId': 'new-fill'}]),
                                      dict(code='50000', data=[], msg='PRIVATE_DO_NOT_LOG'),
                                      dict(code='0', data=None)])
def test_zero_status_but_unproved_fill_view_halts(owned, monkeypatch, response, caplog):
    monkeypatch.setattr(visibility, 'CANCEL_VISIBILITY_INTERVAL', 0)
    core = reconciliation_core(owned)
    pending, terminal = cancelled_restart(owned)
    owned.market._api_get.side_effect = lambda **kwargs: response if kwargs['path_url'].endswith('/fills') else dict(code='0', data=[terminal])
    owned.owner._open_orders = AsyncMock(return_value=[pending])
    with pytest.raises(SubmissionBlocked, match='untracked'):
        owned.loop.run_until_complete(owned.owner.reconcile(core))
    assert owned.store.read()['recovery_state']['halt']
    assert 'PRIVATE_DO_NOT_LOG' not in caplog.text
    owned.market.cancel_all.assert_not_awaited()


@pytest.mark.parametrize('change', ['new_identity', 'database', 'late_fill'])
def test_visibility_proof_cannot_survive_changed_observations(owned, monkeypatch, change):
    monkeypatch.setattr(visibility, 'CANCEL_VISIBILITY_INTERVAL', 0)
    core = reconciliation_core(owned)
    pending, terminal = cancelled_restart(owned)
    reads = 0

    async def pending_view(connector):
        nonlocal reads
        reads += 1
        if reads == 1:
            return [pending]
        if change == 'new_identity':
            return [dict(pending, clOrdId='another-order')]
        if change == 'database':
            with owned.manager.get_new_session() as session, session.begin():
                session.get(Order, pending['clOrdId']).price = 101
        else:
            terminal['accFillSz'] = '0.01'
        return []

    owned.owner._open_orders = AsyncMock(side_effect=pending_view)
    with pytest.raises(SubmissionBlocked, match='untracked'):
        owned.loop.run_until_complete(owned.owner.reconcile(core))
    assert owned.store.read()['recovery_state']['halt']
    owned.market.cancel_all.assert_not_awaited()


def test_visibility_catchup_never_clears_a_prior_halt(owned, monkeypatch):
    monkeypatch.setattr(visibility, 'CANCEL_VISIBILITY_INTERVAL', 0)
    core = reconciliation_core(owned)
    pending, _ = cancelled_restart(owned)
    owned.owner.require_recovery('existing-incident')
    owned.owner._open_orders = AsyncMock(side_effect=[[pending], []])
    with pytest.raises(SubmissionBlocked, match='prior unresolved'):
        owned.loop.run_until_complete(owned.owner.reconcile(core))
    assert owned.store.read()['recovery_state']['halt']
    assert not owned.owner.allow_new_orders


def test_stop_during_read_only_visibility_wait_does_not_invent_halt(owned):
    core = reconciliation_core(owned)
    pending, _ = cancelled_restart(owned)
    owned.owner._open_orders = AsyncMock(return_value=[pending])

    async def run():
        task = asyncio.create_task(owned.owner.reconcile(core))
        while owned.market._api_get.await_count < 2:
            await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    owned.loop.run_until_complete(run())
    assert not owned.store.read()['recovery_state'].get('halt')
    assert not owned.owner.allow_new_orders
    owned.market.cancel_all.assert_not_awaited()


def test_perpetual_canceled_visibility_compares_base_amount_not_contract_count(owned, monkeypatch):
    monkeypatch.setattr(visibility, 'CANCEL_VISIBILITY_INTERVAL', 0)
    owned.market.name = owned.market.display_name = 'okx_perpetual'
    core = reconciliation_core(owned)
    core.connector_manager.connectors = {'okx_perpetual': owned.market}
    owned.owner.connectors = core.connector_manager.connectors
    pending, terminal = cancelled_restart(owned)
    terminal['sz'] = '10'
    owned.market._format_size_to_amount = MagicMock(side_effect=lambda symbol, size: size * Decimal('0.01'))
    owned.owner._open_orders = AsyncMock(side_effect=[[pending], []])
    owned.loop.run_until_complete(owned.owner.reconcile(core))
    assert owned.owner.allow_new_orders
    assert owned.market._api_get.call_args_list[0].kwargs['limit_id'] == 'GET-/api/v5/trade/order'
    assert owned.market._format_size_to_amount.call_count == 2
    owned.market.cancel_all.assert_not_awaited()


def test_too_many_untracked_orders_stay_bounded_and_halted(owned):
    core = reconciliation_core(owned)
    owned.market.cancel_all = AsyncMock()
    owned.owner._open_orders = AsyncMock(return_value=[
        dict(clOrdId=f'foreign-{i}', ordId=str(i), instId='BTC-USDT') for i in range(20)])
    owned.market._api_get = AsyncMock()
    with pytest.raises(SubmissionBlocked, match='untracked'):
        owned.loop.run_until_complete(owned.owner.reconcile(core))
    evidence = owned.store.read()['recovery_state']['startup_untracked_orders']
    assert evidence['untracked_count'] == 20
    assert len(evidence['orders']) == visibility.MAX_CANCEL_VISIBILITY_ORDERS
    owned.market._api_get.assert_not_awaited()
    owned.market.cancel_all.assert_not_awaited()


@pytest.mark.parametrize('failure', ['transport', 'not_found', 'malformed'])
def test_order_status_transport_or_not_found_never_counts_as_cancel_proof(owned, caplog, failure):
    core = reconciliation_core(owned)
    pending, _ = cancelled_restart(owned)
    if failure == 'transport':
        owned.market._api_get.side_effect = IOError('PRIVATE_DO_NOT_LOG')
    else:
        owned.market._api_get.side_effect = None
        owned.market._api_get.return_value = (dict(code='51603', data=[], msg='PRIVATE_DO_NOT_LOG')
                                              if failure == 'not_found' else dict(code='0', data=[]))
    owned.owner._open_orders = AsyncMock(return_value=[pending])
    with pytest.raises(SubmissionBlocked, match='untracked'):
        owned.loop.run_until_complete(owned.owner.reconcile(core))
    evidence = owned.store.read()['recovery_state']['startup_untracked_orders']
    assert evidence['reason_code'] == 'untracked_order_read_unavailable'
    assert 'PRIVATE_DO_NOT_LOG' not in caplog.text
