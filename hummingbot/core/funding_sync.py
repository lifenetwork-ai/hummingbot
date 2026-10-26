"""Bounded GET-only funding backfill; scheduling/ownership belong to ServiceHost."""
import asyncio
import copy
import re

from hummingbot.model.decimal_type_decorator import SqliteDecimal

DAY_MS = 86400000
RECENT_MS = 6 * DAY_MS  # Leave margin at the recent endpoint's seven-day boundary.
HISTORY_MS = 89 * DAY_MS
OVERLAP_MS = 3600000


def public_status(state, *, now_ms=None, error=None):
    until = state.get("covered_until_ms")
    interval_ms = state.get("poll_interval_seconds", 120) * 1000
    error = error or state.get("last_error")
    return dict(state="unavailable" if error else ("backfilling" if state.get("pending") or
                (now_ms is not None and until is not None and until < now_ms - interval_ms) else
                "observed" if until is not None else "waiting"),
                account_id=state.get("account_id"), environment=state.get("environment"),
                pairs=state.get("pairs", []), covered_from_ms=state.get("covered_from_ms"),
                covered_until_ms=until, last_success_ms=state.get("last_success_ms"),
                backfill_pending=bool(state.get("pending")) or bool(now_ms and until and until < now_ms - interval_ms),
                history_gap_from_ms=state.get("history_gap_from_ms"), history_gap_until_ms=state.get("history_gap_until_ms"),
                stale=state.get("last_success_ms") is None or bool(now_ms and now_ms - state["last_success_ms"] > max(300000, 2 * interval_ms)),
                error=error, source="okx_account_bills")


