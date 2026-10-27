"""Read-only projections of connector observations and existing V2 report caches."""
import copy
import json

# Preserve the existing import while storage uses the neutral projection directly.
from hummingbot.core.reporting import reporting_view  # noqa: F401


def capture_reporting(core, previous):
    result = copy.deepcopy(previous or {})
    result.setdefault("accounts", [])
    result.setdefault("positions", {})
    result.setdefault("performance", {})
    connectors = core.connector_manager.connectors
    result["active_connectors"] = sorted(connectors)
    result["ready_controller_ids"] = []
    accounts = {(row["account_id"], row["environment"]): row for row in result["accounts"]}
    for name, connector in connectors.items():
        if name not in ("okx", "okx_perpetual"):
            continue
        uid = getattr(connector, "account_uid", None)
        environment = getattr(connector, "trading_environment", "live")
        observations = getattr(connector, "account_balance_observations", {})
        if isinstance(observations, dict):
            for account in accounts.values():
                for balance in account["balances"]:
                    if balance["connector"] == name and balance["currency"] not in observations:
                        balance["present_in_latest_snapshot"] = False
        if isinstance(observations, dict) and observations:
            if uid is not None and (None, environment) in accounts:
                unverified = accounts[(None, environment)]
                unverified["balances"] = [row for row in unverified["balances"] if row["connector"] != name]
                if not unverified["balances"]:
                    del accounts[(None, environment)]
            key = (uid, environment)
            account = accounts.setdefault(key, dict(account_id=uid, environment=environment,
                                                    identity_verified=uid is not None, balances=[]))
            balances = {row["currency"]: row for row in account["balances"]}
            for currency, fields in observations.items():
                if currency not in balances or fields["observed_at"] >= balances[currency]["observed_at"]:
                    balances[currency] = dict(currency=currency, connector=name, present_in_latest_snapshot=True, **fields)
            account["balances"] = [balances[key] for key in sorted(balances)]
        times = getattr(connector, "position_snapshot_times", {})
        if name == "okx_perpetual" and isinstance(times, dict) and times:
            raw = getattr(connector, "position_observations", {})
            items = []
            for key, position in connector.account_positions.items():
                fields = raw.get(key, {})
                items.append(dict(pair=position.trading_pair, side=position.position_side.name,
                                  amount=position.amount, entry_price=position.entry_price,
                                  unrealized_pnl=position.unrealized_pnl, leverage=position.leverage, **fields))
            result["positions"][name] = dict(pair_observations=times, items=items,
                                             observed_at=max(times.values()), source="okx_account_positions")
    result["accounts"] = list(accounts.values())
    strategy = core.strategy
    if strategy is not None:
        timestamp = getattr(strategy, "controller_reports_timestamp", None)
        if isinstance(timestamp, (float, int)) and not isinstance(getattr(strategy, "controller_reports_error", None), str):
            for controller_id, controller in strategy.controllers.items():
                if not controller.processed_data.get("ready"):
                    continue
                result["ready_controller_ids"].append(controller_id)
                report = strategy.controller_reports.get(controller_id, {}).get("performance")
                if report is not None:
                    config = controller.config
                    result["performance"][controller_id] = dict(
                        controller_id=controller_id, connector=config.connector_name, pair=config.trading_pair,
                        market="spot" if config.connector_name == "okx" else "perpetual",
                        observed_at=timestamp, source="v2_controller_report_cache",
                        valuation_basis="executor_and_position_hold_reports", report=report.model_dump(mode="json"))
    # These observations become part of the existing JSONB status publication.
    return json.loads(json.dumps(result, default=str, allow_nan=False))
