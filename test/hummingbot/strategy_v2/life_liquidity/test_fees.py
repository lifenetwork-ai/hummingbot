"""Account-bound OKX fee rates and actual fill fees remain separate."""

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from hummingbot.connector.derivative.okx_perpetual import okx_perpetual_web_utils
from hummingbot.connector.exchange.okx import okx_constants
from hummingbot.strategy_v2.life_liquidity.fees import (
    ActualFillFee,
    FeeDataError,
    FeeRateSnapshot,
    FeeReconciler,
    OkxFeeRateSource,
)

SPOT_INSTRUMENT = {"instType": "SPOT", "instId": "LIFE-USDT", "groupId": "1"}
SWAP_INSTRUMENT = {"instType": "SWAP", "instId": "LIFE-USDT-SWAP", "groupId": "2"}


def test_fee_endpoint_is_registered_with_both_okx_rate_limiters():
    assert any(limit.limit_id == "/api/v5/account/trade-fee" and limit.limit == 5
               and limit.time_interval == 2 for limit in okx_constants.RATE_LIMITS)
    swap_limit_id = "GET-/api/v5/account/trade-fee"
    assert any(limit.limit_id == swap_limit_id and limit.limit == 5
               and limit.time_interval == 2 for limit in okx_perpetual_web_utils.build_rate_limits())


def fee_response(*, inst_type="SPOT", group_id="1", maker="-0.0008", taker="-0.001", ts="2000000"):
    return {"code": "0", "data": [{
        "instType": inst_type, "ts": ts,
        "maker": "0.01", "taker": "0.01",  # Deprecated fields must not override feeGroup.
        "feeGroup": [{"groupId": group_id, "maker": maker, "taker": taker}],
    }]}


def snapshot(response=None, **kwargs):
    return FeeRateSnapshot.from_okx(
        response or fee_response(), account_id="subaccount-1", connector_name="okx",
        instrument=SPOT_INSTRUMENT, notional_currency="USDT", **kwargs)


def test_fee_group_rates_normalize_commission_and_do_not_credit_unconfirmed_rebate():
    fees = snapshot(fee_response(maker="0.0002", taker="-0.001"))
    assert fees.account_id == "subaccount-1"
    assert fees.instrument_id == "LIFE-USDT"
    assert fees.group_id == "1"
    assert fees.notional_currency == "USDT"
    assert fees.fee_currency is None  # Fee-rate endpoint does not return this.
    assert fees.maker_cost_rate == Decimal("-0.0002")
    assert fees.taker_cost_rate == Decimal("0.001")
    assert fees.conservative_cost_rate("maker") == Decimal("0")
    assert fees.conservative_cost_rate("taker") == Decimal("0.001")


@pytest.mark.parametrize("response,reason", [
    (fee_response(group_id="3"), "FEE_GROUP_MISMATCH"),
    ({"code": "0", "data": [{"instType": "SPOT", "ts": "2000000",
                            "maker": "-0.001", "taker": "-0.001"}]}, "FEE_GROUP_MISSING"),
    (fee_response(maker="NaN"), "FEE_RATE_INVALID"),
    (fee_response(inst_type="SWAP"), "FEE_INSTRUMENT_MISMATCH"),
])
def test_fee_rate_rejects_wrong_group_legacy_only_nonfinite_and_wrong_product(response, reason):
    with pytest.raises(FeeDataError, match=reason):
        snapshot(response)


def test_fee_snapshot_rejects_stale_and_future_exchange_timestamps():
    fees = snapshot()
    assert fees.is_fresh(exchange_now_ms=2000500, max_age_ms=1000)
    assert not fees.is_fresh(exchange_now_ms=2002000, max_age_ms=1000)
    assert not fees.is_fresh(exchange_now_ms=1999999, max_age_ms=1000)
    with pytest.raises(FeeDataError, match="FEE_TIMESTAMP_INVALID"):
        snapshot(fee_response(ts="0"))


def test_fee_rate_rejects_wrong_notional_currency_and_duplicate_group():
    with pytest.raises(FeeDataError, match="FEE_CURRENCY_MISMATCH"):
        FeeRateSnapshot.from_okx(fee_response(), account_id="acct", connector_name="okx",
                                 instrument=SPOT_INSTRUMENT, notional_currency="BTC")
    response = fee_response()
    response["data"][0]["feeGroup"].append(response["data"][0]["feeGroup"][0].copy())
    with pytest.raises(FeeDataError, match="FEE_GROUP_MISMATCH"):
        snapshot(response)


