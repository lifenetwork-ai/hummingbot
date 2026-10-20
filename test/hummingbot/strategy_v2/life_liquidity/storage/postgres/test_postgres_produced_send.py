"""Original native producer -> capital -> V2 executor -> SDK -> durable ACK."""
import json
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_spot_market_send import application

import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize('side', ['BUY', 'SELL'])
@pytest.mark.parametrize('objective', ['profit_mm', 'liquidity_service'])
@pytest.mark.parametrize('shared_quota', [False, True])
async def test_imported_levels_and_native_cost_producer_reach_actual_executor_sdk_ack(database, side, objective, shared_quota):
    x = await application(database, side=side, objective=objective, produced=True, shared_quota=shared_quota)
    try:
        await x.start()
        try:
            await x.app._pending['quote1']
        except BaseException as error:
            error.add_note(str(x.final_checks))
            raise
        await x.app.drain()
        posts = [r for r in x.t.requests if r['method'] == 'POST']
        assert len(posts) == 1 and json.loads(posts[0]['data'])['clOrdId'] == x.wire
        assert (await x.t.financial.attempt('quote1', x.t.cfg.binding)).state.value == 'ACKED'
        entry = x.source.journal._entries['quote1']
        costs = entry.prepared
        assert costs.admission.carry_cost_usdt == D('.0001')
        assert costs.admission.inventory_risk_usdt == (D('.0002') if side == 'BUY' else 0)
        assert costs.prepare.fee_buffer_base_life == D('.0005')
        assert x.app._prepare == x.producer.prepare
        assert x.t.stream_sequence == 3  # Proposal, preparation, final SDK read.
    finally:
        await x.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('shared_quota', [False, True])
async def test_produced_liquidity_quote_holds_its_exact_calculated_loss_and_costs(database, shared_quota):
    x = await application(database, objective='liquidity_service', price=D('.99'), produced=True, shared_quota=shared_quota)
    try:
        await x.start()
        try:
            await x.app._pending['quote1']
        except BaseException as error:
            error.add_note(str(x.final_checks))
            raise
        prepared = x.source.journal._entries['quote1'].prepared
        # .99 entry/exit, .000495 maker + .00198 exit + .0001 carry + .0002 inventory.
        assert prepared.prepare.subsidy_quote_usdt == D('.002775')
        assert (await x.t.financial.attempt('quote1', x.t.cfg.binding)).state.value == 'ACKED'
        assert len([r for r in x.t.requests if r['method'] == 'POST']) == 1
    finally:
        await x.close()


@pytest.mark.asyncio
async def test_native_scale_subsidy_can_reach_prepare_and_final_arm_without_underfunding(database):
    x = await application(database, objective='liquidity_service', maker=D('1e-18'), price=D('.99'), produced=True)
    try:
        await x.start()
        await x.app._pending['quote1']
        prepared = x.source.journal._entries['quote1'].prepared
        assert prepared.prepare.subsidy_quote_usdt == D('.002280000000000001')
        assert prepared.prepare.fee_buffer_quote_usdt == D('1e-18')
        assert (await x.t.financial.attempt('quote1', x.t.cfg.binding)).state.value == 'ACKED'
        assert len([r for r in x.t.requests if r['method'] == 'POST']) == 1
    finally:
        await x.close()
