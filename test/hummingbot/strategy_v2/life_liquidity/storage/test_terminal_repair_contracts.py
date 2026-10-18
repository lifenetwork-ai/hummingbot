"""Bounded canonical terminal-resolution DTOs; no backend or exchange required."""
from dataclasses import replace
from decimal import Decimal as D

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
from hummingbot.strategy_v2.life_liquidity.storage.postgres.swap_terminal import ConfirmSwapTerminal
from hummingbot.strategy_v2.life_liquidity.storage.postgres.terminal_repair import ResolveSwapTerminals


def command():
    return ResolveSwapTerminals('root', 'pending', (ConfirmSwapTerminal('fresh', 'prepare', '123', 'canceled', D('0'), (), 2, 1, 'okx_joint_reconciled'),), ('z', 'a'))


def test_terminal_repair_roundtrip_canonical_members_and_no_implicit_empty_profile():
    c = command()
    assert c.additional_pending_operation_ids == ('a', 'z')
    assert ResolveSwapTerminals.from_json(c.json()) == c
    assert ResolveSwapTerminals('root', 'pending', c.terminals).json()['additional_pending_operation_ids'] == []


@pytest.mark.parametrize('fault', ['extra', 'missing', 'null', 'list', 'duplicate', 'overlap', 'empty', 'bound', 'same-order'])
def test_terminal_repair_rejects_ambiguous_or_unbounded_identity(fault):
    c = command()
    with pytest.raises(StorageError):
        if fault in ('extra', 'missing', 'null'):
            value = c.json()
            if fault == 'extra':
                value['trading_permission'] = True
            elif fault == 'missing':
                del value['terminals']
            else:
                value['additional_pending_operation_ids'] = None
            ResolveSwapTerminals.from_json(value)
        elif fault == 'list':
            replace(c, additional_pending_operation_ids=[])
        elif fault == 'duplicate':
            replace(c, additional_pending_operation_ids=('x', 'x'))
        elif fault == 'overlap':
            replace(c, additional_pending_operation_ids=('fresh',))
        elif fault == 'empty':
            replace(c, terminals=())
        elif fault == 'bound':
            replace(c, additional_pending_operation_ids=tuple(f'm{i}' for i in range(256)))
        else:
            replace(c, terminals=(*c.terminals, replace(c.terminals[0], operation_id='second')))
