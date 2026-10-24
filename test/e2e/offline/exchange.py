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
    def __init__(self):
        self.fixtures = json.loads(Path("/test-assets/fixtures.json").read_text())
        self.orders = {}
        self.fills = []
        self.accepted = []
        self.requests = []
        self.unknown = []
        self.sockets = {}
        self.hold_ack = False
        self.release = asyncio.Event()
        self.release.set()

    def event(self, order, fill=None):
        message = copy.deepcopy(self.fixtures["order_event"])
        message["arg"]["instId"] = "BTC-USDT"
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
        channels = self.sockets[socket] = set()
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
                    signed = credentials["timestamp"] + "GET/users/self/verify"
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
                        await socket.send_json({"event": "subscribe", "arg": arg})
                        if channel == "books":
                            await socket.send_json({"arg": arg, "action": "snapshot", "data": [self.book()]})
                        elif channel == "account":
                            await socket.send_json({"arg": arg, "data": self.fixtures["balances"]["data"]})
        finally:
            self.sockets.pop(socket, None)
        return socket

    @staticmethod
    def book():
        return {"asks": [["101", "100", "0", "1"]], "bids": [["99", "100", "0", "1"]], "ts": millis()}

    @staticmethod
    def ok(data):
        return web.json_response({"code": "0", "msg": "", "data": data})

    async def handle(self, request):
        path, method = request.path, request.method
        if path.startswith("/ws/"):
            return await self.websocket(request)
        if path == "/ledger":
            return web.json_response({"accepted": self.accepted, "orders": list(self.orders.values()),
                                      "fills": self.fills, "requests": self.requests, "unknown": self.unknown})
        if path == "/control":
            return await self.control(await request.json())
        raw = await request.text()
        data = json.loads(raw) if raw else {}
        self.requests.append({"method": method, "path": path, "at_ms": millis()})
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
            return web.json_response(self.fixtures["instruments"])
        if path == "/api/v5/account/balance":
            return web.json_response(self.fixtures["balances"])
        if path == "/api/v5/market/books":
            return self.ok([self.book()])
        if path in ("/api/v5/market/ticker", "/api/v5/market/tickers"):
            return self.ok([{"instId": "BTC-USDT", "last": "100", "askPx": "101", "bidPx": "99", "ts": millis()}])
        if path == "/api/v5/trade/orders-pending":
            return self.ok([order for order in self.orders.values() if order["state"] in ("live", "partially_filled")])
        if path == "/api/v5/trade/fills":
            return self.ok([fill for fill in self.fills if fill["ordId"] == request.query.get("ordId")])
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
            await self.release.wait()
            await self.broadcast(self.event(order))
            return self.ok([{"clOrdId": client_id, "ordId": order["ordId"], "sCode": "0", "sMsg": ""}])
        if path in ("/api/v5/trade/cancel-order", "/api/v5/trade/cancel-batch-orders"):
            result = []
            for item in data if isinstance(data, list) else [data]:
                order = self.orders[item["clOrdId"]]
                if order["state"] != "filled":
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
        if "hold_ack" in data:
            self.hold_ack = data["hold_ack"]
            self.release.clear() if self.hold_ack else self.release.set()
        if data.get("drop_ws"):
            await asyncio.gather(*(socket.close() for socket in list(self.sockets)))
        if "fill" in data:
            spec = data["fill"]
            order = self.orders[spec["clOrdId"]]
            amount = Decimal(spec["amount"])
            total = Decimal(order["accFillSz"]) + amount
            assert total <= Decimal(order["sz"])
            order.update(accFillSz=str(total), state="filled" if total == Decimal(order["sz"]) else "partially_filled", uTime=millis())
            fill = {"instType": "SPOT", "instId": "BTC-USDT", "tradeId": str(len(self.fills) + 1),
                    "clOrdId": order["clOrdId"], "ordId": order["ordId"], "fillPx": order["px"],
                    "fillSz": str(amount), "feeCcy": "USDT", "fee": str(-Decimal(spec["fee"])), "ts": millis()}
            self.fills.append(fill)
            for details in self.fixtures["balances"]["data"][0]["details"]:
                if details["ccy"] == "BTC":
                    delta = amount if order["side"] == "buy" else -amount
                else:
                    quote = amount * Decimal(order["px"])
                    delta = (-quote if order["side"] == "buy" else quote) - Decimal(spec["fee"])
                details["eq"] = details["availEq"] = str(Decimal(details["eq"]) + delta)
            message = self.event(order, fill)
            await self.broadcast(message)
            await self.broadcast({"arg": {"channel": "account"}, "data": self.fixtures["balances"]["data"]})
            if data.get("duplicate"):
                await self.broadcast(message)
        return web.json_response({"ok": True})


if __name__ == "__main__":
    exchange = Exchange()
    app = web.Application()
    app.router.add_route("*", "/{path:.*}", exchange.handle)
    web.run_app(app, host="0.0.0.0", port=8080, access_log=None)
