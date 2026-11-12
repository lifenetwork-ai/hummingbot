"""An unrelated hedge-leg WS update must not discard fresh absence proof."""
import asyncio
import time
from decimal import Decimal
from test.hummingbot.strategy_v2.controllers.test_reduction_controller import maker
from unittest.mock import AsyncMock

import pytest
from bidict import bidict

from hummingbot.connector.derivative.okx_perpetual.okx_perpetual_derivative import OkxPerpetualDerivative
from hummingbot.core.data_type.common import PositionMode, PositionSide


def configured():
    controller, _, _ = maker('1', '0')
    connector = OkxPerpetualDerivative(trading_pairs=['BTC-USDT'], trading_required=False)
    connector._set_trading_pair_symbol_map(bidict({'BTC-USDT-SWAP': 'BTC-USDT'}))
    connector._contract_sizes['BTC-USDT'] = Decimal('.1')
    connector._perpetual_trading.set_position_mode(PositionMode.HEDGE)
    connector.position_snapshot_times['BTC-USDT'] = time.time() - 20
    controller.market_data_provider.time.side_effect = time.time
    controller.market_data_provider.get_connector_with_fallback.return_value = connector
    return controller, connector


def position(side='long', amount='2'):
    return dict(instId='BTC-USDT-SWAP', posSide=side, pos=amount, avgPx='100',
                lever='1', upl='0', mgnMode='cross')


@pytest.mark.asyncio
@pytest.mark.parametrize('side', ['long', 'short'])
async def test_ws_racing_rest_preserves_changed_leg_and_proves_only_its_absent_peer(side):
    controller, connector = configured()
    entered, release = asyncio.Event(), asyncio.Event()
    old_snapshot = connector.position_snapshot_times['BTC-USDT']

    async def response(**kwargs):
        entered.set()
        await release.wait()
        return {'code': '0', 'data': [position(side, amount='1')]}

    connector._api_get = AsyncMock(side_effect=response)
    task = asyncio.create_task(connector._update_positions())
    try:
        await asyncio.wait_for(entered.wait(), 1)
        await connector._process_account_position_event(position(side, amount='2'))
        release.set()
        await asyncio.wait_for(task, 1)
        assert connector.position_snapshot_times['BTC-USDT'] == old_snapshot
        assert abs(next(iter(connector.account_positions.values())).amount) == Decimal('.2')
        expected = (Decimal('.2'), Decimal('0')) if side == 'long' else (Decimal('0'), Decimal('.2'))
        assert controller._reduction_venue() == expected
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_native_position_reads_are_serialized_across_callers():
    _, connector = configured()
    entered, release = asyncio.Event(), asyncio.Event()
    active = peak = 0

    async def response(**kwargs):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        entered.set()
        try:
            await release.wait()
            return {'code': '0', 'data': []}
        finally:
            active -= 1

    connector._api_get = AsyncMock(side_effect=response)
    tasks = [asyncio.create_task(connector._update_positions())]
    try:
        await asyncio.wait_for(entered.wait(), 1)
        tasks.append(asyncio.create_task(connector._update_positions()))
        for _ in range(10):
            await asyncio.sleep(0)
        assert active == peak == 1
        release.set()
        await asyncio.wait_for(asyncio.gather(*tasks), 1)
        assert active == 0 and connector._api_get.await_count == 2
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize('amount', ['0', '2'])
async def test_ws_for_the_absent_leg_invalidates_old_and_inflight_absence_proof(amount):
    controller, connector = configured()
    await connector._process_account_position_event(position())
    key = ('BTC-USDT', PositionSide.SHORT)
    connector.position_absence_times[key] = time.time()
    entered, release = asyncio.Event(), asyncio.Event()

    async def response(**kwargs):
        entered.set()
        await release.wait()
        return {'code': '0', 'data': [position()]}

    connector._api_get = AsyncMock(side_effect=response)
    task = asyncio.create_task(connector._update_positions())
    try:
        await asyncio.wait_for(entered.wait(), 1)
        await connector._process_account_position_event(position('short', amount))
        assert key not in connector.position_absence_times
        release.set()
        await asyncio.wait_for(task, 1)
        assert key not in connector.position_absence_times
        if amount == '0':
            with pytest.raises(ValueError, match='reduction_position_unavailable'):
                controller._reduction_venue()
        else:
            assert controller._reduction_venue() == (Decimal('.2'), Decimal('.2'))
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_a_nonzero_rest_leg_replaces_its_prior_absence_proof():
    controller, connector = configured()
    key = ('BTC-USDT', PositionSide.SHORT)
    connector.position_absence_times[key] = time.time()
    connector._api_get = AsyncMock(return_value={'code': '0', 'data': [position('short')]})
    await connector._update_positions()
    assert key not in connector.position_absence_times
    assert controller._reduction_venue() == (Decimal('0'), Decimal('.2'))


