"""Terminal proof values retain original units and never grant capital release."""
from decimal import Decimal as D, localcontext

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
from hummingbot.strategy_v2.life_liquidity.storage.postgres.swap_terminal import (
    ConfirmSwapTerminal,
    PgSwapTerminalRepository,
)


def command():
    return ConfirmSwapTerminal('terminal1', 'prepare1', '12345', 'canceled', D('0.123456789012345678'),
                               ('trade1',), 1000, 900, 'okx_joint_reconciled')


def test_original_terminal_encoding_does_not_round_quantities_in_a_caller_context():
    value = command()
    with localcontext() as context:
        context.prec = 6
        assert ConfirmSwapTerminal.from_json(value.json()) == value
        assert value.json()['cumulative_base_life'] == '0.123456789012345678'
        assert context.prec == 6


@pytest.mark.parametrize('field,value', [('product', 'SPOT'), ('observed_at_ms', True), ('coverage_from_ms', -1),
                                         ('cumulative_base_life', 1), ('cumulative_base_life', 'NaN'), ('trade_ids', 'trade1'),
                                         ('source', True)])
def test_unsupported_terminal_types_do_not_coerce_money_product_or_source(field, value):
    raw = command().json()
    raw[field] = value
    with pytest.raises(StorageError):
        ConfirmSwapTerminal.from_json(raw)


def test_terminal_receipt_can_never_advertise_release_or_send_authority():
    repo = object.__new__(PgSwapTerminalRepository)
    repo._original = lambda: None  # Actual repository context fencing is native-tested.
    repo.binding = None
    value = dict(kind='SWAP_TERMINAL', operation_id='terminal1', intent_id='swap1', state='TERMINAL',
                 claim_released=False, replayed=False, trading_permission=False)
    parsed = repo._receipt(value)
    assert not parsed.claim_released and not parsed.trading_permission
    for flag in ('claim_released', 'trading_permission'):
        with pytest.raises(StorageError, match='PG_SWAP_TERMINAL_RECEIPT_INVALID'):
            repo._receipt({**value, flag: True})