class FundingSync:
    def __init__(self, store, getter, *, environment, max_pages=2, poll_interval_seconds=120):
        if environment not in ("demo", "live") or not 1 <= max_pages <= 5 or not 1 <= poll_interval_seconds <= 3600:
            raise ValueError("Invalid funding observer bounds")
        self.store, self.getter, self.environment = store, getter, environment
        self.max_pages, self.account_id = max_pages, None
        self.poll_interval_seconds = poll_interval_seconds

    async def get(self, path, params):
        response = await asyncio.wait_for(self.getter(path, params), timeout=10)
        if str(response.get("code")) != "0" or not isinstance(response.get("data"), list):
            raise ValueError("Funding read unavailable")
        return response["data"]

    async def sync(self, owner_uuid, generation, *, now_ms, pairs, earliest_ms=None):
        pairs = sorted(p for p in pairs if re.fullmatch(r"[A-Z0-9][A-Z0-9._]*-USDT", p))
        if not pairs:
            return {}
        if self.account_id is None:
            rows = await self.get("/api/v5/account/config", {})
            if len(rows) != 1 or not rows[0].get("uid"):
                raise ValueError("Funding account identity unavailable")
            self.account_id = str(rows[0]["uid"])
        state = await asyncio.to_thread(self.store.load)
        if (state.get("account_id"), state.get("environment")) != (self.account_id, self.environment):
            state = {}
        state = copy.deepcopy(state)
        cutoff = now_ms - HISTORY_MS
        gap_from, gap_until = state.get("history_gap_from_ms"), state.get("history_gap_until_ms")
        if gap_from is not None and gap_until is not None and (
                gap_from >= gap_until or
                state.get("covered_from_ms", now_ms) <= gap_from < gap_until <= state.get("covered_until_ms", 0)):
            # Repair earlier checkpoints that mistook retained, already-observed
            # history (or an expired overlap) for missing settlements.
            state.pop("history_gap_from_ms", None)
            state.pop("history_gap_until_ms", None)

        def history_gap(begin, end):
            state["history_gap_from_ms"] = min(begin, state.get("history_gap_from_ms", begin))
            state["history_gap_until_ms"] = max(end, state.get("history_gap_until_ms", end))

        requested = min(earliest_ms if earliest_ms is not None else now_ms - 7 * DAY_MS, now_ms)
        lower = max(requested, cutoff)
        # A newly introduced pair needs its own bounded historical sweep. Replay
        # existing pairs through the same account pages instead of N pair calls.
        if not set(pairs) <= set(state.get("pairs", [])):
            state.update(pending=None, covered_from_ms=lower, covered_until_ms=lower)
            if requested < lower:
                history_gap(requested, lower)
        elif state.get("covered_until_ms", cutoff) < cutoff:
            # A downtime gap starts where successful coverage ended, not at the
            # first historical order. Persisted old coverage does not expire.
            history_gap(state["covered_until_ms"], cutoff)
            state.update(covered_from_ms=cutoff, covered_until_ms=cutoff, pending=None)
        elif (state.get("pending") or {}).get("begin", cutoff) < cutoff:
            state["pending"] = None  # Only the already-covered overlap expired.
        state.update(account_id=self.account_id, environment=self.environment,
                     poll_interval_seconds=self.poll_interval_seconds,
                     pairs=sorted(set(pairs) | set(state.get("pairs", []))))
        state.setdefault("covered_from_ms", lower)
        state.setdefault("covered_until_ms", lower)
        pending = state.get("pending")
        if not pending:
            begin = max(state["covered_from_ms"], state["covered_until_ms"] - OVERLAP_MS, cutoff)
            pending = dict(begin=begin, end=min(now_ms, begin + DAY_MS), after=None)
        if pending["begin"] >= pending["end"]:
            return state
        path = "/api/v5/account/bills-archive" if pending["begin"] < now_ms - RECENT_MS else "/api/v5/account/bills"
        for _ in range(self.max_pages):
            params = dict(instType="SWAP", type="8", begin=pending["begin"], end=pending["end"] - 1, limit=100)
            if pending.get("after"):
                params["after"] = pending["after"]
            page = await self.get(path, params)
            if len(page) > 100:
                raise ValueError("Funding page exceeds its limit")
            ids, bills = [], []
            for raw in page:
                identity = str(raw.get("billId", ""))
                if not identity.isdigit() or len(identity) > 120:
                    raise ValueError("Funding bill identity unavailable")
                ids.append(identity)
                symbol = str(raw.get("instId", "")).removesuffix("-SWAP")
                if (str(raw.get("type")) != "8" or str(raw.get("subType")) not in ("173", "174")
                        or raw.get("instId") != symbol + "-SWAP" or symbol not in state["pairs"]):
                    continue
                timestamp = int(raw["ts"])
                amount = SqliteDecimal._finite_decimal(raw["pnl"])
                if (not pending["begin"] <= timestamp < pending["end"] or raw.get("ccy") != "USDT"
                        or amount is None or (raw["subType"] == "173" and amount > 0)
                        or (raw["subType"] == "174" and amount < 0)):
                    raise ValueError("Invalid funding bill economics or scope")
                bills.append(dict(account_id=self.account_id, environment=self.environment, bill_id=identity,
                                  symbol=symbol, timestamp=timestamp, currency=raw["ccy"], amount=amount, rate=None))
            if pending.get("after") and ids and max(map(int, ids)) >= int(pending["after"]):
                # Identical boundary rows are allowed; the cursor itself still
                # must move backwards. Economic duplicates are DB-idempotent.
                if min(map(int, ids)) >= int(pending["after"]):
                    raise ValueError("Funding pagination did not progress")
            if len(page) < 100:
                state["covered_until_ms"] = max(state["covered_until_ms"], pending["end"])
                state["pending"] = None
            else:
                after = ids[-1]
                if pending.get("after") and int(after) >= int(pending["after"]):
                    raise ValueError("Funding pagination did not progress")
                pending = {**pending, "after": after}
                state["pending"] = pending
            state.update(last_success_ms=now_ms, last_error=None)
            await asyncio.to_thread(self.store.commit, owner_uuid, generation, bills, copy.deepcopy(state))
            if state["pending"] is None:
                break
        return state


class FundingReader:
    """Reuse connector authentication/transport without starting any network tasks."""
    PATHS = {"/api/v5/account/config", "/api/v5/account/bills", "/api/v5/account/bills-archive"}

    def __init__(self, connector):
        self.connector = connector

    @classmethod
    def from_environment(cls):
        from hummingbot.client.config.config_helpers import api_keys_from_connector_config_map, get_connector_class
        from hummingbot.client.config.environment_credentials import load_connector_credentials
        from hummingbot.client.settings import AllConnectorSettings
        config = load_connector_credentials("okx_perpetual")
        setting = AllConnectorSettings.get_connector_settings()["okx_perpetual"]
        params = setting.conn_init_parameters(trading_pairs=[], trading_required=False,
                                              api_keys=api_keys_from_connector_config_map(config))
        return cls(get_connector_class("okx_perpetual")(**params))

    async def __call__(self, path, params):
        if path not in self.PATHS:
            raise ValueError("Funding observer only permits account GET readers")
        return await self.connector._api_get(path_url=path, params=params, is_auth_required=True)
