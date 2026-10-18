"""Replay genuine File settlements with quote/base fees and profit/loss cycles."""
import json
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage import test_spot_settlement as file_cycle

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError, thaw
from hummingbot.strategy_v2.life_liquidity.storage.postgres.legacy_settlement_basis import settlement_basis

terminal_bundle = file_cycle.terminal_bundle


async def cycle_source(bundle, currency='USDT', price=D('1')):
    store, _, binding, owner, _ = bundle
    await file_cycle.closed_cycle(store, binding, owner, fee_currency=currency,
                                  sell_quantity=D('.99') if currency == 'LIFE' else D('1'), sell_price=price)
    receipt = await store.settle_inventory(file_cycle.command(), facts=await file_cycle.evidence(store, binding),
                                           cut=(await store.snapshot(binding)).cut, owner=owner)
    resources = thaw((await store.read(binding)).resources)
    raw = await store._call(store._raw)
    return receipt, resources, json.loads(raw['wal'])['records'], json.loads(raw['subsidy'])


@pytest.mark.asyncio
@pytest.mark.parametrize('currency', ['USDT', 'LIFE'])
@pytest.mark.parametrize('price', [D('.8'), D('1'), D('1.2')])
async def test_actual_file_cycle_cost_survives_base_fees_and_profitable_or_lossy_round_trips(terminal_bundle, currency, price):
    receipt, resources, records, subsidy = await cycle_source(terminal_bundle, currency, price)
    basis, actuals = settlement_basis(resources, records, subsidy)
    assert actuals == dict(receipt.actuals_usdt)
    assert basis['spot_settlements'][0]['proof'] == receipt.proof_digest
    assert basis['spot_settlements'][0]['command'] == file_cycle.command().json()
    assert actuals['intent1'] > 0  # A profitable close cannot erase the paid fill cost.


@pytest.mark.asyncio
async def test_rehashed_subsidy_overlay_cannot_erase_original_economic_cost(terminal_bundle):
    _, resources, records, subsidy = await cycle_source(terminal_bundle)
    row = resources['settlement_operations']['settle1']
    row['actuals']['intent1'] = '0'
    for entry in subsidy['entries'].values():
        entry['inventory_cycle']['actuals']['intent1'] = '0'
    subsidy['entries']['intent1']['actual'] = '0'
    with pytest.raises(StorageError, match='ORIGINAL_SETTLEMENT_BASIS_REQUIRED'):
        settlement_basis(resources, records, subsidy)
