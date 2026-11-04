from unittest.mock import AsyncMock

import pytest

from hummingbot.connector.credential_adapters import CredentialAdapterError, CredentialAdapters, OKXCredentialAdapter

VALUES = dict(api_key="CANARY-API", secret_key="CANARY-SECRET", passphrase="CANARY-PASSPHRASE")


def adapter():
    return OKXCredentialAdapter(environ={"OKX_TRADING_ENV": "demo", "OKX_SITE": "www"})


@pytest.mark.parametrize("connector", ["okx", "okx_perpetual"])
def test_input_only_accepts_secrets_and_reuses_existing_connector_models(connector):
    service = adapter()
    config = service.config(connector, VALUES)
    assert getattr(config, connector + "_api_key").get_secret_value() == VALUES["api_key"]
    with pytest.raises(CredentialAdapterError) as error:
        service.config(connector, {**VALUES, "okx_registration_sub_domain": "app"})
    assert error.value.code == "validation_failed" and "CANARY" not in str(error.value)
    assert "CANARY" not in str(service.descriptor())


@pytest.mark.asyncio
async def test_preflight_uses_fresh_uid_read_only_and_checks_each_perpetual_side_mode():
    service = adapter()
    reader = AsyncMock()
    reader._api_get.return_value = {"code": "0", "data": [dict(uid="sub-uid", mainUid="parent-uid", perm="read_only,trade",
                                                               acctLv="2", posMode="long_short_mode")]}
    service.reader = lambda *args: reader
    identity = await service.preflight("okx_perpetual", VALUES)
    assert identity == "www:sub-uid"
    assert reader._api_get.await_args.kwargs == dict(path_url="/api/v5/account/config", is_auth_required=True, return_err=True)
    reader._api_get.return_value["data"][0]["posMode"] = "net_mode"
    with pytest.raises(CredentialAdapterError) as error:
        await service.preflight("okx_perpetual", VALUES)
    assert error.value.code == "unsupported_cleanup_mode"


@pytest.mark.asyncio
@pytest.mark.parametrize("response,code", [
    ({"code": "50119", "msg": "CANARY-PRIVATE-ERROR"}, "exchange_credentials_rejected"),
    ({"code": "50011", "msg": "CANARY-PRIVATE-ERROR"}, "exchange_unavailable"),
    ({"code": "0", "data": [dict(uid="uid", perm="read_only")]}, "exchange_permissions_required"),
    ({"code": "0", "data": [dict(uid="uid", perm="read_only,trade,withdraw")]}, "exchange_permissions_required"),
    ({"code": "0", "data": [dict(mainUid="parent", perm="read_only,trade")]}, "exchange_identity_unavailable"),
])
async def test_rejected_or_unknown_candidate_never_reports_connected(response, code):
    service = adapter()
    reader = AsyncMock()
    reader._api_get.return_value = response
    service.reader = lambda *args: reader
    with pytest.raises(CredentialAdapterError) as error:
        await service.preflight("okx", VALUES)
    assert error.value.code == code and "CANARY" not in str(error.value)


def test_registry_is_exchange_neutral_and_only_enables_deployed_connectors():
    class OtherAdapter:
        exchange = "other"
        connectors = ("other_spot",)

    registry = CredentialAdapters([adapter(), OtherAdapter()], enabled_connectors={"okx", "other_spot"})
    assert registry.get("other", "other_spot").exchange == "other"
    for exchange, connector in (("okx", "okx_perpetual"), ("other", "okx"), ("unknown", "okx")):
        with pytest.raises(CredentialAdapterError):
            registry.get(exchange, connector)
