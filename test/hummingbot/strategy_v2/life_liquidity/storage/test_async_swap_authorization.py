"""Awaited authority at the real SWAP post-throttler REST boundary."""
import asyncio
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.test_protected_swap_send import connector_with_transport
from unittest.mock import AsyncMock

import pytest

from hummingbot.core.data_type.common import OrderType, PositionAction, TradeType


@pytest.mark.asyncio
@pytest.mark.parametrize('outcome', ['allow', 'deny', 'failure', 'cancel', 'stop'])
async def test_swap_waits_for_authority_then_runs_local_fence_with_no_late_send(outcome):
    connector, throttle, sent = connector_with_transport()
    entered, release = asyncio.Event(), asyncio.Event()
    local = {'allowed': True}
    checks, acks = [], []

    async def authority(wire):
        assert wire['instId'] == 'LIFE-USDT-SWAP'
        entered.set()
        await release.wait()
        if outcome in ('deny', 'failure'):
            raise PermissionError('SYNTHETIC_AUTHORITY_DENIED')

    def fence(wire):
        if not local['allowed']:
            raise PermissionError('SYNTHETIC_STOPPED')
        checks.append(wire)

    pending = asyncio.create_task(connector._place_order(
        'wire1', 'LIFE-USDT', D('.5'), TradeType.SELL, OrderType.LIMIT_MAKER, D('1'), PositionAction.CLOSE,
        pre_send_check=fence, on_ack=acks.append, async_pre_send_check=authority))
    await asyncio.wait_for(throttle.entered.wait(), 2)
    assert not entered.is_set()
    throttle.release.set()
    await asyncio.wait_for(entered.wait(), 2)
    assert sent == checks == acks == []
    if outcome == 'cancel':
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        release.set()
        await asyncio.sleep(0)
        assert sent == checks == acks == []
    else:
        if outcome == 'stop':
            local['allowed'] = False
        release.set()
        if outcome == 'allow':
            assert (await pending)[0] == 'swap-42'
            assert len(sent) == len(checks) == len(acks) == 1
        else:
            with pytest.raises(PermissionError):
                await pending
            assert sent == checks == acks == []


@pytest.mark.asyncio
@pytest.mark.parametrize('uid', ['12345', None, 12345, 'foreign', '１２３'])
async def test_swap_uid_is_collected_from_authenticated_account_config_with_strict_encoding(uid):
    connector, _, _ = connector_with_transport()
    connector._api_get = AsyncMock(return_value={'code': '0', 'data': [{'uid': uid}]})
    if uid == '12345':
        assert await connector.get_account_uid() == '12345'
    else:
        with pytest.raises(ValueError, match='ACCOUNT_UID_UNAVAILABLE'):
            await connector.get_account_uid()
    connector._api_get.assert_awaited_once_with(path_url='/api/v5/account/config', is_auth_required=True)
