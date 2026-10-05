"""P2.11: account state needs explicit identity, quality, and zero evidence."""

import asyncio
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from hummingbot.connector.exchange.okx import okx_constants
from hummingbot.strategy_v2.life_liquidity.account_data import (
    AccountConfigSnapshot,
    AccountDataError,
    AccountSnapshotGate,
    BalanceSnapshot,
    OkxAccountSnapshotSource,
    PositionSnapshot,
    UnifiedAccountSnapshot,
)


def test_spot_account_config_endpoint_is_rate_limited():
    assert any(limit.limit_id == "/api/v5/account/config" and limit.limit == 5
               and limit.time_interval == 2 for limit in okx_constants.RATE_LIMITS)


def config_response(uid="acct-1", mode="2", pos_mode="net_mode"):
    return {"code": "0", "data": [{"uid": uid, "acctLv": mode, "posMode": pos_mode}]}


def balance_response(*, total="1000", adjusted="900", life="5", usdt="100", updated="2000000"):
    return {"code": "0", "data": [{
        "uTime": updated, "totalEq": total, "adjEq": adjusted,
        "details": [
            {"ccy": "LIFE", "eq": life, "uTime": updated},
            {"ccy": "USDT", "eq": usdt, "uTime": updated},
        ],
    }]}


def position_response(*, quantity="0", updated="2000000", side="net"):
    return {"code": "0", "data": [{
        "instId": "LIFE-USDT-SWAP", "instType": "SWAP", "posSide": side,
        "pos": quantity, "uTime": updated,
    }]}


def config(response=None, connector="okx", uid="acct-1"):
    return AccountConfigSnapshot.from_okx(
        response or config_response(), connector_name=connector, expected_account_id=uid,
        observed_exchange_ms=2000001, received_monotonic=10)


def balance(response=None, connector="okx"):
    return BalanceSnapshot.from_okx(
        response or balance_response(), identity=config(connector=connector),
        required_currencies=("LIFE", "USDT"), received_monotonic=10)


def position(response=None):
    return PositionSnapshot.from_okx(
        response or position_response(), identity=config(connector="okx_perpetual"),
        instrument_id="LIFE-USDT-SWAP", received_monotonic=10)


def test_explicit_zero_position_and_balance_carry_freshness_and_quality():
    balances = balance()
    positions = position()
    assert balances.assets["LIFE"] == Decimal("5")
    assert balances.total_equity_usd == Decimal("1000")
    assert balances.collateral_equity_usd == Decimal("900")
    assert positions.signed_contracts == Decimal("0")
    assert balances.quality.source == "okx:balance"
    assert positions.quality.source == "okx_perpetual:positions"
    assert config().quality.timestamp_kind == "OBSERVED"
    assert balances.quality.timestamp_kind == "UPDATE"
    assert balances.quality.is_fresh(exchange_now_ms=2000500, monotonic_now=10.5,
                                     max_exchange_age_ms=1000, max_receipt_age_seconds=2)
    assert not balances.quality.is_fresh(exchange_now_ms=2002000, monotonic_now=10.5,
                                         max_exchange_age_ms=1000, max_receipt_age_seconds=2)
    assert not balances.quality.is_fresh(exchange_now_ms=2000500, monotonic_now=13,
                                         max_exchange_age_ms=1000, max_receipt_age_seconds=2)


def test_missing_currency_and_empty_positions_are_unknown_not_zero():
    missing_life = balance_response()
    missing_life["data"][0]["details"] = missing_life["data"][0]["details"][1:]
    with pytest.raises(AccountDataError, match="BALANCE_CURRENCY_MISSING"):
        balance(missing_life)
    with pytest.raises(AccountDataError, match="POSITION_SNAPSHOT_INCOMPLETE"):
        position({"code": "0", "data": []})


@pytest.mark.parametrize("response,reason", [
    (balance_response(life="NaN"), "BALANCE_AMOUNT_INVALID"),
    (balance_response(updated="0"), "ACCOUNT_TIMESTAMP_INVALID"),
    (balance_response(total=""), "BALANCE_AMOUNT_INVALID"),
])
def test_invalid_balance_cannot_be_promoted_to_account_state(response, reason):
    with pytest.raises(AccountDataError, match=reason):
        balance(response)


def test_config_uid_mismatch_and_position_side_mismatch_fail_closed():
    with pytest.raises(AccountDataError, match="ACCOUNT_ID_MISMATCH"):
        config(config_response(uid="other"))
    with pytest.raises(AccountDataError, match="POSITION_MODE_MISMATCH"):
        position(position_response(side="long"))
    with pytest.raises(AccountDataError, match="POSITION_AMOUNT_INVALID"):
        position(position_response(quantity="NaN"))


def test_same_account_spot_and_perp_equity_is_counted_once():
    spot = balance()
    perp = balance(balance_response(total="1100", adjusted="950", updated="2000001"),
                   connector="okx_perpetual")
    account = UnifiedAccountSnapshot.combine(
        identities=(config(), config(connector="okx_perpetual")),
        balances=(spot, perp), positions=position())
    assert account.account_id == "acct-1"
    assert account.total_equity_usd == Decimal("1100")
    assert account.collateral_equity_usd == Decimal("950")
    assert account.assets["USDT"] == Decimal("100")
    assert account.balance_source == "okx_perpetual"
    same_time_conflict = balance(balance_response(total="999"), connector="okx_perpetual")
    with pytest.raises(AccountDataError, match="BALANCE_SNAPSHOT_CONFLICT"):
        UnifiedAccountSnapshot.combine(
            identities=(config(), config(connector="okx_perpetual")),
            balances=(spot, same_time_conflict), positions=position())


