"""Signed repair decoding keeps financial identities, namespace and units strict."""
from dataclasses import replace
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.test_joint_batch_contracts import command as batch_command
from test.hummingbot.strategy_v2.life_liquidity.storage.test_storage_contracts import NOW, binding

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
from hummingbot.strategy_v2.life_liquidity.storage.postgres.joint_repair import JointRepairFacts, ResolveJointSources
from hummingbot.strategy_v2.life_liquidity.storage.recovery import SpotFinancialRecoveryFacts


def facts():
    account = SpotFinancialRecoveryFacts(binding(), NOW, D('5'), D('10'), D('0'), D('0'), ('trade1',), (), (), (), True, True, True)
    at = int(NOW.timestamp() * 1000)
    return JointRepairFacts(account, (('SWAP', 'trade1'),), ('777',), True, at - 1000, at)


@pytest.mark.parametrize('field,value', [('product_trade_ids', (([], 'trade1'),)), ('product_trade_ids', (('SWAP', []),)),
                                         ('funding_bill_ids', ([],)), ('coverage_from_ms', True), ('coverage_through_ms', 0),
                                         ('swap_scope_complete', 1), ('product_trade_ids', (('SPOT', 'missing'),))])
def test_repair_facts_reject_malformed_or_inconsistent_scope(field, value):
    with pytest.raises(StorageError):
        replace(facts(), **{field: value})


@pytest.mark.parametrize('fault', ['money', 'binding', 'scope_shape', 'extra', 'missing'])
def test_repair_decoder_never_coerces_money_or_guesses_missing_scope(fault):
    data = facts().json()
    if fault == 'money':
        data['account']['account_quote_usdt'] = 10
    elif fault == 'binding':
        data['binding']['config_hash'] = 'c' * 64
    elif fault == 'scope_shape':
        data['product_trade_ids'] = ['ST']
    elif fault == 'extra':
        data['trading_permission'] = True
    else:
        del data['funding_bill_ids']
    with pytest.raises(StorageError):
        JointRepairFacts.from_json(data)


def test_original_units_and_namespaced_trade_id_roundtrip_without_authority():
    assert JointRepairFacts.from_json(facts().json()) == facts()
    command = ResolveJointSources('repair', 'pending', batch_command())
    assert ResolveJointSources.from_json(command.json()) == command
    for operation_id in ('pending', 'batch', 'funding'):
        with pytest.raises(StorageError):
            replace(command, operation_id=operation_id)
