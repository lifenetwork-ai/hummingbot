"""Configured risk consumes the sole financial store and fences actual sends."""
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.test_quote_service import quote_service
from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_financial_contracts import NOW

import pytest

from hummingbot.strategy_v2.life_liquidity.capital_risk import IndependentNavObservation
from hummingbot.strategy_v2.life_liquidity.safety import SafetyObservation
from hummingbot.strategy_v2.life_liquidity.storage.service_config import RiskServiceConfig


def policy(**changes):
    return RiskServiceConfig(**(dict(max_drawdown_bps=D('100'), min_margin_buffer_quote=D('0'),
                                     stable_data_ms=0, recovery_probe_base=D('.1'), max_observation_age_ms=1000,
                                     max_value_age_ms=1000) | changes))


def sources():
    at = int(NOW.timestamp() * 1000)
    state = {'now': NOW, 'nav': IndependentNavObservation(D('1'), at, at, 'independent_market'),
             'risk': SafetyObservation(2050, True, True, True, True, D('0'), D('10'))}
    return state, dict(clock=lambda: state['now'], nav_observation=lambda: state['nav'], safety_observation=lambda: state['risk'])


@pytest.mark.asyncio
async def test_configured_risk_records_cashflow_adjusted_nav_and_blocks_drawdown(tmp_path):
    state, callbacks = sources()
    service, _ = await quote_service(tmp_path, risk_config=policy(), risk_sources=callbacks)
    try:
        assert not await service.store._call(service.risk.ready)  # Explicit degraded startup.
        assert await service.store._call(service.risk.ready)
        capital = await service.store.capital(service.store.binding)
        assert capital.current and capital.nav_usdt == D('15')
        assert service.controller._fill_attributor is None
        assert len(await service.plan()) == 1
        # The source's drawdown number is ignored; the qualified wallet owns NAV.
        state['now'] += timedelta(milliseconds=1)
        state['nav'] = replace(state['nav'], price_usdt=D('.5'), value_at_ms=state['nav'].value_at_ms + 1,
                               observed_at_ms=state['nav'].observed_at_ms + 1)
        assert not await service.store._call(service.risk.ready)
        assert await service.plan() == []
        assert (await service.store.capital(service.store.binding)).highwater_usdt == D('15')
    finally:
        await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['stale', 'foreign', 'future', 'margin', 'latency', 'policy'])
async def test_risk_source_or_policy_failure_closes_action_admission_without_money(tmp_path, fault):
    state, callbacks = sources()
    service, _ = await quote_service(tmp_path, risk_config=policy(), risk_sources=callbacks)
    try:
        if fault == 'stale':
            state['nav'] = replace(state['nav'], value_at_ms=state['nav'].value_at_ms - 1001)
        elif fault == 'foreign':
            state['nav'] = replace(state['nav'], source_kind='own_quote')
        elif fault == 'future':
            state['nav'] = replace(state['nav'], observed_at_ms=state['nav'].observed_at_ms + 1)
        elif fault == 'margin':
            state['risk'] = replace(state['risk'], margin_buffer_quote=D('-1'))
        elif fault == 'latency':
            state['risk'] = replace(state['risk'], latency_ok=False)
        else:
            service.risk.policy = policy(max_drawdown_bps=D('200'))
        assert await service.plan() == []
        assert (await service.store.snapshot(service.store.binding)).balance.total_quote_usdt == D('10')
        assert service.store._wal.all_records() == ()
    finally:
        await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['none', 'early', 'late', 'own', 'missing'])
