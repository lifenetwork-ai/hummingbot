"""Hedge commands preserve captured JSON identity and strict decimal units."""
from decimal import Decimal

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
from hummingbot.strategy_v2.life_liquidity.storage.postgres.hedge import PlanHedge, SettleHedge, UpdateHedgeTarget


@pytest.mark.parametrize('body', [dict(operation_id='u', expected_version=True, target_base='1'),
                                  dict(operation_id='u', expected_version=-1, target_base='1'),
                                  dict(operation_id='u', expected_version=1, target_base=1),
                                  dict(operation_id='u', expected_version=1, target_base='NaN'),
                                  dict(operation_id='u', expected_version=1, target_base='-1'),
                                  dict(operation_id='u', expected_version=1, target_base='1', extra=0)])
def test_target_json_denies_coercion_nonfinite_and_extra_fields(body):
    with pytest.raises(StorageError):
        UpdateHedgeTarget.from_json(body)


def test_target_and_settlement_roundtrip_keep_exact_operation_and_version():
    target = UpdateHedgeTarget('target', 7, Decimal('.12345678901234567890123456789'))
    assert UpdateHedgeTarget.from_json(target.json()) == target
    settlement = SettleHedge('settle', 'plan')
    assert SettleHedge.from_json(settlement.json()) == settlement
    with pytest.raises(StorageError):
        SettleHedge.from_json({**settlement.json(), 'terminal': True})
    with pytest.raises(StorageError):
        SettleHedge('same', 'same')


@pytest.mark.parametrize('body', [None, {}, dict(operation_id='p', market={}, child=None),
                                  dict(operation_id='p', market={}, child=None, trading_permission=True)])
def test_plan_json_requires_complete_canonical_captured_market(body):
    with pytest.raises(StorageError):
        PlanHedge.from_json(body)