@pytest.mark.asyncio
@pytest.mark.parametrize('response', [
    {'code': '1', 'data': []}, {'data': []}, {'code': '0', 'data': {}},
    {'code': '0', 'data': [position(), position()]},
    {'code': '0', 'data': [dict(position(), instId='ETH-USDT-SWAP')]},
    {'code': '0', 'data': [dict(position(), posSide='net')]},
    {'code': '0', 'data': [dict(position(), mgnMode='isolated')]},
    {'code': '0', 'data': [dict(position(), pos='NaN')]},
    OSError('positions unavailable'),
])
async def test_failed_or_incomplete_responses_cannot_prove_an_absent_leg(response):
    controller, connector = configured()
    connector._api_get = AsyncMock(side_effect=response) if isinstance(response, Exception) else AsyncMock(return_value=response)
    try:
        await connector._update_positions()
    except (KeyError, ValueError, TypeError, ArithmeticError):
        pass  # Preserve the native parser's rejection of malformed responses.
    assert not connector.position_absence_times
    with pytest.raises(ValueError, match='reduction_position_unavailable'):
        controller._reduction_venue()


@pytest.mark.asyncio
@pytest.mark.parametrize('offset', [-11, 1])
async def test_fresh_pair_timestamp_cannot_hide_stale_or_future_absence_evidence(offset):
    controller, connector = configured()
    await connector._process_account_position_event(position())
    connector.position_snapshot_times['BTC-USDT'] = time.time()
    connector.position_absence_times['BTC-USDT', PositionSide.SHORT] = time.time() + offset
    with pytest.raises(ValueError, match='reduction_position_unavailable'):
        controller._reduction_venue()
    assert controller.request_reduction_position_refresh()


@pytest.mark.asyncio
async def test_net_position_event_invalidates_both_hedge_absence_proofs():
    controller, connector = configured()
    connector._api_get = AsyncMock(return_value={'code': '0', 'data': []})
    await connector._update_positions()
    assert len(connector.position_absence_times) == 2
    await connector._process_account_position_event(dict(position(), posSide='net'))
    assert not connector.position_absence_times
    with pytest.raises(ValueError, match='reduction_position_unavailable'):
        controller._reduction_venue()
    assert controller.request_reduction_position_refresh()


@pytest.mark.asyncio
async def test_cancelled_rest_read_releases_serialization_without_publishing_absence():
    controller, connector = configured()
    entered, canceled = asyncio.Event(), asyncio.Event()

    async def response(**kwargs):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            canceled.set()

    connector._api_get = AsyncMock(side_effect=response)
    task = asyncio.create_task(connector._update_positions())
    await asyncio.wait_for(entered.wait(), 1)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert canceled.is_set() and not connector.position_absence_times
    connector._api_get = AsyncMock(return_value={'code': '0', 'data': []})
    await asyncio.wait_for(connector._update_positions(), 1)
    assert controller._reduction_venue() == (Decimal('0'), Decimal('0'))


@pytest.mark.asyncio
@pytest.mark.parametrize('malformed', [
    dict(position(), pos='NaN'),
    {key: value for key, value in position().items() if key != 'pos'},
])
async def test_failed_ws_application_cannot_reuse_a_fresh_old_position_observation(malformed):
    controller, connector = configured()
    await connector._process_account_position_event(position())
    connector._api_get = AsyncMock(return_value={'code': '0', 'data': [position()]})
    await connector._update_positions()
    entered, release = asyncio.Event(), asyncio.Event()

    async def response(**kwargs):
        entered.set()
        await release.wait()
        return {'code': '0', 'data': [position()]}

    connector._api_get = AsyncMock(side_effect=response)
    task = asyncio.create_task(connector._update_positions())
    try:
        await asyncio.wait_for(entered.wait(), 1)
        with pytest.raises((ValueError, KeyError)):
            await connector._process_account_position_event(malformed)
        release.set()
        await asyncio.wait_for(task, 1)
        assert next(iter(connector.account_positions.values())).amount == Decimal('.2')
        assert ('BTC-USDT', PositionSide.SHORT) in connector.position_absence_times
        with pytest.raises(ValueError, match='reduction_position_unavailable'):
            controller._reduction_venue()
        assert controller.request_reduction_position_refresh()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
