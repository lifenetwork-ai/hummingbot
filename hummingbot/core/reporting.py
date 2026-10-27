"""Read-only views of persisted observations, independent of HTTP adapters."""
import copy


def reporting_view(row):
    now = float(row["database_now"])
    status = row.get("status") or {}
    age = row.get("heartbeat_age_s")
    available = bool(row.get("owner_uuid")) and age is not None and 0 <= float(age) <= 5
    reporting = copy.deepcopy(status.get("reporting") or {})
    active = set(reporting.get("active_connectors", [])) if (
        available and status.get("running") and not status.get("reporting_unavailable")) else set()

    def stale(timestamp, connector):
        return connector not in active or timestamp is None or not 0 <= now - timestamp <= 60

    accounts = reporting.get("accounts", [])
    for account in accounts:
        for balance in account["balances"]:
            balance["stale"] = not balance.get("present_in_latest_snapshot", True) or stale(balance.get("observed_at"), balance["connector"])
    positions = []
    for name, observation in reporting.get("positions", {}).items():
        observation.update(market="perpetual", connector=name, stale=stale(observation["observed_at"], name))
        for item in observation["items"]:
            item["stale"] = stale(item.get("observed_at"), name)
        positions.append(observation)
    performance = list(reporting.get("performance", {}).values())
    for item in performance:
        item["stale"] = item["controller_id"] not in reporting.get("ready_controller_ids", []) or stale(item["observed_at"], item["connector"])
    return dict(server_time=now, owner_available=available, accounts=accounts, positions=positions, performance=performance)
