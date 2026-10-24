"""Bounded observational comparison using existing connector readers and sizing.

This module never calls tracker updates, cancellation, startup recovery, control
transitions or exchange mutations. Its only persistence is the caller's report.
"""
import asyncio
import copy
from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from hummingbot.core.bot_ownership import BotOwnership
from hummingbot.core.data_type.common import OrderType, TradeType
from hummingbot.core.data_type.in_flight_order import InFlightOrder


class ReconciliationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    market: Literal["spot", "perpetual", "all"] = "all"
    pair: Optional[str] = Field(default=None, max_length=120, pattern=r"^[A-Z0-9][A-Z0-9._]*-USDT$")
    lookback_seconds: int = Field(default=3600, ge=60, le=86400, strict=True)
    max_orders: int = Field(default=100, ge=1, le=200, strict=True)
    max_pages: int = Field(default=5, ge=1, le=10, strict=True)


def expected_positions(core, pair=None):
    expected, unsettled = {}, False
    orchestrator = getattr(core.strategy, "executor_orchestrator", None) if core.strategy else None
    if orchestrator is None:
        return None, False

    def add(trading_pair, side, amount):
        if (pair is None or pair == trading_pair) and amount:
            key = trading_pair, side
            expected[key] = expected.get(key, Decimal(0)) + abs(Decimal(amount))

    for holds in orchestrator.positions_held.values():
        for hold in holds:
            if hold.connector_name == "okx_perpetual":
                add(hold.trading_pair, "LONG" if hold.net_amount_base > 0 else "SHORT", hold.net_amount_base)
    for executors in orchestrator.active_executors.values():
        for executor in executors:
            config = executor.config
            if config.connector_name != "okx_perpetual" or (pair and config.trading_pair != pair):
                continue
            if executor.is_done:
                # A completed hold can be in transit to the existing hold cache.
                if (getattr(getattr(executor, "close_type", None), "name", None) == "POSITION_HOLD"
                        and config.id not in orchestrator.executors_ids_position_held):
                    unsettled = True
                continue
            if not hasattr(executor, "amount_to_close"):
                return None, False
            add(config.trading_pair, "LONG" if config.side == TradeType.BUY else "SHORT", executor.amount_to_close)
    return expected, unsettled


def _difference(kind, local, exchange, **identity):
    return dict(kind=kind, local=None if local is None else str(local),
                exchange=None if exchange is None else str(exchange), **identity)