async def test_markout_uses_accounted_fills_and_independent_horizon_then_reuses_durable_observation(tmp_path, fault):
    from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_fill_contracts import command, fill, prepare_arm

    from hummingbot.strategy_v2.life_liquidity.markout_risk import IndependentHorizonObservation
    from hummingbot.strategy_v2.life_liquidity.storage.service_config import MarkoutServiceConfig
    p = MarkoutServiceConfig(horizons_ms=(100,), min_samples=1, size_cutoff_base=D('2'), cohort_window_ms=1000,
                             max_horizon_lag_ms=50, min_mean_markout_quote=D('0'), monitored_cohorts=(('BUY', 'small', 100),))
    state, callbacks = sources()
    at = state['nav'].value_at_ms
    horizon = {'value': IndependentHorizonObservation(D('1.01'), at + 100, at + 100, 'independent_market')}
    callbacks['horizon_observation'] = lambda *_: horizon['value']
    service, _ = await quote_service(tmp_path, risk_config=policy(markout=p), risk_sources=callbacks)
    try:
        assert not await service.store._call(service.risk.markout_ready)  # No invented bootstrap sample.
        await prepare_arm(service.store, service.store.binding, service.spot.owner)
        event = fill(signed_fee=D('0'), independent_price_usdt=D('1'))
        await service.store.apply_fills(command(service.spot.owner, fills=(event,)),
                                        cut=(await service.store.snapshot(service.store.binding)).cut, owner=service.spot.owner)
        state['now'] += timedelta(milliseconds=100)
        if fault == 'early':
            horizon['value'] = replace(horizon['value'], value_at_ms=at + 99)
        elif fault == 'late':
            state['now'] += timedelta(milliseconds=51)
            horizon['value'] = replace(horizon['value'], value_at_ms=at + 151, observed_at_ms=at + 151)
        elif fault == 'own':
            horizon['value'] = replace(horizon['value'], source_kind='own_quote')
        elif fault == 'missing':
            horizon['value'] = None
        view = await service.store.snapshot(service.store.binding)
        assert await service.store._call(service.risk.markout_ready) is (fault == 'none')
        if fault == 'none':
            horizon['value'] = None
            assert await service.store._call(service.risk.markout_ready)
        after = await service.store.snapshot(service.store.binding)
        assert after.balance == view.balance and after.budget == view.budget
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_risk_cold_restart_preserves_highwater_and_never_rearms(tmp_path):
    from hummingbot.strategy_v2.life_liquidity.storage.service import FileLiquidityService
    state, callbacks = sources()
    service, _ = await quote_service(tmp_path, risk_config=policy(), risk_sources=callbacks)
    assert not await service.store._call(service.risk.ready)
    assert await service.store._call(service.risk.ready)
    args = dict(config=service.config, connectors=service.connectors, sources=service.sources,
                market_data_provider=service.controller.market_data_provider)
    await service.close()
    cold = await FileLiquidityService.open(**args)
    try:
        assert cold.store._cold_unarmed and cold.risk.gate.rearm_required
        assert not await cold.store._call(cold.risk.ready)
        assert await cold.plan() == []
        assert (await cold.store.capital(cold.store.binding)).highwater_usdt == D('15')
    finally:
        await cold.close()


@pytest.mark.asyncio
async def test_manual_halt_revokes_immediately_and_waits_for_worker_capacity_before_checkpoint(tmp_path):
    import asyncio
    import threading
    state, callbacks = sources()
    service, _ = await quote_service(tmp_path, risk_config=policy(), risk_sources=callbacks)
    entered, release = threading.Event(), threading.Event()

    def blocked():
        entered.set()
        assert release.wait(3)
    running = asyncio.create_task(service.store._call(blocked))
    await asyncio.to_thread(entered.wait, 2)
    try:
        service.controller.manual_kill_switch()
        first = service._halt_task
        service.controller.manual_kill_switch()
        assert service._halt_task is first and not service.runtime._local_ready()
        assert not first.done()
        release.set()
        await running
        await first
        assert service.risk.gate.state == 'HALTED'
        assert await service.store._call(service.risk.gate.journal_verified)
        assert await service.plan() == []
    finally:
        release.set()
        await running
        await service.close()