def test_cross_account_connector_snapshots_are_never_combined():
    wrong_account = AccountConfigSnapshot.from_okx(
        config_response(uid="acct-2"), connector_name="okx_perpetual",
        expected_account_id="acct-2", observed_exchange_ms=2000001, received_monotonic=10)
    with pytest.raises(AccountDataError, match="ACCOUNT_ID_MISMATCH"):
        UnifiedAccountSnapshot.combine(
            identities=(config(), wrong_account), balances=(balance(),), positions=None)


def test_conflicting_account_modes_are_not_combined():
    other_mode = config(config_response(mode="3"), connector="okx_perpetual")
    with pytest.raises(AccountDataError, match="ACCOUNT_MODE_MISMATCH"):
        UnifiedAccountSnapshot.combine(
            identities=(config(), other_mode), balances=(balance(),), positions=None)


@pytest.mark.asyncio
async def test_authenticated_source_verifies_both_uids_and_scoped_position_query():
    spot = SimpleNamespace(_api_get=AsyncMock(side_effect=[config_response(), balance_response()]))
    perp = SimpleNamespace(_api_get=AsyncMock(side_effect=[
        config_response(), balance_response(), position_response()]))
    provider = SimpleNamespace(connectors={"okx": spot, "okx_perpetual": perp})
    source = OkxAccountSnapshotSource(provider, expected_account_id="acct-1", include_perpetual=True,
                                      clock=lambda: 10)
    account = await source.fetch(observed_exchange_ms=2000001)
    assert account.total_equity_usd == Decimal("1000")
    assert account.positions.signed_contracts == Decimal("0")
    spot._api_get.assert_any_await(path_url="/api/v5/account/balance",
                                   params={"ccy": "LIFE,USDT"}, is_auth_required=True)
    perp._api_get.assert_any_await(path_url="/api/v5/account/positions",
                                   params={"instType": "SWAP", "instId": "LIFE-USDT-SWAP"},
                                   is_auth_required=True)


@pytest.mark.asyncio
async def test_source_rejects_mismatched_uid_before_reading_balances():
    spot = SimpleNamespace(_api_get=AsyncMock(return_value=config_response()))
    perp = SimpleNamespace(_api_get=AsyncMock(return_value=config_response(uid="other")))
    provider = SimpleNamespace(connectors={"okx": spot, "okx_perpetual": perp})
    source = OkxAccountSnapshotSource(provider, expected_account_id="acct-1", include_perpetual=True,
                                      clock=lambda: 10)
    with pytest.raises(AccountDataError, match="ACCOUNT_ID_MISMATCH"):
        await source.fetch(observed_exchange_ms=2000001)
    spot._api_get.assert_awaited_once()
    perp._api_get.assert_awaited_once()


@pytest.mark.asyncio
async def test_failed_refresh_revokes_previous_snapshot_without_zero_substitution():
    class Source:
        responses = [UnifiedAccountSnapshot.combine(
            identities=(config(),), balances=(balance(),), positions=None),
            AccountDataError("BALANCE_CURRENCY_MISSING")]

        async def fetch(self, **kwargs):
            result = self.responses.pop(0)
            if isinstance(result, Exception):
                raise result
            return result

    gate = AccountSnapshotGate(clock=lambda: 11)
    source = Source()
    assert await gate.refresh(source, observed_exchange_ms=2000001,
                              max_exchange_age_ms=1000, max_receipt_age_seconds=2)
    assert gate.snapshot is not None
    assert gate.permit(exchange_now_ms=2000002, monotonic_now=11,
                       max_exchange_age_ms=1000, max_receipt_age_seconds=2)
    assert not gate.permit(exchange_now_ms=2002000, monotonic_now=11,
                           max_exchange_age_ms=1000, max_receipt_age_seconds=2)
    assert not await gate.refresh(source, observed_exchange_ms=2000002,
                                  max_exchange_age_ms=1000, max_receipt_age_seconds=2)
    assert gate.snapshot is None
    assert gate.reason_code == "BALANCE_CURRENCY_MISSING"


@pytest.mark.asyncio
async def test_gate_rejects_stale_account_observation():
    old_balance = balance(balance_response(updated="1000"))
    account = UnifiedAccountSnapshot.combine(
        identities=(config(),), balances=(old_balance,), positions=None)
    source = SimpleNamespace(fetch=AsyncMock(return_value=account))
    gate = AccountSnapshotGate(clock=lambda: 10)
    assert not await gate.refresh(source, observed_exchange_ms=2000001,
                                  max_exchange_age_ms=1000, max_receipt_age_seconds=2)
    assert gate.snapshot is None
    assert gate.reason_code == "ACCOUNT_SNAPSHOT_STALE"


@pytest.mark.asyncio
async def test_older_refresh_cannot_restore_snapshot_after_newer_failure():
    slow_started, release_slow = asyncio.Event(), asyncio.Event()
    account = UnifiedAccountSnapshot.combine(
        identities=(config(),), balances=(balance(),), positions=None)

    class SlowSource:
        async def fetch(self, **kwargs):
            slow_started.set()
            await release_slow.wait()
            return account

    class FailedSource:
        async def fetch(self, **kwargs):
            raise AccountDataError("BALANCE_CURRENCY_MISSING")

    gate = AccountSnapshotGate(clock=lambda: 11)
    old_refresh = asyncio.create_task(gate.refresh(
        SlowSource(), observed_exchange_ms=2000001,
        max_exchange_age_ms=1000, max_receipt_age_seconds=2))
    await slow_started.wait()
    try:
        assert not await gate.refresh(FailedSource(), observed_exchange_ms=2000002,
                                      max_exchange_age_ms=1000, max_receipt_age_seconds=2)
    finally:
        release_slow.set()
    assert not await old_refresh
    assert gate.snapshot is None
    assert gate.reason_code == "BALANCE_CURRENCY_MISSING"
