"""Configured recovery probes keep native size, money and final-send authority."""
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.test_quote_service import quote_service
from test.hummingbot.strategy_v2.life_liquidity.storage.test_risk_service import policy, sources

import pytest

from hummingbot.strategy_v2.life_liquidity.config import ResumePolicyConfig
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
from hummingbot.strategy_v2.life_liquidity.storage.service import FileLiquidityService
from hummingbot.strategy_v2.life_liquidity.storage.service_config import FileServiceConfig


async def probe_service(tmp_path, *, risk_changes=None, objective='profit_mm'):
    state, callbacks = sources()
    state['mono'] = 2.05
    callbacks['monotonic_clock'] = lambda: state['mono']
    callbacks['horizon_observation'] = lambda *_: None
    service, market = await quote_service(
        tmp_path, objective=objective,
        risk_config=policy(**(dict(stable_data_ms=100, recovery_probe_campaign_base=D('.2')) | (risk_changes or {}))),
        risk_sources=callbacks, resume_policy=ResumePolicyConfig(stable_data_duration='0.1s', probe_size_base='.1'))
    return service, state, market


async def degraded(service, state):
    assert not await service.store._call(service.risk.ready)
    state['mono'] += .1
    state['now'] += timedelta(milliseconds=100)
    state['risk'] = replace(state['risk'], observed_monotonic_ms=2150)
    assert await service.store._call(service.risk.ready)
    assert service.risk.gate.state == 'DEGRADED'


@pytest.mark.asyncio
async def test_probe_clamps_native_quote_and_blocks_parallel_campaign_risk(tmp_path):
    service, state, _ = await probe_service(tmp_path)
    try:
        await degraded(service, state)
        assert service.controller.recovery_probe_active()
        actions = await service.plan()
        assert len(actions) == 1
        config = actions[0].executor_config
        assert config.amount == D('.1')
        assert await service.store._call(service.quotes.native.authorizes_config, config)
        assert not await service.store._call(service.quotes.native.authorizes_config,
                                             config.model_copy(update={'amount': D('1')}))
        assert await service.plan() == []
        assert service.store._wal.all_records() == ()
        assert service.controller._fill_attributor is None
        # Probe permission does not authorize inventory or SWAP routes.
        assert not await service.store._call(lambda: service.risk.ready(normal_only=True))
        assert service.risk.local_ready() and not service.risk.local_ready(normal_only=True)
    finally:
        await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['latency', 'expiry', 'halt', 'guard', 'config'])
async def test_probe_local_and_worker_fences_close_without_issuing_money(tmp_path, fault):
    service, state, _ = await probe_service(tmp_path)
    try:
        await degraded(service, state)
        action = (await service.plan())[0]
        assert service.runtime.preflight(action)
        if fault == 'latency':
            state['risk'] = replace(state['risk'], latency_ok=False)
        elif fault == 'expiry':
            state['now'] += timedelta(milliseconds=1001)
        elif fault == 'halt':
            await service.store._call(service.risk.gate.halt, 'OPERATOR_STOP')
        elif fault == 'guard':
            service.controller._recovery_probe_guard.max_quote_base = D('1')
        else:
            service.risk.policy = policy(stable_data_ms=100, recovery_probe_campaign_base=D('1'))
        assert not service.runtime.preflight(action)
        assert not service.spot.local_ready()
        assert not await service.runtime.authorize_action(action)
        assert await service.plan() == []
        assert service.store._wal.all_records() == ()
    finally:
        await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['size', 'duration', 'quotes'])
