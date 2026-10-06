"""The local account lock is bound to the authenticated OKX UID."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from hummingbot.connector.exchange.okx.okx_constants import OKX_ACCOUNT_CONFIG_PATH
from hummingbot.connector.exchange.okx.okx_exchange import OkxExchange


@pytest.mark.asyncio
async def test_account_uid_uses_authenticated_config_endpoint():
    connector = SimpleNamespace(_api_get=AsyncMock(return_value={
        "code": "0", "data": [{"uid": "12345", "mainUid": "67890"}]}))
    assert await OkxExchange.get_account_uid(connector) == "12345"
    connector._api_get.assert_awaited_once_with(
        path_url=OKX_ACCOUNT_CONFIG_PATH, is_auth_required=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [
    None, {"code": "1", "data": [{"uid": "12345"}]},
    {"code": "0", "data": []}, {"code": "0", "data": [{"uid": "bad/uid"}]},
    {"code": "0", "data": [{"uid": "12345"}, {"uid": "67890"}]},
])
async def test_account_uid_rejects_ambiguous_or_invalid_response(response):
    connector = SimpleNamespace(_api_get=AsyncMock(return_value=response))
    with pytest.raises(ValueError, match="ACCOUNT_UID_UNAVAILABLE"):
        await OkxExchange.get_account_uid(connector)


@pytest.mark.asyncio
async def test_account_bills_archive_uses_authenticated_unfiltered_pages():
    connector = SimpleNamespace(_api_get=AsyncMock(return_value={"code": "0", "data": []}))
    await OkxExchange.get_account_bills_page(connector, after="123")
    connector._api_get.assert_awaited_once_with(
        path_url="/api/v5/account/bills-archive",
        params={"limit": "100", "after": "123"}, is_auth_required=True)
