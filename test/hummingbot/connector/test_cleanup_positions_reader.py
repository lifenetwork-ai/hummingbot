from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from hummingbot.connector.credential_adapters import CredentialAdapterError, OKXCredentialAdapter


def reader(data):
    return SimpleNamespace(name="okx_perpetual", _api_get=AsyncMock(return_value={"code": "0", "data": data}),
                           _format_size_to_amount=lambda pair, size: size * Decimal("0.01"))


@pytest.mark.asyncio
async def test_cleanup_reads_actual_sides_separately_including_pairs_outside_current_yaml():
    connector = reader([dict(instId="BTC-USDT-SWAP", posSide="long", pos="3", mgnMode="cross"),
                        dict(instId="BTC-USDT-SWAP", posSide="short", pos="3", mgnMode="cross")])
    adapter = OKXCredentialAdapter(environ={"OKX_TRADING_ENV": "demo"})
    assert await adapter.cleanup_positions(connector) == {("BTC-USDT", "LONG"): Decimal("0.03"), ("BTC-USDT", "SHORT"): Decimal("0.03")}
    assert connector._api_get.await_args.kwargs["params"] == {"instType": "SWAP"}


@pytest.mark.asyncio
@pytest.mark.parametrize("row", [dict(instId="BTC-USDT-SWAP", posSide="net", pos="1", mgnMode="cross"),
                                 dict(instId="BTC-USDT-SWAP", posSide="long", pos="1", mgnMode="isolated"),
                                 dict(instId="BTC-USD-SWAP", posSide="long", pos="1", mgnMode="cross"),
                                 dict(instId="BTC-USDT-SWAP", posSide="long", pos="NaN", mgnMode="cross")])
async def test_unsupported_or_malformed_exposure_cannot_be_treated_as_flat(row):
    adapter = OKXCredentialAdapter(environ={"OKX_TRADING_ENV": "demo"})
    with pytest.raises(CredentialAdapterError):
        await adapter.cleanup_positions(reader([row]))


@pytest.mark.asyncio
async def test_error_response_does_not_reuse_cached_positions():
    connector = reader([])
    connector._api_get.return_value = {"code": "50011", "data": []}
    with pytest.raises(CredentialAdapterError, match="exchange_unavailable"):
        await OKXCredentialAdapter(environ={"OKX_TRADING_ENV": "demo"}).cleanup_positions(connector)
