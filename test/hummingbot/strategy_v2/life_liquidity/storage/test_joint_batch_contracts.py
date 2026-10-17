"""Strict immutable joint command decoding; no provider or send authority."""
from dataclasses import replace
from test.hummingbot.strategy_v2.life_liquidity.storage.test_funding_contracts import command as funding_command

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
from hummingbot.strategy_v2.life_liquidity.storage.postgres.joint_batch import ApplyJointBatch


def command():
    return ApplyJointBatch('batch', (), (), (), funding_command())


def test_original_funding_bundle_roundtrips_without_intermediate_accounts():
    original = command()
    assert ApplyJointBatch.from_json(original.json()) == original
    assert original.json()['funding'] == original.funding.json()


@pytest.mark.parametrize('field,value', [('spot_fills', []), ('swap_fills', []), ('cashflows', []),
                                         ('funding', None), ('operation_id', 'funding')])
def test_joint_command_rejects_mutable_or_conflicting_members(field, value):
    with pytest.raises(StorageError):
        replace(command(), **{field: value})


@pytest.mark.parametrize('fault', ['missing', 'extra', 'null', 'not_array', 'money'])
def test_joint_decoder_does_not_guess_scope_or_coerce_account_money(fault):
    data = command().json()
    if fault == 'missing':
        del data['funding']
    elif fault == 'extra':
        data['permission'] = True
    elif fault == 'null':
        data['cashflows'] = None
    elif fault == 'not_array':
        data['spot_fills'] = {}
    else:
        data['funding']['bundle']['account']['snapshot']['spot_cash_quote'] = 1
    with pytest.raises(StorageError):
        ApplyJointBatch.from_json(data)