async def test_probe_graph_rejects_inconsistent_native_policy(tmp_path, change):
    service, _, _ = await probe_service(tmp_path)
    cfg = service.config
    await service.close()
    values = {k: getattr(cfg, k) for k in FileServiceConfig.model_fields}
    if change == 'quotes':
        values['quotes'] = None
    else:
        values['risk'] = cfg.risk.model_copy(update={
            'recovery_probe_base': D('.2')} if change == 'size' else {'stable_data_ms': 101})
    with pytest.raises(StorageError, match='SERVICE_RECOVERY_PROBE_GRAPH_INVALID'):
        FileServiceConfig.model_validate(values)


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['none', 'missing', 'policy'])
async def test_probe_cold_readback_never_rearms_or_recreates_history(tmp_path, change):
    service, state, _ = await probe_service(tmp_path)
    await degraded(service, state)
    args = dict(config=service.config, connectors=service.connectors, sources=service.sources,
                market_data_provider=service.controller.market_data_provider)
    path = service.controller._recovery_probe_guard.journal.path
    await service.close()
    if change == 'missing':
        path.unlink()
    elif change == 'policy':
        args['config'] = args['config'].model_copy(update={
            'risk': args['config'].risk.model_copy(update={'recovery_probe_campaign_base': D('1')})})
    if change != 'none':
        with pytest.raises((StorageError, ValueError, RuntimeError)):
            cold = await FileLiquidityService.open(**args)
            await cold.close()
        assert path.exists() is (change != 'missing')
    else:
        cold = await FileLiquidityService.open(**args)
        try:
            assert cold.store._cold_unarmed and cold.risk.gate.rearm_required
            assert not await cold.store._call(cold.risk.ready)
            assert await cold.plan() == []
        finally:
            await cold.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['none', 'book', 'fee', 'stop', 'risk', 'halt', 'normal', 'journal'])
@pytest.mark.parametrize('objective', ['profit_mm', 'liquidity_service'])
async def test_actual_probe_runner_rest_retains_final_guards(tmp_path, change, objective):
    from test.hummingbot.strategy_v2.life_liquidity.storage.test_quote_service import (
        test_configured_quote_actual_runner_rest_reprices_after_throttle,
    )
    await test_configured_quote_actual_runner_rest_reprices_after_throttle(
        tmp_path, change, objective, recovery_probe=True)


@pytest.mark.asyncio
async def test_probe_actual_fills_consume_campaign_capacity_once_across_cold_restart(tmp_path):
    from hummingbot.strategy_v2.life_liquidity.storage.financial import (
        AckSpot,
        FillSpot,
        PrepareSpot,
        SpotFillObservation,
    )
    from hummingbot.strategy_v2.life_liquidity.storage.terminal import SpotTerminalFacts, TerminalSpot
    service, state, _ = await probe_service(tmp_path)
    try:
        await degraded(service, state)
        store, owner = service.store, service.spot.owner
        base, cash, trade_ids = D('5'), D('10'), []
        for i in range(2):
            action = (await service.plan())[0]
            cfg = action.executor_config
            assert cfg.amount == D('.1')
            await store._call(service.quotes.transition, service.quotes.capture(action), 'DISPATCHED')
            terms = await store._call(service.quotes.admission, cfg)
            prepare = PrepareSpot('probe-prepare-' + str(i), cfg.id, 'probewire' + str(i), cfg.trading_pair,
                                  cfg.side.name, cfg.amount, cfg.price, terms.reference_price_usdt,
                                  terms.subsidy_quote_usdt, str(owner.session_id), owner.epoch, 1, 0,
                                  terms.fee_buffer_quote_usdt, terms.fee_buffer_base_life)
            await store.prepare_current(prepare, owner=owner)
            await store.arm_current(prepare.operation_id, owner=owner)
            now, exchange_id = state['now'], str(100 + i)
            await store.acknowledge(AckSpot('probe-ack-' + str(i), prepare.operation_id, cfg.id, prepare.client_order_id,
                                            cfg.trading_pair, cfg.side.name, str(owner.session_id), owner.epoch, exchange_id, now),
                                    cut=(await store.snapshot(store.binding)).cut, owner=owner)
            at = int(now.timestamp() * 1000)
            event = SpotFillObservation('probe-trade-' + str(i), cfg.amount, cfg.price, 'USDT', D('0'),
                                        at, D('1'), at, at, 'independent_market')
            trade_ids.append(event.trade_id)
            command = FillSpot('probe-fill-' + str(i), prepare.operation_id, cfg.id, prepare.client_order_id,
                               cfg.trading_pair, cfg.side.name, str(owner.session_id), owner.epoch, exchange_id,
                               cfg.amount, (event,), now)
            cut = (await store.snapshot(store.binding)).cut
            await store.apply_fills(command, cut=cut, owner=owner)
            assert (await store.apply_fills(command, cut=cut, owner=owner)).replayed
            sign = D('1') if cfg.side.name == 'BUY' else D('-1')
            base += sign * cfg.amount
            cash -= sign * cfg.amount * cfg.price
            facts = SpotTerminalFacts(store.binding, now, 'FILLED', cfg.amount, cfg.amount, cfg.price, (event,),
                                      base, cash, D('0'), D('0'), tuple(trade_ids), (), (), (),
                                      (prepare.client_order_id,), True, True, True)
            terminal = TerminalSpot('probe-terminal-' + str(i), prepare.operation_id, cfg.id, prepare.client_order_id,
                                    cfg.trading_pair, cfg.side.name, str(owner.session_id), owner.epoch, exchange_id, facts)
            await store.confirm_terminal(terminal, cut=(await store.snapshot(store.binding)).cut, owner=owner)
        assert not await store._call(service.risk.probe.capacity_available)
        assert await service.plan() == []
        before = await store.snapshot(store.binding)
        assert before.balance.total_base_life == base and before.balance.total_quote_usdt == cash
        args = dict(config=service.config, connectors=service.connectors, sources=service.sources,
                    market_data_provider=service.controller.market_data_provider)
        await service.close()
        service = await FileLiquidityService.open(**args)
        assert not await service.store._call(service.risk.probe.capacity_available)
        assert await service.plan() == []
        after = await service.store.snapshot(service.store.binding)
        assert after.balance == before.balance and after.budget == before.budget
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_probe_loop_scope_rejects_large_or_swap_action_before_queueing(tmp_path):
    service, state, _ = await probe_service(tmp_path)
    try:
        await degraded(service, state)
        action = (await service.plan())[0]
        assert service.runtime.preflight(action)
        for changes in ({'amount': D('1')}, {'amount': D('NaN')}, {'connector_name': 'okx_perpetual'}, {'trading_pair': 'OTHER-USDT'}):
            forged = action.model_copy(update={'executor_config': action.executor_config.model_copy(update=changes)})
            assert not service.runtime.preflight(forged)
            assert not await service.runtime.authorize_action(forged)
        assert service.risk.local_ready()
    finally:
        await service.close()


