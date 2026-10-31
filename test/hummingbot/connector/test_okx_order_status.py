"""Only authoritative absence can release the existing lost-order tracking."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from hummingbot.connector.derivative.okx_perpetual.okx_perpetual_derivative import OkxPerpetualDerivative
from hummingbot.connector.exchange.okx.okx_exchange import OkxExchange
from hummingbot.connector.okx_order_status import OKXOrderNotFoundError, order_status_data


@pytest.mark.parametrize("connector", [OkxExchange, OkxPerpetualDerivative])
@pytest.mark.parametrize("response", [
    {"code": "0", "data": []}, {"code": "0", "data": None}, {}, [],
    {"code": "50011", "data": []}, {"code": "50113", "data": []},
    {"code": "51603"}, {"code": "51603", "data": [{"state": "live"}]},
    {"code": "0", "data": [None]},
])
@pytest.mark.asyncio
async def test_ambiguous_response_is_not_order_absence(connector, response):
    source = SimpleNamespace(_request_order_update=AsyncMock(return_value=response))
    with pytest.raises(IOError) as error:
        await connector._request_order_status(source, SimpleNamespace())
    assert not connector._is_order_not_found_during_status_update_error(source, error.value)


@pytest.mark.parametrize("connector", [OkxExchange, OkxPerpetualDerivative])
@pytest.mark.asyncio
async def test_network_timeout_is_not_order_absence(connector):
    source = SimpleNamespace(_request_order_update=AsyncMock(side_effect=asyncio.TimeoutError))
    with pytest.raises(asyncio.TimeoutError) as error:
        await connector._request_order_status(source, SimpleNamespace())
    assert not connector._is_order_not_found_during_status_update_error(source, error.value)


def test_only_explicit_absence_has_the_typed_error():
    with pytest.raises(OKXOrderNotFoundError):
        order_status_data({"code": "51603", "data": []})
    value = {"state": "live", "ordId": "id"}
    assert order_status_data({"code": "0", "data": [value]}) == value


@pytest.mark.parametrize("connector", [OkxExchange, OkxPerpetualDerivative])
def test_status_error_retains_existing_time_sync_detection(connector):
    with pytest.raises(IOError) as error:
        order_status_data({"code": "50113", "msg": "private-message", "data": []})
    assert connector._is_request_exception_related_to_time_synchronizer(None, error.value)
    assert "private-message" not in str(error.value)
