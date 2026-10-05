"""P2.7: exact LIFE contract units and rejected unsupported derivatives."""

from decimal import Decimal
from types import SimpleNamespace

import pytest

from hummingbot.connector.derivative.okx_perpetual.okx_perpetual_derivative import OkxPerpetualDerivative
from hummingbot.strategy_v2.life_liquidity.market_data import ContractValidationError, LinearSwapContract


def _life_swap(**changes):
    instrument = {
        "instType": "SWAP", "instId": "LIFE-USDT-SWAP", "state": "live",
        "ctType": "linear", "ctVal": "0.25", "ctMult": "1",
        "ctValCcy": "LIFE", "settleCcy": "USDT",
        "tickSz": "0.0001", "lotSz": "0.1", "minSz": "1",
    }
    instrument.update(changes)
    return instrument


def test_linear_swap_contracts_convert_exactly_to_life_and_match_connector():
    contract = LinearSwapContract.from_okx(_life_swap(), "LIFE-USDT")
    assert contract.contract_value_life == Decimal("0.25")
    assert contract.contracts_to_life(Decimal("4")) == Decimal("1.00")
    assert contract.contracts_to_life(Decimal("-4")) == Decimal("-1.00")
    assert contract.life_to_contracts(Decimal("1")) == Decimal("4")
    assert contract.minimum_order_life == Decimal("0.25")
    assert contract.order_step_life == Decimal("0.025")
    assert contract.valid_order_contracts(Decimal("1.1"))
    assert not contract.valid_order_contracts(Decimal("0.9"))
    assert not contract.valid_order_contracts(Decimal("1.05"))
    connector = SimpleNamespace(_contract_sizes={"LIFE-USDT": Decimal("0.25")})
    assert OkxPerpetualDerivative._format_size_to_amount(connector, "LIFE-USDT", Decimal("4")) == \
        contract.contracts_to_life(Decimal("4"))
    assert OkxPerpetualDerivative._format_amount_to_size(connector, "LIFE-USDT", Decimal("1")) == \
        contract.life_to_contracts(Decimal("1"))


@pytest.mark.parametrize("change,reason", [
    ({"instType": "FUTURES", "instId": "LIFE-USDT-261225"}, "SWAP_INSTRUMENT_INVALID"),
    ({"instType": "FUTURES"}, "SWAP_INSTRUMENT_INVALID"),
    ({"instId": "LIFE-USDT-261225"}, "SWAP_INSTRUMENT_INVALID"),
    ({"ctType": "inverse"}, "SWAP_CONTRACT_TYPE_INVALID"),
    ({"ctValCcy": "USDT"}, "SWAP_CONTRACT_UNIT_INVALID"),
    ({"settleCcy": "LIFE"}, "SWAP_CONTRACT_UNIT_INVALID"),
    ({"ctVal": "0"}, "SWAP_CONTRACT_VALUE_INVALID"),
    ({"ctVal": "NaN"}, "SWAP_CONTRACT_VALUE_INVALID"),
    ({"ctVal": "inf"}, "SWAP_CONTRACT_VALUE_INVALID"),
    ({"ctMult": "2"}, "SWAP_CONTRACT_MULTIPLIER_UNSUPPORTED"),
    ({"ctMult": ""}, "SWAP_CONTRACT_MULTIPLIER_UNSUPPORTED"),
    ({"lotSz": "0"}, "SWAP_ORDER_RULES_INVALID"),
])
def test_invalid_or_unsupported_contract_metadata_fails_closed(change, reason):
    with pytest.raises(ContractValidationError) as exc:
        LinearSwapContract.from_okx(_life_swap(**change), "LIFE-USDT")
    assert exc.value.reason_code == reason


@pytest.mark.parametrize("value", [Decimal("NaN"), Decimal("Infinity"), 0.1, "1"])
def test_conversion_rejects_non_decimal_or_nonfinite_quantity(value):
    contract = LinearSwapContract.from_okx(_life_swap(), "LIFE-USDT")
    with pytest.raises(ValueError):
        contract.contracts_to_life(value)


def test_reverse_conversion_rejects_an_inexact_contract_count():
    contract = LinearSwapContract.from_okx(_life_swap(ctVal="0.3"), "LIFE-USDT")
    with pytest.raises(ValueError, match="no exact decimal"):
        contract.life_to_contracts(Decimal("1"))
