"""Configured carry uses independent native streams on the one financial worker."""
from dataclasses import replace
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.test_service_startup import joint_service

import pytest


@pytest.mark.asyncio
async def test_configured_carry_cold_readback_and_changed_source_never_reset_money(tmp_path):
    from hummingbot.strategy_v2.life_liquidity.storage.service import FileLiquidityService
    service, config, sources, spot, swap = await joint_service(tmp_path, carry=True)
    try:
        await service.recover()
        decision = await service.products.check_carry(service.store.binding)
        assert decision.allowed, decision.reason_code
        assert service.runtime._products is service.products
        assert service.products._carry is service.controller._carry_monitor
        first = await service.store.snapshot(service.store.binding)
        assert len(await service.plan()) == 0  # Production remains disabled.
    finally:
        await service.close()
    cold = await FileLiquidityService.open(config, connectors={'okx': spot, 'okx_perpetual': swap}, sources=sources,
                                           market_data_provider=service.controller.market_data_provider, create_binding=False)
    try:
        await cold.recover()
        assert (await cold.products.check_carry(cold.store.binding)).allowed
        assert (await cold.store.snapshot(cold.store.binding)).balance == first.balance
        assert cold.store._cold_unarmed and not cold.runtime._local_ready()
    finally:
        await cold.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['mark', 'terms', 'funding', 'account'])
async def test_configured_carry_invalid_stream_closes_planning_without_cash_or_claims(tmp_path, fault):
    service, _, sources, _, _ = await joint_service(tmp_path, carry=True)
    try:
        await service.recover()
        before = await service.store.snapshot(service.store.binding)
        original = sources.carry_observation()
        if fault == 'mark':
            bad = replace(original, mark=replace(original.mark, observed_at_ms=original.mark.observed_at_ms - 1001))
        elif fault == 'terms':
            bad = replace(original, terms=replace(original.terms, scope_complete=False))
        elif fault == 'funding':
            bad = replace(original, funding=replace(original.funding, rate=D('NaN')))
        else:
            bad = replace(original, account=replace(original.account, settled_funding_included=False))
        # Inject an explicitly captured invalid source; native validators remain active.
        service.controller._carry_monitor.observation = lambda: bad
        service.controller.trading_permissions_ready = lambda: True
        service.controller.allow_create_executor_actions = lambda: True
        assert await service.plan() == []
        assert not (await service.products.check_carry(service.store.binding)).allowed
        after = await service.store.snapshot(service.store.binding)
        assert after.balance == before.balance and after.budget == before.budget
        assert await service.store._call(service.controller._shared_capital_authority.claim_ids) == ()
        assert service.swap.sender.wal.all_records() == ()
    finally:
        await service.close()
