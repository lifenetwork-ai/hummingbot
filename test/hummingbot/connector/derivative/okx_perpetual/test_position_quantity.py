"""Position sizing must use contracts, independently of valuation and entry price."""
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from bidict import bidict

from hummingbot.connector.derivative.okx_perpetual.okx_perpetual_derivative import OkxPerpetualDerivative
from hummingbot.core.data_type.common import PositionSide


@pytest_asyncio.fixture
async def exchange():
    connector = OkxPerpetualDerivative(trading_pairs=["LIFE-USDT"], trading_required=False)
    connector._set_trading_pair_symbol_map(bidict({"LIFE-USDT-SWAP": "LIFE-USDT"}))
    connector._contract_sizes["LIFE-USDT"] = Decimal("0.1")
    connector.trading_pair_associated_to_exchange_symbol = AsyncMock(return_value="LIFE-USDT")
    connector.exchange_symbol_associated_to_pair = AsyncMock(return_value="LIFE-USDT-SWAP")
    return connector


def position():
    return dict(instId="LIFE-USDT-SWAP", posSide="long", pos="6", avgPx="100",
                notionalUsd="60", lever="2", upl="0")


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["rest", "ws"])
@pytest.mark.parametrize("entry_price,notional", [("100", "60"), ("0", "60"), ("", ""), ("110", "900")])
async def test_contract_quantity_is_independent_of_entry_price_and_notional(exchange, transport, entry_price, notional):
    message = position()
    message.update(avgPx=entry_price, notionalUsd=notional)
    if transport == "rest":
        exchange._api_get = AsyncMock(return_value={"data": [message]})
        await exchange._update_positions()
    else:
        await exchange._process_account_position_event(message)
    actual = next(iter(exchange.account_positions.values()))
    assert actual.amount == Decimal("0.6")
    assert actual.entry_price == Decimal(entry_price or "0")


@pytest.mark.asyncio
@pytest.mark.parametrize("contracts,side,amount", [("0.5", "long", "0.05"), ("-0.5", "short", "-0.05")])
async def test_fractional_net_contracts(exchange, contracts, side, amount):
    message = position()
    message.update(posSide="net", pos=contracts)
    await exchange._process_account_position_event(message)
    actual = next(iter(exchange.account_positions.values()))
    assert actual.position_side == (PositionSide.LONG if side == "long" else PositionSide.SHORT)
    assert actual.amount == Decimal(amount)


@pytest.mark.asyncio
async def test_zero_contracts_remove_position_even_with_stale_notional(exchange):
    message = position()
    await exchange._process_account_position_event(message)
    message.update(pos="0")
    await exchange._process_account_position_event(message)
    assert exchange.account_positions == {}


@pytest.mark.asyncio
async def test_missing_contract_metadata_does_not_guess_from_notional(exchange):
    exchange._contract_sizes.clear()
    with pytest.raises(ValueError, match="contract size"):
        await exchange._process_account_position_event(position())
    assert exchange.account_positions == {}


@pytest.mark.asyncio
async def test_successful_empty_rest_snapshot_clears_closed_positions(exchange):
    await exchange._process_account_position_event(position())
    assert exchange.account_positions
    exchange._api_get = AsyncMock(return_value={"code": "0", "data": []})
    await exchange._update_positions()
    assert exchange.account_positions == {}, "Closed positions cannot remain visible after a successful empty snapshot"


@pytest.mark.asyncio
async def test_failed_rest_snapshot_retains_position_and_last_observation(exchange):
    await exchange._process_account_position_event(position())
    original = dict(exchange.account_positions)
    exchange._api_get = AsyncMock(return_value={"code": "1", "msg": "temporarily unavailable", "data": []})
    await exchange._update_positions()
    assert exchange.account_positions == original


@pytest.mark.asyncio
async def test_rest_snapshot_cannot_erase_a_newer_websocket_position(exchange):
    async def read_before_ws_event(**kwargs):
        await exchange._process_account_position_event(position())
        return {"code": "0", "data": []}
    exchange._api_get = AsyncMock(side_effect=read_before_ws_event)
    await exchange._update_positions()
    assert len(exchange.account_positions) == 1
    assert next(iter(exchange.account_positions.values())).amount == Decimal("0.6")
