"""Configured reducing exits share repository money and independent terminal proof."""
from dataclasses import replace
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.test_exit_budget_repository import EXIT_BUDGET
from test.hummingbot.strategy_v2.life_liquidity.storage.test_quote_service import quote_service
from test.hummingbot.strategy_v2.life_liquidity.storage.test_risk_service import policy, sources
from test.hummingbot.strategy_v2.life_liquidity.storage.test_service_control import (
    attach_empty_observer,
    control_policy,
    keys,
)

import pytest

from hummingbot.strategy_v2.life_liquidity.exit_actions import ExitObservation
from hummingbot.strategy_v2.life_liquidity.market_data import InstrumentRules
from hummingbot.strategy_v2.life_liquidity.storage.contracts import encode
from hummingbot.strategy_v2.life_liquidity.storage.service_config import ExitServiceConfig


async def exit_service(tmp_path, *, objective='profit_mm', risk_config=None):
    state, callbacks = sources()
    state['mono'] = 2.05
    callbacks['monotonic_clock'] = lambda: state['mono']
    at = int(state['now'].timestamp() * 1000)
    state['exit'] = ExitObservation(at, D('1'), D('.99'), D('.98'), D('10'), D('0'), 'independent_market',
                                    InstrumentRules(D('.01'), D('.1'), D('.1')))
    callbacks['exit_observation'] = lambda: state['exit']
    signer = keys()
    service, market = await quote_service(
        tmp_path, objective=objective, risk_config=risk_config or policy(), risk_sources=callbacks,
        control_config=control_policy(signer), control_seed=lambda b, o: encode(signer.seed(b, {'mode': 'ACTIVE', 'token': o.json()})),
        exit_config=ExitServiceConfig(target_base=D('3'), max_child_base=D('1'), max_slippage_bps=D('150'), max_age_ms=1000, budget=EXIT_BUDGET))
    attach_empty_observer(service)
    service.controller._spot_quote_gates_ready = lambda: True  # Explicit synthetic listing/feed release.
    await service.store._call(service.risk.ready)
    await service.store._call(service.risk.ready)
    return service, state, market


@pytest.mark.asyncio
@pytest.mark.parametrize('objective', ['profit_mm', 'liquidity_service'])
async def test_explicit_exit_has_separate_budget_and_only_sells_existing_inventory(tmp_path, objective):
    service, _, _ = await exit_service(tmp_path, objective=objective)
    try:
        await service.request_exit('exit-request1')
        actions = await service.plan()
        assert len(actions) == 1, service.exit.reason_code
        config = actions[0].executor_config
        assert (config.side.name, config.amount, config.price) == ('SELL', D('1'), D('.99'))
        assert service.exit.budget is service.store._subsidy.exit
        assert service.exit.budget is not service.quotes.native.subsidy_budget
        assert service.controller._fill_attributor is None
        assert await service.plan() == []
        assert await service.runtime.authorize_action(actions[0])
    finally:
        await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['source', 'depth', 'cross', 'slippage', 'stale', 'fee', 'rules', 'halt'])
async def test_exit_denies_bad_native_market_or_risk_before_holds(tmp_path, fault):
    service, state, _ = await exit_service(tmp_path)
    try:
        await service.request_exit('exit-request1')
        changes = {'source': {'source_kind': 'own_quote'}, 'depth': {'depth_base': D('999')},
                   'cross': {'limit_price_usdt': D('.98')}, 'slippage': {'independent_value_usdt': D('2')},
                   'stale': {'observed_at_ms': state['exit'].observed_at_ms - 1001}, 'fee': {'fee_rate': D('.01')},
                   'rules': {'rules': InstrumentRules(D('.01'), D('1'), D('1'))}}
        if fault == 'halt':
            await service.store._call(service.risk.gate.halt, 'OPERATOR_STOP')
        else:
            state['exit'] = replace(state['exit'], **changes[fault])
        assert await service.plan() == []
        assert service.store._wal.all_records() == ()
    finally:
        await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('objective', ['profit_mm', 'liquidity_service'])
@pytest.mark.parametrize('change', ['none', 'book', 'fee', 'stop', 'risk', 'halt', 'exit_price'])
async def test_exit_actual_runner_rest_retains_original_reducing_terms(tmp_path, objective, change):
    from test.hummingbot.strategy_v2.life_liquidity.storage.test_quote_service import (
        test_configured_quote_actual_runner_rest_reprices_after_throttle,
    )
    await test_configured_quote_actual_runner_rest_reprices_after_throttle(
        tmp_path, change, objective, exit_route=True)


@pytest.mark.asyncio
async def test_exit_degraded_permission_cannot_authorize_quote_or_swap(tmp_path):
    from datetime import timedelta
    service, state, _ = await exit_service(tmp_path, risk_config=policy(stable_data_ms=100))
    try:
        await service.request_exit('degraded-exit')
        state['now'] += timedelta(milliseconds=100)
        state['mono'] += .1
        state['risk'] = replace(state['risk'], observed_monotonic_ms=2150)
        actions = await service.plan()
        assert len(actions) == 1, service.exit.reason_code
        assert service.risk.gate.state == 'DEGRADED'
        assert not service.risk.local_ready()
        assert service.risk.local_ready(reduce_only=True)
        assert service.runtime.preflight(actions[0])
        for changes in ({'connector_name': 'okx_perpetual'}, {'side': actions[0].executor_config.side.BUY}, {'amount': D('2')}):
            forged = actions[0].model_copy(update={'executor_config': actions[0].executor_config.model_copy(update=changes)})
            assert not service.runtime.preflight(forged)
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_exit_stop_requires_fresh_quiescence_and_replayed_request_does_not_reopen(tmp_path):
    from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
    service, _, _ = await exit_service(tmp_path)
    try:
        await service.request_exit('request-once')
        action = (await service.plan())[0]
        with pytest.raises(StorageError):
            await service.stop_exit('cannot-stop-pending')
        service.rejected(action)
        await service.drain_action_events()
        await service.stop_exit('stop-exit')
        assert not service.exit.active
        await service.request_exit('request-once')
        assert not service.exit.active
        ordinary = (await service.plan())[0]
        assert not service.exit.handles(ordinary.executor_config)
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_overlapping_control_collection_cannot_lose_admission_barrier_after_exit_request(tmp_path, monkeypatch):
    import asyncio
    import threading
    service, _, _ = await exit_service(tmp_path)
    entered, release = threading.Event(), threading.Event()
    collecting, finish = asyncio.Event(), asyncio.Event()
    original = service.exit.request

    def wait_worker(*args):
        entered.set()
        assert release.wait(3)
        return original(*args)
    monkeypatch.setattr(service.exit, 'request', wait_worker)
    first = asyncio.create_task(service.request_exit('concurrent-exit'))
    second = None
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        collect = service.spot._observer.control_snapshot

        async def wait_collection():
            collecting.set()
            await finish.wait()
            return await collect()
        monkeypatch.setattr(service.spot._observer, 'control_snapshot', wait_collection)
        second = asyncio.create_task(service.control.snapshot())
        await asyncio.sleep(0)
        release.set()
        await first
        await asyncio.wait_for(collecting.wait(), 1)
        assert service.admission_paused
        finish.set()
        await second
        assert service.admission_paused  # Snapshot still awaits an explicit command.
    finally:
        release.set()
        finish.set()
        await asyncio.gather(first, *([second] if second else []), return_exceptions=True)
        await service.close()