async def check_market(connector, market, snapshot, request, expected, started_at):
    name = "okx" if market == "spot" else "okx_perpetual"
    result = dict(market=market, state="unavailable", local_observed_at=started_at, exchange_observed_at=None,
                  orders_checked=0, fills_checked=0, positions_checked=0, complete=False, reasons=[], differences=[])
    if connector is None:
        result["reasons"].append("authenticated_connector_unavailable")
        return result
    result["state"] = "matched"
    if snapshot["truncated"]:
        result["reasons"].append("local_scope_limit")
    local_orders = [row for row in snapshot["orders"] if row["connector"] == name]
    try:
        pending = await BotOwnership._open_orders(connector, max_pages=request.max_pages)
        known = {row["id"] for row in local_orders}
        prefix = connector.client_order_id_prefix
        for row in pending:
            client_id = row.get("clOrdId") or ""
            if client_id.startswith(prefix) and client_id not in known and (not request.pair or row.get("instId", "").removesuffix("-SWAP") == request.pair):
                result["reasons"].append("exchange_open_order_outside_local_scope")
        for local in local_orders:
            identity = dict(order_id=local["id"], pair=local["pair"])
            if local["side"] is None or local["exchange_order_id"] is None:
                result["reasons"].append("unresolved_local_order_identity")
                continue
            order = InFlightOrder(client_order_id=local["id"], exchange_order_id=local["exchange_order_id"],
                                  trading_pair=local["pair"], order_type=OrderType[local["order_type"]], trade_type=TradeType[local["side"]],
                                  amount=local["amount"], price=local["price"], creation_timestamp=local["created_at"].timestamp())
            response = await connector._request_order_update(order)
            if str(response.get("code")) != "0" or len(response.get("data", [])) != 1:
                result["reasons"].append("order_read_unavailable")
                continue
            actual = response["data"][0]
            if str(actual.get("ordId")) != str(local["exchange_order_id"]) or actual.get("clOrdId") != local["id"]:
                result["differences"].append(_difference("order_identity", local["exchange_order_id"], actual.get("ordId"), **identity))
                continue
            if int(actual.get("uTime", "0")) > int(started_at.timestamp() * 1000):
                result["reasons"].append("exchange_changed_during_check")
            result["orders_checked"] += 1
            local_fills = {row["id"]: row for row in snapshot["fills"] if row["connector"] == name and row["order_id"] == local["id"]}
            cumulative = sum((Decimal(row["amount"]) for row in local_fills.values()), Decimal(0))
            quantity = Decimal(actual.get("accFillSz", "0"))
            if market == "perpetual":
                quantity = connector._format_size_to_amount(local["pair"], quantity)
            if abs(cumulative - quantity) > Decimal("0.000001"):
                result["differences"].append(_difference("order_cumulative", cumulative, quantity, **identity))
            local_status = {"BuyOrderCompleted": "filled", "SellOrderCompleted": "filled", "OrderCancelled": "canceled",
                            "OrderExpired": "canceled", "BuyOrderCreated": "live", "SellOrderCreated": "live", "OrderFailure": "failed"}.get(local["status"])
            if local["status"] == "OrderFilled":
                local_status = "filled" if cumulative == local["amount"] else "partially_filled"
            if local_status is None:
                result["reasons"].append("unknown_local_order_status")
            elif local_status != actual.get("state"):
                result["differences"].append(_difference("order_status", local_status, actual.get("state"), **identity))
            exchange_fills, after, seen_pages = {}, None, set()
            for page_index in range(request.max_pages):
                response = await connector._request_order_fills(order, after=after, limit=100)
                if str(response.get("code")) != "0" or not isinstance(response.get("data"), list):
                    raise ValueError("Invalid fill response")
                page = response["data"]
                for fill in page:
                    trade_id = str(fill["tradeId"])
                    if str(fill.get("ordId")) != str(order.exchange_order_id) or trade_id in exchange_fills:
                        result["reasons"].append("fill_identity_or_pagination_overlap")
                    exchange_fills[trade_id] = fill
                    if int(fill.get("ts", "0")) > int(started_at.timestamp() * 1000):
                        result["reasons"].append("exchange_changed_during_check")
                if len(page) < 100:
                    break
                cursor = page[-1].get("billId")
                if not cursor or str(cursor) in seen_pages or page_index + 1 == request.max_pages:
                    result["reasons"].append("fill_page_limit_or_gap")
                    break
                after = str(cursor)
                seen_pages.add(after)
            exchange_total = sum((Decimal(fill["fillSz"]) for fill in exchange_fills.values()), Decimal(0))
            if market == "perpetual":
                exchange_total = connector._format_size_to_amount(local["pair"], exchange_total)
            if abs(exchange_total - quantity) > Decimal("0.000001"):
                result["reasons"].append("exchange_fill_coverage_incomplete")
            if local["created_at"].timestamp() < started_at.timestamp() - 3 * 86400 and cumulative:
                result["reasons"].append("fills_endpoint_history_window")
            for trade_id in sorted(set(local_fills) | set(exchange_fills)):
                local_fill, actual_fill = local_fills.get(trade_id), exchange_fills.get(trade_id)
                fill_identity = {**identity, "fill_id": trade_id}
                if local_fill is None or actual_fill is None:
                    result["differences"].append(_difference("fill_identity", local_fill is not None, actual_fill is not None, **fill_identity))
                    continue
                amount = Decimal(actual_fill["fillSz"])
                if market == "perpetual":
                    amount = abs(connector._format_size_to_amount(local["pair"], amount))
                for kind, recorded, observed in (("fill_quantity", Decimal(local_fill["amount"]), amount),
                                                 ("fill_price", Decimal(local_fill["price"]), Decimal(actual_fill["fillPx"]))):
                    # Recorder quantities/prices are stored at scale six.
                    if abs(recorded - observed) > Decimal("0.000001"):
                        result["differences"].append(_difference(kind, recorded, observed, **fill_identity))
                fee = local_fill["fee"]
                flat = {}
                for item in fee.get("flat_fees", []):
                    flat[item["token"]] = flat.get(item["token"], Decimal(0)) + Decimal(item["amount"])
                actual_fee = {actual_fill["feeCcy"]: -Decimal(actual_fill["fee"])}
                flat = {key: value for key, value in flat.items() if value}
                actual_fee = {key: value for key, value in actual_fee.items() if value}
                if Decimal(str(fee.get("percent", "0"))):
                    result["reasons"].append("original_fee_percentage_not_actual_amount")
                elif flat != actual_fee:
                    result["differences"].append(_difference("fill_fee", sorted(flat.items()), sorted(actual_fee.items()), **fill_identity))
                result["fills_checked"] += 1
            final_response = await connector._request_order_update(order)
            final_rows = final_response.get("data", [])
            if str(final_response.get("code")) != "0" or len(final_rows) != 1:
                result["reasons"].append("final_order_read_unavailable")
            elif any(final_rows[0].get(key) != actual.get(key) for key in ("ordId", "clOrdId", "state", "accFillSz", "uTime")):
                result["reasons"].append("exchange_changed_during_check")
        if market == "perpetual":
            positions, unsettled = expected
            if positions is None or unsettled:
                result["reasons"].append("local_exposure_unavailable_or_unsettled")
            else:
                pairs = sorted({key[0] for key in positions} | {row["pair"] for row in local_orders}
                               | set(connector.trading_pairs or []))
                if request.pair:
                    pairs = [request.pair]
                actual_positions = {}
                for pair in pairs:
                    symbol = await connector.exchange_symbol_associated_to_pair(pair)
                    response = await connector._api_get(path_url="/api/v5/account/positions", params={"instId": symbol},
                                                        is_auth_required=True, trading_pair=pair)
                    if str(response.get("code")) != "0" or not isinstance(response.get("data"), list):
                        raise ValueError("Invalid position response")
                    for row in response["data"]:
                        if row.get("instId") != symbol:
                            raise ValueError("Position scope mismatch")
                        amount = connector.get_position_amount(row, pair)
                        if int(row.get("uTime", "0")) > int(started_at.timestamp() * 1000):
                            result["reasons"].append("exchange_changed_during_check")
                        if amount:
                            key = pair, connector.get_position_side(row).name
                            actual_positions[key] = actual_positions.get(key, Decimal(0)) + amount
                for key in sorted(set(positions) | set(actual_positions)):
                    if abs(positions.get(key, Decimal(0)) - actual_positions.get(key, Decimal(0))) > Decimal("0.000001"):
                        result["differences"].append(_difference("position_quantity", positions.get(key, Decimal(0)), actual_positions.get(key, Decimal(0)), pair=key[0], side=key[1]))
                    result["positions_checked"] += 1
        result["exchange_observed_at"] = datetime.now(timezone.utc)
        result["complete"] = not result["reasons"]
        result["state"] = "incomplete" if result["reasons"] else ("mismatch" if result["differences"] else "matched")
    except Exception as error:
        result["state"] = "unavailable"
        result["reasons"].append("exchange_read_failed_" + type(error).__name__)
    result["reasons"] = sorted(set(result["reasons"]))
    return result


