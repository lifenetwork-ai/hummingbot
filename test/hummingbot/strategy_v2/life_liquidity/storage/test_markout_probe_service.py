"""Repository markout bootstrap uses real fills and never waives horizon evidence."""
from datetime import timedelta
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.test_quote_service import quote_service
from test.hummingbot.strategy_v2.life_liquidity.storage.test_risk_service import policy, sources

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.service_config import MarkoutServiceConfig


def markout_policy(**changes):
    return MarkoutServiceConfig(**(dict(horizons_ms=(100,), min_samples=1, size_cutoff_base=D('2'),
                                        cohort_window_ms=1000, max_horizon_lag_ms=50,
                                        min_mean_markout_quote=D('0'), monitored_cohorts=(('BUY', 'small', 100),),
                                        probe_size_base=D('.1'), probe_campaign_base=D('.2')) | changes))


async def markout_service(tmp_path, *, objective='profit_mm', **changes):
    state, callbacks = sources()
    callbacks['horizon_observation'] = lambda *_: state.get('horizon')
    service, market = await quote_service(tmp_path, objective=objective, risk_config=policy(markout=markout_policy(**changes)), risk_sources=callbacks)
    return service, state, market


@pytest.mark.asyncio
async def test_configured_markout_bootstrap_is_small_sequential_and_not_general_normal_permission(tmp_path):
    service, _, _ = await markout_service(tmp_path)
    try:
        assert not await service.store._call(service.risk.ready)  # Unconfigured recovery probe stays disabled.
        assert await service.store._call(service.risk.ready)
        assert service.risk.gate.state == 'NORMAL'
        action = (await service.plan())[0]
        assert action.executor_config.amount == D('.1')
        assert action.executor_config.side.name == 'BUY'
        assert service.runtime.preflight(action)
        assert not service.runtime.preflight(action.model_copy(update={
            'executor_config': action.executor_config.model_copy(update={'amount': D('1')})}))
        assert not await service.store._call(lambda: service.risk.ready(normal_only=True))
        assert not service.risk.local_ready(normal_only=True)
        assert await service.plan() == []
        assert service.controller._fill_attributor is None
    finally:
        await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('horizon', ['pending', 'missing', 'adverse', 'good'])
async def test_actual_accounted_fill_requires_complete_nonadverse_horizon_before_more_bootstrap(tmp_path, horizon):
    from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_fill_contracts import command, fill, prepare_arm

    from hummingbot.strategy_v2.life_liquidity.markout_risk import IndependentHorizonObservation
    service, state, _ = await markout_service(tmp_path)
    try:
        await prepare_arm(service.store, service.store.binding, service.spot.owner)
        event = fill(signed_fee=D('0'), independent_price_usdt=D('1'))
        await service.store.apply_fills(command(service.spot.owner, fills=(event,)),
                                        cut=(await service.store.snapshot(service.store.binding)).cut, owner=service.spot.owner)
        if horizon != 'pending':
            state['now'] += timedelta(milliseconds=100)
            at = int(state['now'].timestamp() * 1000)
            state['horizon'] = (None if horizon == 'missing' else IndependentHorizonObservation(
                D('.5') if horizon == 'adverse' else D('1.01'), at, at, 'independent_market'))
        await service.store._call(service.risk.ready)
        assert await service.store._call(service.risk.ready) is (horizon == 'good')
        assert not service.controller.markout_probe_active()  # Pending/adverse or already sufficient.
    finally:
        await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('objective', ['profit_mm', 'liquidity_service'])
@pytest.mark.parametrize('change', ['none', 'book', 'fee', 'stop', 'risk', 'halt', 'journal'])
async def test_markout_bootstrap_actual_runner_rest_preserves_final_qualification(tmp_path, objective, change):
    from test.hummingbot.strategy_v2.life_liquidity.storage.test_quote_service import (
        test_configured_quote_actual_runner_rest_reprices_after_throttle,
    )
    await test_configured_quote_actual_runner_rest_reprices_after_throttle(
        tmp_path, change, objective, markout_bootstrap=True)
