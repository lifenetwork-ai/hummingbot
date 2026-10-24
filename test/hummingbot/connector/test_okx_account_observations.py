from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from hummingbot.connector.derivative.okx_perpetual.okx_perpetual_derivative import OkxPerpetualDerivative
from hummingbot.connector.exchange.okx.okx_exchange import OkxExchange


@pytest_asyncio.fixture(params=["spot", "perpetual"])
async def connector(request):
    if request.param == "spot":
        return OkxExchange("", "", "", trading_pairs=[], trading_required=False)
    return OkxPerpetualDerivative(trading_pairs=[], trading_required=False)


def detail(currency="USDT"):
    return dict(ccy=currency, cashBal="100", availBal="60", frozenBal="40", eq="120", availEq="80", uTime="1700000000000")


@pytest.mark.asyncio
async def test_observation_preserves_cash_separately_from_engine_equity(connector):
    connector._update_balance_from_details(detail())
    assert connector.get_balance("USDT") == Decimal("120")
    observation = getattr(connector, "account_balance_observations", {}).get("USDT")
    assert observation is not None, "Balance API needs raw wallet cash; engine balances contain equity"
    assert observation["cash_balance"] == Decimal("100")
    assert observation["available_cash"] == Decimal("60")
    assert observation["equity"] == Decimal("120")
    assert observation["available_equity"] == Decimal("80")
    assert observation["observed_at"] > 0


@pytest.mark.asyncio
async def test_absent_cash_is_unavailable_and_ws_update_keeps_other_assets(connector):
    connector._update_balance_from_details(detail("LIFE"))
    update = detail()
    update.pop("cashBal")
    connector._update_balance_from_details(update)
    observations = getattr(connector, "account_balance_observations", {})
    assert set(observations) == {"LIFE", "USDT"}
    assert observations["USDT"]["cash_balance"] is None


@pytest.mark.asyncio
async def test_full_balance_snapshot_removes_absent_assets_and_perpetual_fetches_life(connector):
    connector._update_balance_from_details(detail("LIFE"))
    response = dict(code="0", data=[dict(details=[detail()])])
    connector._api_get = connector._api_request = AsyncMock(return_value=response)
    await connector._update_balances()
    assert set(getattr(connector, "account_balance_observations", {})) == {"USDT"}
    # The unified account endpoint must not restrict the perpetual UI to collateral.
    assert "ccy" not in (connector._api_get.call_args.kwargs.get("params") or {})


@pytest.mark.asyncio
async def test_private_account_identity_is_cached_without_exposing_credentials(connector):
    from hummingbot.connector.okx_account_observations import observe_account_identity
    connector._api_get = AsyncMock(return_value={"code": "0", "data": [{"uid": "account-17", "mainUid": "parent-1", "label": "private"}]})
    await observe_account_identity(connector)
    await observe_account_identity(connector)
    assert getattr(connector, "account_uid", None) == "account-17"
    connector._api_get.assert_awaited_once()
    assert connector._api_get.call_args.kwargs["is_auth_required"]