@pytest.mark.asyncio
async def test_authenticated_fee_source_requests_exact_instrument_for_incentive_rates():
    connector = SimpleNamespace(_api_get=AsyncMock(return_value=fee_response()))
    provider = SimpleNamespace(connectors={"okx": connector})
    source = OkxFeeRateSource(provider, "okx", "subaccount-1", SPOT_INSTRUMENT, "USDT")
    fees = await source.fetch()
    connector._api_get.assert_awaited_once_with(
        path_url="/api/v5/account/trade-fee", params={"instType": "SPOT", "instId": "LIFE-USDT"},
        is_auth_required=True)
    assert fees.group_id == "1"

    swap_connector = SimpleNamespace(_api_get=AsyncMock(return_value=fee_response(inst_type="SWAP", group_id="2")))
    provider.connectors["okx_perpetual"] = swap_connector
    swap_source = OkxFeeRateSource(provider, "okx_perpetual", "subaccount-1", SWAP_INSTRUMENT, "USDT")
    swap_fees = await swap_source.fetch()
    swap_connector._api_get.assert_awaited_once_with(
        path_url="/api/v5/account/trade-fee", params={"instType": "SWAP", "instFamily": "LIFE-USDT"},
        is_auth_required=True)
    assert swap_fees.instrument_id == "LIFE-USDT-SWAP"


@pytest.mark.asyncio
async def test_fee_source_requires_registered_authenticated_connector_and_group_id():
    no_group = {"instType": "SPOT", "instId": "LIFE-USDT"}
    with pytest.raises(FeeDataError, match="FEE_GROUP_MISSING"):
        OkxFeeRateSource(SimpleNamespace(connectors={}), "okx", "acct", no_group, "USDT")
    source = OkxFeeRateSource(SimpleNamespace(connectors={}), "okx", "acct", SPOT_INSTRUMENT, "USDT")
    with pytest.raises(FeeDataError, match="FEE_CONNECTOR_UNAVAILABLE"):
        await source.fetch()


def fill(*, trade_id="fill-1", fee="-0.02", currency="USDT"):
    return {"instId": "LIFE-USDT", "tradeId": trade_id, "fee": fee,
            "feeCcy": currency, "fillTime": "2000000", "ts": "2000001"}


def actual(raw):
    return ActualFillFee.from_okx(raw, account_id="subaccount-1", inst_type="SPOT",
                                  instrument_id="LIFE-USDT")


def test_actual_fees_reconcile_per_fill_and_currency_without_double_counting():
    ledger = FeeReconciler()
    charged = actual(fill())
    first = ledger.record(charged, estimated_cost=Decimal("0.015"), estimated_currency="USDT")
    assert charged.cost_amount == Decimal("0.02")
    assert first.variance == Decimal("0.005")
    assert ledger.record(charged, estimated_cost=Decimal("0.015"), estimated_currency="USDT") == first
    rebate = actual(fill(trade_id="fill-2", fee="0.005"))
    second = ledger.record(rebate, estimated_cost=Decimal("0"), estimated_currency="USDT")
    assert second.variance == Decimal("-0.005")
    assert ledger.actual_costs_by_currency() == {"USDT": Decimal("0.015")}


def test_fee_reconciliation_keeps_other_currency_unconverted_and_rejects_conflicting_fill():
    ledger = FeeReconciler()
    charged_in_life = actual(fill(currency="LIFE", fee="-2"))
    result = ledger.record(charged_in_life, estimated_cost=Decimal("0.01"), estimated_currency="USDT")
    assert result.variance is None
    assert result.reason_code == "FEE_CURRENCY_CONVERSION_REQUIRED"
    assert ledger.actual_costs_by_currency() == {"LIFE": Decimal("2")}
    with pytest.raises(FeeDataError, match="FEE_FILL_CONFLICT"):
        ledger.record(actual(fill(currency="LIFE", fee="-3")), estimated_cost=Decimal("0.01"),
                      estimated_currency="USDT")


def test_same_trade_id_on_different_accounts_is_not_collapsed():
    ledger = FeeReconciler()
    first = actual(fill(fee="-0.02"))
    second = ActualFillFee.from_okx(fill(fee="-0.03"), account_id="subaccount-2",
                                    inst_type="SPOT", instrument_id="LIFE-USDT")
    ledger.record(first, estimated_cost=Decimal("0"), estimated_currency="USDT")
    ledger.record(second, estimated_cost=Decimal("0"), estimated_currency="USDT")
    assert ledger.actual_costs_by_currency() == {"USDT": Decimal("0.05")}


@pytest.mark.parametrize("raw,reason", [
    (fill(fee="NaN"), "FEE_AMOUNT_INVALID"),
    (fill(currency=""), "FEE_CURRENCY_INVALID"),
    ({**fill(), "instId": "OTHER-USDT"}, "FEE_INSTRUMENT_MISMATCH"),
    ({**fill(), "fillTime": ""}, "FEE_TIMESTAMP_INVALID"),
])
def test_actual_fill_rejects_missing_or_ambiguous_fee_data(raw, reason):
    with pytest.raises(FeeDataError, match=reason):
        actual(raw)