@pytest.mark.asyncio
async def test_unbacked_margin_hint_cannot_exceed_available_account_cash(tmp_path):
    state, callbacks = sources()
    state['risk'] = replace(state['risk'], margin_buffer_quote=D('100'))
    service, _ = await quote_service(tmp_path, risk_config=policy(min_margin_buffer_quote=D('11')), risk_sources=callbacks)
    try:
        await service.store._call(service.risk.ready)
        assert not await service.store._call(service.risk.ready)
        assert await service.plan() == []
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_same_independent_value_timestamp_cannot_rewrite_the_price(tmp_path):
    state, callbacks = sources()
    service, _ = await quote_service(tmp_path, risk_config=policy(), risk_sources=callbacks)
    try:
        await service.store._call(service.risk.ready)
        assert await service.store._call(service.risk.ready)
        state['nav'] = replace(state['nav'], price_usdt=D('.99'))
        assert not await service.store._call(service.risk.ready)
        assert (await service.store.capital(service.store.binding)).independent_price_usdt == D('1')
    finally:
        await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('bad_index', [False, True])
async def test_configured_joint_risk_uses_joint_nav_without_adding_collateral_or_second_spot_nav(tmp_path, bad_index):
    from test.hummingbot.strategy_v2.life_liquidity.storage.test_service_startup import joint_service
    state, callbacks = sources()
    if bad_index:
        state['nav'] = replace(state['nav'], price_usdt=D('2'))
    service, _, _, _, _ = await joint_service(tmp_path, hedge=False, config_extra={'risk': policy()},
                                              source_extra=lambda *_: {**callbacks, 'monotonic_clock': lambda: 2.05})
    try:
        await service.recover()
        await service.store._call(service.risk.ready)
        assert await service.store._call(service.risk.ready) is (not bad_index)
        assert await service.store.capital(service.store.binding) is None
        measured = await service.store._call(service.joint._recovery.measure)
        assert measured.nav_quote == D('15')  # Collateral is not another wallet.
    finally:
        await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['latency', 'value', 'expiry'])
async def test_known_changed_or_expired_risk_inputs_close_loop_local_final_fence(tmp_path, change):
    state, callbacks = sources()
    service, _ = await quote_service(tmp_path, risk_config=policy(), risk_sources=callbacks)
    try:
        await service.store._call(service.risk.ready)
        assert await service.store._call(service.risk.ready)
        assert service.runtime._local_ready() and service.spot.local_ready()
        if change == 'latency':
            state['risk'] = replace(state['risk'], latency_ok=False)
        elif change == 'value':
            state['nav'] = replace(state['nav'], price_usdt=D('.99'))
        else:
            state['now'] += timedelta(milliseconds=1001)
        # Final local fences consume cache-only source inputs; no file call or
        # asynchronous repository reply can hide a newly known stale/bad input.
        assert not service.runtime._local_ready()
        assert not service.spot.local_ready()
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_benign_cache_timestamp_refresh_keeps_original_proof_lifetime_without_revoking_it(tmp_path):
    state, callbacks = sources()
    state['mono'] = 2.05
    callbacks['monotonic_clock'] = lambda: state['mono']
    service, _ = await quote_service(tmp_path, risk_config=policy(), risk_sources=callbacks)
    try:
        await service.store._call(service.risk.ready)
        assert await service.store._call(service.risk.ready)
        state['now'] += timedelta(milliseconds=1)
        state['mono'] = 2.052
        state['nav'] = replace(state['nav'], value_at_ms=state['nav'].value_at_ms + 1,
                               observed_at_ms=state['nav'].observed_at_ms + 1)
        state['risk'] = replace(state['risk'], observed_monotonic_ms=2051)
        assert service.runtime._local_ready() and service.spot.local_ready()
        # A new cached timestamp cannot extend the old worker proof's deadline.
        state['now'] += timedelta(milliseconds=1000)
        assert not service.runtime._local_ready()
    finally:
        await service.close()
