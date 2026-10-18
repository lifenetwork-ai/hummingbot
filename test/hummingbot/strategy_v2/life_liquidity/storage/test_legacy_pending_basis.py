"""Read-only admission of actual interrupted File terminal/inventory images."""
import copy
import json
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.test_joint_settlement_composition import pending_cycle
from test.hummingbot.strategy_v2.life_liquidity.storage.test_joint_terminal_composition import pending_terminal

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError, thaw
from hummingbot.strategy_v2.life_liquidity.storage.postgres.legacy_pending_basis import pending_view
from hummingbot.strategy_v2.life_liquidity.storage.postgres.legacy_settlement_basis import settlement_basis
from hummingbot.strategy_v2.life_liquidity.storage.postgres.legacy_terminal_basis import terminal_basis


async def images(store):
    resources = thaw((await store.read(store.binding)).resources)
    raw = await store._call(store._raw)
    return resources, json.loads(raw['reservations']), json.loads(raw['subsidy']), json.loads(raw['wal'])['records']


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['observation', 'principal', 'wal', 'receipt'])
async def test_actual_terminal_partial_images_are_only_an_admission_view_with_exact_original_evidence(tmp_path, monkeypatch, stage):
    values = await pending_terminal(tmp_path, monkeypatch, stage=stage)
    store, terminal = values[1], values[8]
    try:
        original = await images(store)

        def qualify(data, selected=(terminal.operation_id,)):
            resources, wallet, subsidy, records, pending = pending_view(*data, terminal_ids=selected)
            quantities = {}
            for row in resources.get('fills', {}).values():
                quantities[row['intent_id']] = quantities.get(row['intent_id'], D('0')) + D(row['fill']['quantity_base_life'])
            terminal_basis(resources, records, wallet, quantities)
            return resources, wallet, subsidy, records, pending
        result = qualify(original)
        assert await images(store) == original
        assert result[-1] == {'spot_pending_terminal_ids': [terminal.operation_id]}
        assert result[0]['terminal_operations'][terminal.operation_id]['state'] == 'TERMINAL'
        for selected in ((), (terminal.operation_id,) * 2, ('invented',)):
            with pytest.raises(StorageError):
                qualify(original, selected)
        for fault in ('wire', 'exchange', 'partial_quantity', 'premature_subsidy', 'unobserved_terminal', 'metadata', 'hash'):
            changed = copy.deepcopy(original)
            row = changed[0]['terminal_operations'][terminal.operation_id]
            intent = terminal.intent_id
            if fault == 'wire':
                changed[3][intent]['client_order_id'] = 'other'
            elif fault == 'exchange':
                changed[3][intent]['exchange_order_id'] = 'other'
            elif fault == 'partial_quantity':
                changed[1]['reservations'][intent]['filled_base'] = '0'
            elif fault == 'premature_subsidy':
                changed[2]['entries'][intent]['actual'] = '0'
            elif fault == 'unobserved_terminal':
                changed[3][intent].update(state='TERMINAL', exchange_terminal_observed=False)
            elif fault == 'metadata':
                changed[0]['commands'][terminal.prepare_operation_id]['state'] = 'ACKED'
            else:
                row['fingerprint'] = 'f' * 64
            with pytest.raises(StorageError):
                qualify(changed)
    finally:
        await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['before', 'after', 'receipt'])
async def test_actual_pending_cycle_replays_original_cost_and_refuses_mixed_or_changed_budget_images(tmp_path, monkeypatch, stage):
    values = await pending_cycle(tmp_path, monkeypatch, stage=stage)
    store, command = values[1], values[7]
    try:
        original = await images(store)

        def qualify(data, selected=(command.operation_id,)):
            resources, wallet, subsidy, records, pending = pending_view(*data, settlement_ids=selected)
            basis, actuals = settlement_basis(resources, records, subsidy)
            assert sum(actuals.values()) == D('.24')
            return basis, pending
        pending = original[0]['settlement_operations'][command.operation_id]['state'] == 'SETTLING'
        assert qualify(original)[1] == ({'spot_pending_settlement_ids': [command.operation_id]} if pending else {})
        assert await images(store) == original
        for selected in (((),) if pending else ()) + ((command.operation_id,) * 2, ('invented',)):
            with pytest.raises(StorageError):
                qualify(original, selected)
        for fault in ('proof', 'cost', 'member', 'mixed', 'original_hold'):
            changed = copy.deepcopy(original)
            row = changed[0]['settlement_operations'][command.operation_id]
            intent = command.intent_ids[0]
            if fault == 'proof':
                row['proof'] = 'f' * 64
            elif fault == 'cost':
                row['actuals'][intent] = '0'
            elif fault == 'member':
                row['command']['intent_ids'] = [intent]
            elif fault == 'mixed':
                changed[2]['entries'][intent].update(actual='0', inventory_cycle=None)
            else:
                row['committed_before'][intent] = '0'
            with pytest.raises(StorageError):
                qualify(changed)
    finally:
        await store.close()