async def perform_check(store, core, request, *, timeout=20):
    started = datetime.now(timezone.utc)
    cutoff = int((started.timestamp() - request.lookback_seconds) * 1000)
    selected = ("spot", "perpetual") if request.market == "all" else (request.market,)
    snapshot = await asyncio.to_thread(store.reconciliation_scope, request, cutoff)
    expected = copy.deepcopy(expected_positions(core, request.pair))

    async def read(market):
        try:
            return await asyncio.wait_for(check_market(core.connector_manager.connectors.get(
                "okx" if market == "spot" else "okx_perpetual"), market, snapshot, request, expected, started), timeout=timeout)
        except asyncio.TimeoutError:
            return dict(market=market, state="unavailable", complete=False, reasons=["check_timeout"], differences=[],
                        local_observed_at=started, exchange_observed_at=None, orders_checked=0, fills_checked=0, positions_checked=0)

    markets = await asyncio.gather(*(read(market) for market in selected))
    after = await asyncio.to_thread(store.reconciliation_scope, request, cutoff)
    if after["fingerprint"] != snapshot["fingerprint"] or expected_positions(core, request.pair) != expected:
        for market in markets:
            if market["state"] != "unavailable":
                market.update(state="incomplete", complete=False)
                market["reasons"].append("local_state_changed_during_check")
    state = next((value for value in ("unavailable", "incomplete", "mismatch") if any(row["state"] == value for row in markets)), "matched")
    return dict(state=state, markets=markets, started_at=started, finished_at=datetime.now(timezone.utc))
