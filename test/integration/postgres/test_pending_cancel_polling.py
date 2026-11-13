"""A live but incomplete stream uses native REST, committed fills and owner fences."""
import asyncio
from decimal import Decimal
from test.integration.postgres.test_automatic_recovery import managed_native as _managed_native, owned as _owned
from types import SimpleNamespace
from unittest.mock import AsyncMock, PropertyMock, patch

import pytest

from hummingbot.core.data_type.in_flight_order import OrderState
from hummingbot.model.order import Order
from hummingbot.model.trade_fill import TradeFill

managed_native, owned = _managed_native, _owned


@pytest.mark.parametrize('outcome', ['zero_cancel', 'partial_cancel', 'filled', 'missing_fill', 'not_found'])
def test_missing_ws_terminal_is_polled_and_only_complete_receipts_retire_the_order(managed_native, outcome):
    context, exchange, recorder, transport = managed_native
    core = SimpleNamespace(markets_recorder=recorder, trade_fill_db=context.manager)
    status = dict(clOrdId='auto-order', ordId='venue-auto', instId='BTC-USDT', side='buy', ordType='post_only',
                  px='100', sz='.1', accFillSz='0', uTime='1700000001000', state='live')
    fills = []
    identity = context.owner.owner_uuid, context.owner.generation, context.owner.backend_pid
    with patch.object(exchange, '_request_order_update', AsyncMock(side_effect=lambda *a, **k: {'code': '0', 'data': [dict(status)]})), \
            patch.object(exchange, '_request_order_fills', AsyncMock(side_effect=lambda *a, **k: {'code': '0', 'data': list(fills)})):
        context.loop.run_until_complete(context.owner.automatic_recovery.step(core, force=True))
        order = exchange.in_flight_orders['auto-order']
        assert order.current_state == OrderState.OPEN
        with patch.object(exchange, '_place_cancel', AsyncMock(return_value=True)):
            context.loop.run_until_complete(exchange._execute_order_cancel_and_process_update(order))
            context.loop.run_until_complete(asyncio.sleep(0))
        assert order.current_state == OrderState.PENDING_CANCEL and not order.is_done
        with context.manager.get_new_session() as db:
            assert db.query(TradeFill).count() == 0

        quantity = '.04' if outcome == 'partial_cancel' else '.1' if outcome in ('filled', 'missing_fill') else '0'
        status.update(state='filled' if outcome in ('filled', 'missing_fill') else 'canceled', accFillSz=quantity)
        if outcome in ('partial_cancel', 'filled'):
            fills.append(dict(tradeId='native-fill', billId='1', ordId='venue-auto', fillSz=quantity,
                              fillPx='100', fee='-.01', feeCcy='USDT', ts='1700000001000'))
        if outcome == 'not_found':
            exchange._request_order_update.return_value = None
            exchange._request_order_update.side_effect = lambda *a, **k: {'code': '51603', 'data': []}

        async def run():
            # Another topic/heartbeat keeps WS fresh; no order event is delivered.
            exchange._last_timestamp = 121
            exchange._poll_notifier.clear()
            exchange.tick(125)
            assert exchange._poll_notifier.is_set()
            await exchange._update_order_status()
            if outcome in ('missing_fill', 'not_found'):
                for _ in range(100):
                    await asyncio.sleep(.001)
                    if not context.owner.terminal_tasks:
                        break
                assert 'auto-order' in exchange.in_flight_orders and not order.is_done
                assert exchange._get_poll_interval(125) == 5
                if outcome == 'missing_fill':
                    assert order._terminal_proof_pending
            else:
                async def terminal():
                    while 'auto-order' in exchange.in_flight_orders:
                        await asyncio.sleep(.001)
                await asyncio.wait_for(terminal(), 3)
                assert exchange._get_poll_interval(125) == 120

        # Reuse the native tracker rather than replacing it with a test writer.
        with patch.object(type(exchange._user_stream_tracker), 'last_recv_time', new_callable=PropertyMock, return_value=125):
            context.loop.run_until_complete(run())
        context.owner.check_owner()
        assert identity == (context.owner.owner_uuid, context.owner.generation, context.owner.backend_pid)
        assert transport.call.await_count == 1  # Initial ambiguous submission; no replacement POST.
        with context.manager.get_new_session() as db:
            saved = db.get(Order, 'auto-order')
            receipts = db.query(TradeFill).filter_by(order_id='auto-order').all()
            if outcome in ('missing_fill', 'not_found'):
                assert saved.last_status not in ('OrderCancelled', 'BuyOrderCompleted', 'OrderFailure')
                assert not receipts
            else:
                assert saved.last_status == ('BuyOrderCompleted' if outcome == 'filled' else 'OrderCancelled')
                assert len(receipts) == int(Decimal(quantity) > 0)
                if receipts:
                    assert receipts[0].amount == Decimal(quantity)
                    assert receipts[0].trade_fee['flat_fees'] == [{'token': 'USDT', 'amount': '0.01'}]
