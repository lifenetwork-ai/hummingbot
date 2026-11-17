"""A partial local risk projection cannot become durable final-send permission."""
import asyncio
import copy
import json
import time
from decimal import Decimal
from test.hummingbot.core.test_spot_outflow import fill
from test.integration.postgres.test_spot_outflow import buy, owned as _owned, setup

import pytest
from sqlalchemy import func, select

from hummingbot.core.data_type.common import TradeType
from hummingbot.core.order_submission_guard import SubmissionBlocked
from hummingbot.core.web_assistant.connections.data_types import RESTMethod, RESTRequest
from hummingbot.model.trade_fill import TradeFill

owned = _owned


def expiring(context, count=4096):
    projection = context.owner.spot_outflow.projection
    stamp = projection.now_ms - 1
    for index in range(count):
        projection.add_fill(fill(f'history-{index}:fill', stamp,
                                 'BUY' if index % 2 else 'SELL', amount='.0000001'))
    return stamp


def test_partial_expiry_rejects_final_send_without_halt_and_resumes_after_proof(owned):
    session, _ = setup(owned, window_seconds=1)
    queued = buy(owned, session)
    stamp = expiring(owned)
    guard = owned.owner.spot_outflow
    request = RESTRequest(method=RESTMethod.POST, url='https://www.okx.com/api/v5/trade/order',
                          is_auth_required=True, data=json.dumps(dict(
                              clOrdId='a', instId='LIFE-USDT', side='buy', sz='5', px='10', tdMode='cash')))

    async def scenario():
        await asyncio.sleep(max(0, (stamp + 1001) / 1000 - time.time()))
        with pytest.raises(SubmissionBlocked, match='spot_outflow_projection_catching_up') as denied:
            await owned.owner.authorize_rest(request)
        assert type(denied.value).__name__ == 'SpotOutflowPending'
        assert guard.projection.catching_up() and guard.status()['stale']
        assert 'a' not in owned.owner.sent_intents
        owned.owner.order_failed(owned.market, queued)
        buy(owned, session, 'sell', TradeType.SELL)
        await guard.advance(int(owned.owner.check_owner()['database_now'] * 1000))
        assert not guard.projection.catching_up()
        assert guard.status()['recorded_net_outflow_quote'] == Decimal('0')
        assert buy(owned, session, 'new-buy').client_order_id == 'new-buy'

    asyncio.run(scenario())
    assert not owned.store.read()['recovery_state'].get('halt')
    assert owned.manager.persistence_healthy
    with owned.engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(TradeFill)) == 0


def test_database_stop_interrupts_bulk_expiry_without_clearing_guard_or_checkpoint(owned):
    setup(owned, window_seconds=1)
    stamp = expiring(owned)
    guard = owned.owner.spot_outflow
    before = owned.store.read()['recovery_state']

    async def scenario():
        await asyncio.sleep(max(0, (stamp + 1001) / 1000 - time.time()))
        target = int(owned.owner.check_owner()['database_now'] * 1000)
        reference = copy.deepcopy(guard.projection)
        reference.advance(target)
        asyncio.get_running_loop().call_soon(lambda: owned.store.control(
            expected_version=owned.owner.version, desired_state='stopped'))
        assert await guard.advance(target) is True
        assert len(guard.projection.entries) == 4096 - 512
        row = owned.store.read()
        assert row['desired_state'] == 'stopped' and not row['recovery_state'].get('halt')
        assert guard.status()['reason_code'] == 'spot_outflow_projection_catching_up'
        owned.owner.version = row['control_version']
        await guard.advance(target)
        assert guard.projection.status() == reference.status()
        assert owned.store.read()['desired_state'] == 'stopped'

    asyncio.run(scenario())
    assert owned.store.read()['recovery_state'] == before
