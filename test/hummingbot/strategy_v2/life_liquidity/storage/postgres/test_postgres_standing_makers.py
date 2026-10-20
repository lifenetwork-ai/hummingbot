"""Standing maker scope remains a complete native/runner/SDK proof."""
from copy import deepcopy
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_service_trading import configured

import pytest

from hummingbot.core.data_type.common import TradeType
from hummingbot.core.data_type.in_flight_order import OrderState
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['canceling', 'quantity', 'unbooked_fill', 'foreign_sdk'])
async def test_mismatched_standing_order_cannot_finance_or_send_a_sibling(database, monkeypatch, fault):
    t, service = await configured(database, maker_cycle=True)
    order = foreign = None
    saved = None
    primary = None
    try:
        action = await service.quote('original-standing', TradeType.BUY, 0)
        wire = service.quote_source.wire_for_action(action)
        order = t.connector._order_tracker.active_orders[wire]
        saved = order.current_state, order.amount, order.executed_amount_base
        before = await database[0].fetchrow('''SELECT version,
            (SELECT jsonb_agg(to_jsonb(b) ORDER BY asset) FROM life_financial.balances b WHERE b.account_id=a.account_id) balances
            FROM life_financial.accounts a WHERE account_id=$1''', t.cfg.account_id)
        propose = service.producer.propose

        async def changed(*args):
            nonlocal foreign
            intent = await propose(*args)
            if fault == 'canceling':
                order.current_state = OrderState.PENDING_CANCEL
            elif fault == 'quantity':
                order.amount += 1
            elif fault == 'unbooked_fill':
                order.executed_amount_base = D('.1')
            else:
                foreign = deepcopy(order)
                foreign.client_order_id = 'unknown-standing-wire'
                t.connector._order_tracker.start_tracking_order(foreign)
            return intent
        monkeypatch.setattr(service.producer, 'propose', changed)
        with pytest.raises(StorageError, match='STANDING_SCOPE'):
            await service.quote('refused-sibling', TradeType.SELL, 0)
        after = await database[0].fetchrow('''SELECT version,
            (SELECT jsonb_agg(to_jsonb(b) ORDER BY asset) FROM life_financial.balances b WHERE b.account_id=a.account_id) balances
            FROM life_financial.accounts a WHERE account_id=$1''', t.cfg.account_id)
        assert after == before
        assert [c.intent_id for c in await service.source.capital.claims(t.cfg.binding)] == ['original-standing']
        assert await service.financial.attempt('refused-sibling', t.cfg.binding) is None
        assert len([r for r in t.requests if r['method'] == 'POST' and r['url'].endswith('/order')]) == 1
        assert service._revoked
    except BaseException as error:
        primary = error
        raise
    finally:
        if order is not None:
            order.current_state, order.amount, order.executed_amount_base = saved
        if foreign is not None:
            t.connector._order_tracker.stop_tracking_order(foreign.client_order_id)
        try:
            await service.close()
        except BaseException as error:
            if primary is None:
                raise
            primary.add_note('cleanup_failure=' + type(error).__name__ + ':' + str(error))


@pytest.mark.asyncio
async def test_allocation_pause_callback_cannot_be_replaced_after_assembly(database):
    t, service = await configured(database)
    original = service.hedge._pause_allocation
    try:
        before = len(t.requests)
        service.hedge._pause_allocation = lambda: True
        with pytest.raises(StorageError, match='CONTEXT_CHANGED'):
            service.hedge._original()
        assert len(t.requests) == before and not service._owned_executors
    finally:
        service.hedge._pause_allocation = original
        await service.close()
