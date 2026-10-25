from decimal import Decimal
from test.hummingbot.cli.test_admin_api import client
from types import SimpleNamespace

import pytest


@pytest.mark.asyncio
async def test_reporting_endpoints_keep_account_cash_separate_and_return_stale_observations(monkeypatch):
    async with client() as (http, storage):
        storage.observations.return_value = {
            "server_time": 1700000000, "owner_available": False,
            "accounts": [{"account_id": "17", "environment": "live", "identity_verified": True, "balances": [
                {"currency": "USDT", "cash_balance": Decimal("100"), "available_cash": Decimal("60"),
                 "equity": Decimal("120"), "available_equity": Decimal("80"), "observed_at": 1699999999,
                 "source": "okx_account_balance", "connector": "okx", "stale": True}]}],
            "positions": [], "performance": []}
        response = await http.get("/api/v1/admin/balances")
        assert response.status == 200
        data = await response.json()
        balance = data["accounts"][0]["balances"][0]
        assert balance["cash_balance"] == "100" and balance["equity"] == "120"
        assert balance["stale"] and balance["observed_at"] == "2023-11-14T22:13:19Z"
        assert len(data["accounts"]) == 1
        assert (await http.get("/api/v1/admin/positions")).status == 200
        assert (await http.get("/api/v1/admin/performance?market=perpetual")).status == 200
        assert (await http.get("/api/v1/admin/performance?market=margin")).status == 422


@pytest.mark.asyncio
async def test_performance_http_round_trips_a_populated_upstream_close_type_report():
    from hummingbot.strategy_v2.models.executors import CloseType
    from hummingbot.strategy_v2.models.executors_info import PerformanceReport
    report = PerformanceReport(close_type_counts={CloseType.EARLY_STOP: 2})
    async with client() as (http, storage):
        storage.observations.return_value = dict(server_time=1700000000, owner_available=True, performance=[dict(
            controller_id="life_spot", market="spot", connector="okx", pair="LIFE-USDT", observed_at=1700000000,
            source="v2_controller_report_cache", valuation_basis="executor_and_position_hold_reports",
            stale=False, report=report.model_dump(mode="json"))])
        response = await http.get("/api/v1/admin/performance")
        assert response.status == 200, await response.text()
        body = await response.json()
        assert body["markets"][0]["observations"][0]["report"]["close_type_counts"] == {str(CloseType.EARLY_STOP.value): 2}


def test_capture_deduplicates_shared_wallet_and_keeps_disabled_market_last_observed():
    from hummingbot.cli.admin_observations import capture_reporting

    def connector(cash):
        return SimpleNamespace(account_uid="17", account_balance_observations={"USDT": {
            "cash_balance": Decimal(cash), "available_cash": Decimal("60"), "equity": Decimal("120"),
            "available_equity": Decimal("80"), "observed_at": 1000, "source": "okx_account_balance"}})
    core = SimpleNamespace(connector_manager=SimpleNamespace(connectors={"okx": connector("100"), "okx_perpetual": connector("100")}), strategy=None)
    previous = {"positions": {"okx_perpetual": {"observed_at": 900, "items": [], "pair_observations": {"LIFE-USDT": 900}}}, "performance": {}}
    snapshot = capture_reporting(core, previous)
    assert len(snapshot["accounts"]) == 1
    assert snapshot["accounts"][0]["balances"][0]["cash_balance"] == "100"
    assert snapshot["positions"] == previous["positions"]
    core.connector_manager.connectors = {}
    stopped = capture_reporting(core, snapshot)
    assert stopped["accounts"] == snapshot["accounts"]
    assert stopped["positions"] == snapshot["positions"]
    assert stopped["active_connectors"] == []


def test_asset_omitted_from_new_account_snapshot_cannot_look_fresh():
    from hummingbot.cli.admin_observations import capture_reporting, reporting_view
    connector = SimpleNamespace(account_uid="17", account_balance_observations={"USDT": dict(
        cash_balance="100", available_cash="80", equity="110", available_equity="90", observed_at=1001, source="okx_account_balance")})
    previous = dict(accounts=[dict(account_id="17", environment="live", identity_verified=True, balances=[dict(
        currency="LIFE", connector="okx", source="okx_account_balance", cash_balance="100", available_cash="80", equity="110",
        available_equity="90", observed_at=1000)])])
    core = SimpleNamespace(connector_manager=SimpleNamespace(connectors={"okx": connector}), strategy=None)
    reporting = capture_reporting(core, previous)
    row = dict(database_now=1002, heartbeat_age_s=0, owner_uuid="owner", status={"running": True, "reporting": reporting})
    view = reporting_view(row)
    life = next(item for item in view["accounts"][0]["balances"] if item["currency"] == "LIFE")
    assert life["stale"]


def test_performance_reads_use_existing_cache_and_keep_separate_market_history_after_switch():
    from hummingbot.cli.admin_observations import capture_reporting, reporting_view
    from hummingbot.strategy_v2.models.executors_info import PerformanceReport
    controllers = {market: SimpleNamespace(config=SimpleNamespace(connector_name=name, trading_pair="LIFE-USDT"),
                                           processed_data={"ready": True}) for market, name in (("spot", "okx"), ("perpetual", "okx_perpetual"))}
    strategy = SimpleNamespace(controllers=controllers, controller_reports_timestamp=1000,
                               controller_reports={"spot": {"performance": PerformanceReport(realized_pnl_quote=Decimal("1"))},
                                                   "perpetual": {"performance": PerformanceReport(realized_pnl_quote=Decimal("2"))}})
    core = SimpleNamespace(strategy=strategy, connector_manager=SimpleNamespace(connectors={}))
    reporting = capture_reporting(core, {})
    strategy.controllers = {"spot": controllers["spot"]}
    reporting = capture_reporting(core, reporting)
    reporting["active_connectors"] = ["okx"]
    row = dict(database_now=1001, owner_uuid="owner", heartbeat_age_s=0, status={"running": True, "reporting": reporting})
    observations = {item["market"]: item for item in reporting_view(row)["performance"]}
    assert observations["spot"]["report"]["realized_pnl_quote"] == "1"
    assert observations["perpetual"]["report"]["realized_pnl_quote"] == "2"
    assert not observations["spot"]["stale"] and observations["perpetual"]["stale"]
    controllers["spot"].processed_data["ready"] = False
    reporting = capture_reporting(core, reporting)
    reporting["active_connectors"] = ["okx"]
    row["status"]["reporting"] = reporting
    assert next(item for item in reporting_view(row)["performance"] if item["market"] == "spot")["stale"]
