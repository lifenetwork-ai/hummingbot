"""P2.7: exact LIFE contract units and rejected unsupported derivatives."""

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call

import pytest

from hummingbot.connector.derivative.okx_perpetual.okx_perpetual_derivative import OkxPerpetualDerivative
from hummingbot.core.data_type.common import OrderType, PositionAction, PositionMode, PositionSide, TradeType
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


@pytest.mark.parametrize("position", [
    {"pos": "1.5", "avgPx": "0", "markPx": "1", "notionalUsd": "0"},
    {"pos": "1.5", "avgPx": "2", "markPx": "3", "notionalUsd": "1.125"},
    {"pos": "-1.5", "avgPx": "2", "markPx": "4", "notionalUsd": "1.5"},
])
def test_linear_position_uses_contract_count_not_notional_or_entry_price(position):
    connector = SimpleNamespace(_contract_sizes={"LIFE-USDT": Decimal("0.25")})
    assert OkxPerpetualDerivative.get_position_amount(
        connector, position, "LIFE-USDT") == Decimal("0.375")


@pytest.mark.parametrize("value", ["NaN", "Infinity", "invalid"])
def test_position_count_must_be_finite(value):
    connector = SimpleNamespace(_contract_sizes={"LIFE-USDT": Decimal("0.25")})
    with pytest.raises(ValueError, match="POSITION_SIZE_INVALID"):
        OkxPerpetualDerivative.get_position_amount(
            connector, {"pos": value, "avgPx": "0"}, "LIFE-USDT")


@pytest.mark.parametrize("contracts,expected", [
    ("0.5", PositionSide.LONG),
    ("-0.5", PositionSide.SHORT),
])
def test_fractional_net_contracts_preserve_position_side(contracts, expected):
    assert OkxPerpetualDerivative.get_position_side(
        {"posSide": "net", "pos": contracts}) == expected


def test_unknown_position_side_is_not_treated_as_short():
    with pytest.raises(ValueError, match="POSITION_SIDE_INVALID"):
        OkxPerpetualDerivative.get_position_side({"posSide": "unknown", "pos": "1"})


@pytest.mark.asyncio
async def test_zero_net_position_event_clears_both_cached_sides():
    positions = MagicMock()
    positions.position_key.side_effect = lambda pair, side: (pair, side)
    connector = SimpleNamespace(
        trading_pair_associated_to_exchange_symbol=AsyncMock(return_value="LIFE-USDT"),
        get_position_side=OkxPerpetualDerivative.get_position_side,
        get_position_amount=lambda *_: Decimal("0"),
        _perpetual_trading=positions,
    )
    await OkxPerpetualDerivative._process_account_position_event(connector, {
        "instId": "LIFE-USDT-SWAP", "posSide": "net", "pos": "0",
        "avgPx": "0", "lever": "1", "upl": "0",
    })
    positions.remove_position.assert_has_calls([
        call(("LIFE-USDT", PositionSide.LONG)),
        call(("LIFE-USDT", PositionSide.SHORT)),
    ], any_order=True)


@pytest.mark.asyncio
async def test_net_position_flip_removes_previous_side():
    positions = MagicMock()
    positions.position_key.side_effect = lambda pair, side: (pair, side)
    connector = SimpleNamespace(
        trading_pair_associated_to_exchange_symbol=AsyncMock(return_value="LIFE-USDT"),
        get_position_side=OkxPerpetualDerivative.get_position_side,
        get_position_amount=lambda *_: Decimal("0.125"),
        _perpetual_trading=positions,
    )
    await OkxPerpetualDerivative._process_account_position_event(connector, {
        "instId": "LIFE-USDT-SWAP", "posSide": "net", "pos": "-0.5",
        "avgPx": "2", "lever": "1", "upl": "0",
    })
    positions.remove_position.assert_called_with(("LIFE-USDT", PositionSide.LONG))
    assert positions.set_position.call_args.args[0] == ("LIFE-USDT", PositionSide.SHORT)


@pytest.mark.asyncio
async def test_rest_position_uses_contract_units_and_removes_stale_side():
    positions = MagicMock()
    positions.position_key.side_effect = lambda pair, side: (pair, side)
    connector = SimpleNamespace(
        _trading_pairs=["LIFE-USDT"],
        _contract_sizes={"LIFE-USDT": Decimal("0.25")},
        exchange_symbol_associated_to_pair=AsyncMock(return_value="LIFE-USDT-SWAP"),
        trading_pair_associated_to_exchange_symbol=AsyncMock(return_value="LIFE-USDT"),
        _api_get=AsyncMock(return_value={"data": [{
            "instId": "LIFE-USDT-SWAP", "posSide": "net", "pos": "-1.5",
            "avgPx": "0", "notionalUsd": "0", "upl": "0", "lever": "1",
        }]}),
        get_position_side=OkxPerpetualDerivative.get_position_side,
        _perpetual_trading=positions,
    )
    connector.get_position_amount = lambda message, pair: (
        OkxPerpetualDerivative.get_position_amount(connector, message, pair))
    await OkxPerpetualDerivative._update_positions(connector)
    positions.remove_position.assert_called_with(("LIFE-USDT", PositionSide.LONG))
    key, position = positions.set_position.call_args.args
    assert key == ("LIFE-USDT", PositionSide.SHORT)
    assert position.amount == Decimal("-0.375")


@pytest.mark.asyncio
async def test_oneway_close_requires_exchange_reduce_only_on_wire():
    connector = SimpleNamespace(
        exchange_symbol_associated_to_pair=AsyncMock(return_value="LIFE-USDT-SWAP"),
        _format_amount_to_size=lambda pair, amount: amount / Decimal("0.25"),
        position_mode=PositionMode.ONEWAY,
        _api_post=AsyncMock(return_value={"data": [{"sCode": "0", "ordId": "42"}]}),
        current_timestamp=1.0,
    )
    await OkxPerpetualDerivative._place_order(
        connector, "wire", "LIFE-USDT", Decimal("0.5"), TradeType.SELL,
        OrderType.LIMIT, Decimal("2"), PositionAction.CLOSE)
    data = connector._api_post.await_args.kwargs["data"]
    assert data["posSide"] == "net"
    assert data["reduceOnly"] is True
    assert data["sz"] == "2"


@pytest.mark.asyncio
async def test_unknown_position_mode_cannot_send_swap_order():
    connector = SimpleNamespace(
        exchange_symbol_associated_to_pair=AsyncMock(return_value="LIFE-USDT-SWAP"),
        _format_amount_to_size=lambda pair, amount: amount / Decimal("0.25"),
        position_mode=None,
        _api_post=AsyncMock(),
    )
    with pytest.raises(ValueError, match="POSITION_MODE_UNAVAILABLE"):
        await OkxPerpetualDerivative._place_order(
            connector, "wire", "LIFE-USDT", Decimal("0.5"), TradeType.SELL,
            OrderType.LIMIT, Decimal("2"), PositionAction.CLOSE)
    connector._api_post.assert_not_awaited()


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