def test_absent_probe_opt_in_retains_original_risk_binding_json():
    p = policy()
    assert p.recovery_probe_campaign_base is None
    assert p.binding_json() == {'max_drawdown_bps': '100', 'min_margin_buffer_quote': '0',
                                'stable_data_ms': 0, 'recovery_probe_base': '0.1',
                                'max_observation_age_ms': 1000, 'max_value_age_ms': 1000, 'markout': None}


@pytest.mark.parametrize('limit', [D('0'), D('-.1'), D('.09'), D('NaN'), D('Infinity')])
def test_probe_campaign_limit_requires_explicit_finite_capacity(limit):
    with pytest.raises((StorageError, ValueError)):
        policy(recovery_probe_campaign_base=limit)


@pytest.mark.asyncio
@pytest.mark.parametrize('side', ['BUY', 'SELL'])
async def test_inventory_mode_cannot_spend_probe_permission(tmp_path, side):
    from test.hummingbot.strategy_v2.life_liquidity.storage.test_inventory_service import (
        activate_inventory,
        inventory_service,
    )
    service, state, _, signer = await inventory_service(tmp_path, side=side, recovery_probe=True)
    try:
        await activate_inventory(service, signer)
        await degraded(service, state)
        assert service.risk.local_ready()
        assert not await service.store._call(service.inventory.permissions)
        assert not await service.store._call(service.inventory.pending_exit_ready)
        assert await service.plan() == []
        assert service.store._wal.all_records() == ()
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_probe_does_not_invent_markout_bootstrap_samples(tmp_path):
    from hummingbot.strategy_v2.life_liquidity.storage.service_config import MarkoutServiceConfig
    markout = MarkoutServiceConfig(horizons_ms=(100,), min_samples=1, size_cutoff_base=D('2'), cohort_window_ms=1000,
                                   max_horizon_lag_ms=50, min_mean_markout_quote=D('0'), monitored_cohorts=(('BUY', 'small', 100),))
    service, state, _ = await probe_service(tmp_path, risk_changes={'markout': markout})
    try:
        assert not await service.store._call(service.risk.ready)
        state['now'] += timedelta(milliseconds=100)
        state['mono'] += .1
        state['risk'] = replace(state['risk'], observed_monotonic_ms=2150)
        assert not await service.store._call(service.risk.ready)
        assert service.risk.gate.state == 'DEGRADED'
        assert not service.risk.local_ready()
        assert await service.plan() == []
        assert service.store._wal.all_records() == ()
    finally:
        await service.close()
