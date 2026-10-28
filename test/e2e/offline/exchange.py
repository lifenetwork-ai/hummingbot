"""Small independent OKX HTTP/WS truth server; no application DB access.

The response shapes come from the existing connector fixtures. This is a bounded
scripted ledger, not a matching engine. Public control endpoints exist only on the
isolated test network and are never included in the application image/HTTP API.
"""
import asyncio
import base64
import copy
import hashlib
import hmac
import json
import time
from decimal import Decimal
from pathlib import Path

from aiohttp import WSMsgType, web


def millis():
    return str(int(time.time() * 1000))


class Exchange:
    def __init__(self, fixtures=None):
        self.fixtures = (copy.deepcopy(fixtures) if fixtures is not None else
                         json.loads(Path("/test-assets/fixtures.json").read_text()))
        self.candle_width = Decimal(".005")
        self.candle_stale = False
        self.last_candle_bucket = None
        self.orders = {}
        self.fills = []
        self.funding_bills = []
        self.funding_error = False
        self.accepted = []
        self.requests = []
        self.unknown = []
        self.sockets = {}
        self.positions = {}
        self.position_mode = "long_short_mode"
        self.mutations = []
        self.connections = 0
        self.subscriptions = []
        self.hold_ack = False
        self.cancel_before_ack = False
        self.cancel_delay_seconds = 0
        self.release = asyncio.Event()
        self.release.set()
        self.socket_args = {}
        self.prices = {"BTC-USDT": "100", "ETH-USDT": "10", "AVAX-USDT": "20", "LIFE-USDT": "0.1"}
        self.stale_pairs = set()
        self.stale_instruments = set()
        self.book_overrides = {}
        self.include_own_orders = False
        self.markets_task = None
        self.target_updates_per_second = 0
        self.market_update_count = 0
        self.market_rate_changed = asyncio.Event()

    def event(self, order, fill=None):
        message = copy.deepcopy(self.fixtures["order_event"])
        message["arg"]["instId"] = order["instId"]
        if order["instId"].endswith("-SWAP"):
            message["arg"].update(instId=order["instId"], instType="SWAP")
        message["data"][0].update(order)
        if fill:
            message["data"][0].update(
                fillSz=fill["fillSz"], fillPx=fill["fillPx"], tradeId=fill["tradeId"],
                fillFee=fill["fee"], fillFeeCcy=fill["feeCcy"], fillTime=fill["ts"])
        return message

    async def broadcast(self, message):
        for socket, channels in list(self.sockets.items()):
            if not socket.closed and message["arg"]["channel"] in channels:
                await socket.send_json(message)

    async def websocket(self, request):
        socket = web.WebSocketResponse(autoping=True)
        await socket.prepare(request)
        self.connections += 1
        channels = self.sockets[socket] = set()
        args_list = self.socket_args[socket] = []
        try:
            async for message in socket:
                if message.type != WSMsgType.TEXT:
                    continue
                if message.data == "ping":
                    await socket.send_str("pong")
                    continue
                payload = json.loads(message.data)
                if payload["op"] == "login":
                    credentials = payload["args"][0]
                    signed = str(credentials["timestamp"]) + "GET/users/self/verify"
                    expected = base64.b64encode(hmac.new(b"offline-secret", signed.encode(), hashlib.sha256).digest()).decode()
                    if (credentials.get("apiKey") != "offline-key" or credentials.get("passphrase") != "offline-pass"
                            or not hmac.compare_digest(credentials.get("sign", ""), expected)):
                        self.unknown.append({"reason": "invalid WS auth"})
                        await socket.close()
                        continue
                    await socket.send_json({"event": "login", "code": "0", "msg": ""})
                elif payload["op"] == "subscribe":
                    for arg in payload["args"]:
                        channel = arg["channel"]
                        channels.add(channel)
                        args_list.append(arg)
                        self.subscriptions.append({"channel": channel, "connection": self.connections})
                        await socket.send_json({"event": "subscribe", "arg": arg})
                        if channel.startswith("candle"):
                            await socket.send_json({"arg": arg, "data": [self.candle(arg.get("instId", "LIFE-USDT"), channel[6:])]})
                        elif channel == "books":
                            await socket.send_json({"arg": arg, "action": "snapshot", "data": [self.book(arg.get("instId", "BTC-USDT"))]})
                        elif channel == "account":
                            await socket.send_json({"arg": arg, "data": self.fixtures["balances"]["data"]})
                        elif channel == "positions":
                            await socket.send_json({"arg": arg, "data": list(self.positions.values())})
                        elif channel in ("funding-rate", "mark-price", "index-tickers"):
                            await socket.send_json({"arg": arg, "data": [self.funding(arg.get("instId", "BTC-USDT-SWAP"))]})
        finally:
            self.sockets.pop(socket, None)
            self.socket_args.pop(socket, None)
        return socket

    @staticmethod
    def candle_interval(bar):
        unit = bar[-1].lower()
        return int(bar[:-1]) * {"s": 1, "m": 60, "h": 3600}[unit]

    def candle(self, pair, bar, stamp=None):
        interval = self.candle_interval(bar)
        stamp = int(time.time()) // interval * interval if stamp is None else stamp
        mid = Decimal(self.prices[pair.removesuffix("-SWAP")])
        return [str(stamp * 1000), str(mid), str(mid * (1 + self.candle_width)),
                str(mid * (1 - self.candle_width)), str(mid), "1000", "100", "100", "1"]

    def book(self, pair="BTC-USDT"):
        mid = Decimal(self.prices[pair.removesuffix("-SWAP")])
        defaults = {"asks": [[str(mid * Decimal("1.01")), "100000"]],
                    "bids": [[str(mid * Decimal("0.99")), "100000"]]}
        levels = {side: {Decimal(str(price)): Decimal(str(amount)) for price, amount in rows}
                  for side, rows in self.book_overrides.get(pair, defaults).items()}
        if self.include_own_orders:
            for order in self.orders.values():
                if order["instId"] == pair and order["state"] in ("live", "partially_filled"):
                    side = "bids" if order["side"] == "buy" else "asks"
                    price = Decimal(order["px"])
                    levels[side][price] = levels[side].get(price, Decimal("0")) + Decimal(order["sz"]) - Decimal(order["accFillSz"])
        return {**{side: [[str(price), str(amount), "0", "1"] for price, amount in sorted(
            rows.items(), reverse=side == "bids") if amount > 0] for side, rows in levels.items()}, "ts": millis()}

    async def market_updates(self):
        next_update = time.monotonic()
        previous_target = None
        while True:
            subscriptions = sum(arg.get("channel") == "books" for args in self.socket_args.values() for arg in args)
            if previous_target != self.target_updates_per_second:
                next_update = time.monotonic()
                previous_target = self.target_updates_per_second
            # The burst gate requires at least the requested delivered rate.
            # Send genuine extra frames for timer/batch jitter, never inflate
            # counters or lower the minimum. Ordinary 100/s load is unchanged.
            rate = self.target_updates_per_second * (1.02 if self.target_updates_per_second > 100 else 1)
            interval = subscriptions / rate if rate and subscriptions else 0.2
            next_update += interval
            try:
                await asyncio.wait_for(self.market_rate_changed.wait(), timeout=max(0, next_update - time.monotonic()))
                self.market_rate_changed.clear()
                previous_target = None
                continue
            except asyncio.TimeoutError:
                pass
            for socket, args_list in list(self.socket_args.items()):
                try:
                    for arg in list(args_list):
                        pair = arg.get("instId", "")
                        channel = arg.get("channel", "")
                        if channel.startswith("candle"):
                            bucket = int(time.time()) // self.candle_interval(channel[6:])
                            if not socket.closed and not self.candle_stale and arg.get("last_bucket") != bucket:
                                arg["last_bucket"] = bucket
                                await socket.send_json({"arg": {key: value for key, value in arg.items() if key != "last_bucket"},
                                                        "data": [self.candle(pair, channel[6:])]})
                            continue
                        if (socket.closed or arg.get("channel") != "books" or pair.removesuffix("-SWAP") in self.stale_pairs
                                or pair in self.stale_instruments):
                            continue
                        await socket.send_json({"arg": arg, "action": "snapshot", "data": [self.book(pair)]})
                        self.market_update_count += 1
                except ConnectionError:
                    # A client may close after the check and before send_json.
                    # Retire that subscription without killing every other feed.
                    self.socket_args.pop(socket, None)
                    self.sockets.pop(socket, None)

    def funding(self, pair="BTC-USDT-SWAP"):
        price = self.prices[pair.removesuffix("-SWAP")]
        return {"instId": pair, "fundingRate": "0", "nextFundingRate": "0", "fundingTime": millis(),
                "nextFundingTime": str(int(millis()) + 3600000), "markPx": price, "idxPx": price, "ts": millis()}

    @staticmethod
    def ok(data):
        return web.json_response({"code": "0", "msg": "", "data": data})

    async def handle(self, request):
        path, method = request.path, request.method
        if path.startswith("/ws/"):
            return await self.websocket(request)
        if path == "/market-metrics":
            return web.json_response({"market_update_count": self.market_update_count,
                                      "observed_at_monotonic": time.monotonic()})
        if path == "/working-orders":
            return web.json_response({"orders": [order for order in self.orders.values()
                                                 if order["state"] in ("live", "partially_filled")]})
        if path == "/ledger":
            return web.json_response({"accepted": self.accepted, "orders": list(self.orders.values()),
                                      "fills": self.fills, "funding_bills": self.funding_bills, "requests": self.requests, "unknown": self.unknown,
                                      "positions": list(self.positions.values()), "mutations": self.mutations,
                                      "balances": self.fixtures["balances"]["data"][0]["details"],
                                      "market_update_count": self.market_update_count,
                                      "observed_at_monotonic": time.monotonic(),
                                      "connections": self.connections, "subscriptions": self.subscriptions})
        if path == "/control":
            return await self.control(await request.json())
        raw = await request.text()
        data = json.loads(raw) if raw else {}
        self.requests.append({"method": method, "path": path, "at_ms": millis(), "simulated": request.headers.get("x-simulated-trading"), "params": dict(request.query)})
        if path.startswith(("/api/v5/trade/", "/api/v5/account/")) and not request.headers.get("OK-ACCESS-KEY"):
            self.unknown.append({"path": path, "reason": "missing authentication"})
            return web.json_response({"code": "50103", "msg": "missing fixture auth", "data": []}, status=401)
        if request.headers.get("OK-ACCESS-KEY"):
            signed = request.headers["OK-ACCESS-TIMESTAMP"] + method + request.path_qs + raw
            expected = base64.b64encode(hmac.new(b"offline-secret", signed.encode(), hashlib.sha256).digest()).decode()
            if (request.headers["OK-ACCESS-KEY"] != "offline-key" or request.headers.get("OK-ACCESS-PASSPHRASE") != "offline-pass"
                    or not hmac.compare_digest(request.headers["OK-ACCESS-SIGN"], expected)):
                self.unknown.append({"method": method, "path": path, "reason": "invalid auth"})
                return web.json_response({"code": "50113", "msg": "invalid fixture signature", "data": []}, status=401)
        if path == "/api/v5/public/time":
            return self.ok([{"ts": millis()}])
        if path == "/api/v5/public/instruments":
            return web.json_response(self.fixtures["perpetual_instruments" if request.query.get("instType") == "SWAP" else "instruments"])
        if path in ("/api/v5/public/mark-price", "/api/v5/market/index-tickers", "/api/v5/public/funding-rate"):
            return self.ok([self.funding(request.query.get("instId", "BTC-USDT-SWAP"))])
        if path == "/api/v5/account/config":
            return self.ok([{"posMode": self.position_mode, "uid": "offline-account-1"}])
        if path == "/api/v5/account/positions":
            return self.ok([row for row in self.positions.values() if not request.query.get("instId") or row["instId"] == request.query["instId"]])
        if path in ("/api/v5/account/bills", "/api/v5/account/bills-archive"):
            if self.funding_error:
                return web.json_response({"code": "50011", "msg": "injected temporary failure", "data": []})
            rows = [row for row in self.funding_bills
                    if int(request.query.get("begin", "0")) <= int(row["ts"]) <= int(request.query.get("end", millis()))
                    and (not request.query.get("after") or int(row["billId"]) < int(request.query["after"]))
                    and (not request.query.get("type") or str(row["type"]) == request.query["type"])]
            rows.sort(key=lambda row: int(row["billId"]), reverse=True)
            return self.ok(rows[:min(100, int(request.query.get("limit", "100")))])
        if path in ("/api/v5/account/set-position-mode", "/api/v5/account/set-leverage"):
            self.mutations.append({"path": path, "data": data})
            if "posMode" in data:
                self.position_mode = data["posMode"]
            return self.ok([data])
        if path == "/api/v5/account/balance":
            return web.json_response(self.fixtures["balances"])
        if path == "/api/v5/market/history-candles":
            bar = request.query.get("bar", "1m")
            interval = self.candle_interval(bar)
            end = int(request.query.get("after", millis())) // 1000
            end = end // interval * interval
            if self.candle_stale:
                end -= 86400
            return self.ok([self.candle(request.query.get("instId", "LIFE-USDT"), bar, end - (i + 1) * interval)
                            for i in range(min(100, int(request.query.get("limit", "100"))))])
        if path == "/api/v5/market/books":
            return self.ok([self.book(request.query.get("instId", "BTC-USDT"))])
        if path in ("/api/v5/market/ticker", "/api/v5/market/tickers"):
            single = request.query.get('instId') if path.endswith('/ticker') else None
            swaps = request.query.get('instType') == 'SWAP' or bool(single and single.endswith('-SWAP'))
            instruments = self.fixtures['perpetual_instruments' if swaps else 'instruments']['data']
            rows = []
            for instrument in instruments:
                symbol = instrument['instId']
                pair = symbol.removesuffix('-SWAP')
                if (single is not None and symbol != single) or pair not in self.prices:
                    continue
                price = Decimal(self.prices[pair])
                rows.append(dict(instId=symbol, last=str(price), askPx=str(price * Decimal('1.01')),
                                 bidPx=str(price * Decimal('.99')), ts=millis()))
            return self.ok(rows)
        if path == "/api/v5/trade/orders-pending":
            return self.ok([order for order in self.orders.values() if order["state"] in ("live", "partially_filled")])
        if path == "/api/v5/trade/fills":
            return self.ok([fill for fill in self.fills if not request.query.get("ordId") or fill["ordId"] == request.query["ordId"]])
        if path == "/api/v5/trade/order" and method == "GET":
            order = self.orders.get(request.query.get("clOrdId"))
            if order is None:
                return web.json_response({"code": "51603", "msg": "order absent", "data": []})
            return self.ok([order])
        if path == "/api/v5/trade/order" and method == "POST":
            client_id = data["clOrdId"]
            if client_id in self.orders:
                self.unknown.append({"reason": "duplicate physical submission", "clOrdId": client_id})
                return web.json_response({"code": "1", "msg": "duplicate identity", "data": []})
            if int(request.headers.get("expTime", "0")) <= int(millis()):
                return web.json_response({"code": "50037", "msg": "expired", "data": []})
            order = {**data, "ordId": str(len(self.accepted) + 1), "state": "live", "accFillSz": "0",
                     "uTime": millis(), "cTime": millis(), "tradeId": "", "fillSz": "0", "fillPx": "0"}
            self.orders[client_id] = order
            self.accepted.append(copy.deepcopy(order))
            if self.cancel_before_ack:
                order.update(state="canceled", uTime=millis())
                await self.broadcast(self.event(order))
            await self.release.wait()
            await self.broadcast(self.event(order))
            return self.ok([{"clOrdId": client_id, "ordId": order["ordId"], "sCode": "0", "sMsg": ""}])
        if path in ("/api/v5/trade/cancel-order", "/api/v5/trade/cancel-batch-orders"):
            if self.cancel_delay_seconds:
                await asyncio.sleep(self.cancel_delay_seconds)
            result = []
            for item in data if isinstance(data, list) else [data]:
                order = self.orders.get(item["clOrdId"])
                if order is None:
                    result.append({"clOrdId": item["clOrdId"], "ordId": "", "sCode": "51400", "sMsg": "Order does not exist"})
                    continue
                if order["state"] != "filled":
                    order.setdefault('cancel_terminal_ms', millis())
                    order.update(state="canceled", uTime=millis())
                await self.broadcast(self.event(order))
                result.append({"clOrdId": order["clOrdId"], "ordId": order["ordId"], "sCode": "0", "sMsg": ""})
            return self.ok(result)
        # The existing default rate source uses a non-trading Gate.IO connector.
        # Its public shape is simulated too; the isolated network prevents fallback.
        if path == "/api/v4/spot/time":
            return web.json_response({"server_time": int(time.time()), "server_time_ms": int(millis())})
        if path == "/api/v4/spot/tickers":
            return web.json_response([{"currency_pair": "BTC_USDT", "lowest_ask": "101", "highest_bid": "99", "last": "100"}])
        if path == "/api/v4/spot/currency_pairs":
            return web.json_response([{"id": "BTC_USDT", "base": "BTC", "quote": "USDT", "trade_status": "tradable"}])
        self.unknown.append({"method": method, "path": path})
        return web.json_response({"error": "Unspecified simulator endpoint"}, status=500)

    async def control(self, data):
        if 'cancel_delay_seconds' in data:
            delay = float(data['cancel_delay_seconds'])
            assert 0 <= delay <= 10
            self.cancel_delay_seconds = delay
        if "target_updates_per_second" in data:
            self.target_updates_per_second = float(data["target_updates_per_second"])
            assert 0 <= self.target_updates_per_second <= 1000
            self.market_rate_changed.set()
        if "prices" in data:
            for pair, price in data["prices"].items():
                assert pair in self.prices and Decimal(price) > 0
                self.prices[pair] = str(price)
            self.mark_positions()
        if "stale_pairs" in data:
            self.stale_pairs = set(data["stale_pairs"])
        if "stale_instruments" in data:
            self.stale_instruments = set(data["stale_instruments"])
        if "book_overrides" in data:
            self.book_overrides = data["book_overrides"]
            assert all(set(value) == {"bids", "asks"} for value in self.book_overrides.values())
        if "candle_width" in data:
            self.candle_width = Decimal(str(data["candle_width"]))
            assert 0 <= self.candle_width < 1
        if "candle_stale" in data:
            self.candle_stale = bool(data["candle_stale"])
        if "include_own_orders" in data:
            self.include_own_orders = bool(data["include_own_orders"])
        if "hold_ack" in data:
            self.hold_ack = data["hold_ack"]
            self.release.clear() if self.hold_ack else self.release.set()
        if "cancel_before_ack" in data:
            self.cancel_before_ack = bool(data["cancel_before_ack"])
        if data.get("drop_ws"):
            await asyncio.gather(*(socket.close() for socket in list(self.sockets)))
        if "funding_bills" in data:
            self.funding_bills = copy.deepcopy(data["funding_bills"])
        if "funding_error" in data:
            self.funding_error = bool(data["funding_error"])
        if data.get("clear_positions"):
            self.positions.clear()
        if "replay_trade_id" in data:
            fill = next(item for item in self.fills if item["tradeId"] == data["replay_trade_id"])
            await self.broadcast(self.event(self.orders[fill["clOrdId"]], fill))
        if "fill" in data:
            spec = data["fill"]
            order = self.orders[spec["clOrdId"]]
            # A candidate GET /ledger can race with a real connector cancel.
            # A terminal order cannot be filled/reopened by the test driver.
            if order["state"] not in ("live", "partially_filled"):
                return web.json_response({"error": "order_not_fillable"}, status=409)
            amount = Decimal(spec["amount"])
            total = Decimal(order["accFillSz"]) + amount
            assert total <= Decimal(order["sz"])
            order.update(accFillSz=str(total), state="filled" if total == Decimal(order["sz"]) else "partially_filled", uTime=millis())
            perpetual = order["instId"].endswith("-SWAP")
            fill = {"instType": "SWAP" if perpetual else "SPOT", "instId": order["instId"], "tradeId": str(len(self.fills) + 1),
                    "clOrdId": order["clOrdId"], "ordId": order["ordId"], "fillPx": order["px"],
                    "fillSz": str(amount), "feeCcy": "USDT", "fee": str(-Decimal(spec["fee"])), "ts": millis(),
                    "side": order["side"], "posSide": order.get("posSide", "long"), "billId": str(len(self.fills) + 1)}
            self.fills.append(fill)
            if perpetual:
                side = order.get("posSide", "long")
                position = self.positions.setdefault((order["instId"], side), {"instId": order["instId"], "posSide": side, "pos": "0",
                                                                               "avgPx": order["px"], "notionalUsd": "0", "lever": "1", "upl": "0"})
                previous = Decimal(position["pos"])
                position["avgPx"] = str((previous * Decimal(position["avgPx"]) + amount * Decimal(order["px"])) / (previous + amount))
                position["pos"] = str(previous + amount)
                self.mark_positions()
            for details in self.fixtures["balances"]["data"][0]["details"]:
                if perpetual:
                    delta = -Decimal(spec["fee"]) if details["ccy"] == "USDT" else Decimal("0")
                    details["cashBal"] = details["availBal"] = details["eq"] = details["availEq"] = str(Decimal(details["eq"]) + delta)
                    continue
                if details["ccy"] == order["instId"].split("-")[0]:
                    delta = amount if order["side"] == "buy" else -amount
                elif details["ccy"] == "USDT":
                    quote = amount * Decimal(order["px"])
                    delta = (-quote if order["side"] == "buy" else quote) - Decimal(spec["fee"])
                else:
                    delta = Decimal("0")
                details["cashBal"] = details["availBal"] = details["eq"] = details["availEq"] = str(Decimal(details["eq"]) + delta)
            message = self.event(order, fill)
            await self.broadcast(message)
            if perpetual:
                await self.broadcast({"arg": {"channel": "positions"}, "data": copy.deepcopy(list(self.positions.values()))})
            await self.broadcast({"arg": {"channel": "account"}, "data": self.fixtures["balances"]["data"]})
            if data.get("duplicate"):
                await self.broadcast(message)
        return web.json_response({"ok": True})

    def mark_positions(self):
        for position in self.positions.values():
            mark = Decimal(self.prices[position["instId"].removesuffix("-SWAP")])
            amount = Decimal(position["pos"]) * Decimal("0.01")
            position["notionalUsd"] = str(amount * mark)
            position["upl"] = str(amount * (mark - Decimal(position["avgPx"]))
                                  * (-1 if position["posSide"] == "short" else 1))


if __name__ == "__main__":
    exchange = Exchange()
    app = web.Application()

    async def start(app):
        exchange.markets_task = asyncio.create_task(exchange.market_updates())

    async def stop(app):
        exchange.markets_task.cancel()
        await asyncio.gather(exchange.markets_task, return_exceptions=True)
    app.on_startup.append(start)
    app.on_cleanup.append(stop)
    app.router.add_route("*", "/{path:.*}", exchange.handle)
    web.run_app(app, host="0.0.0.0", port=8080, access_log=None)
