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
from collections import deque
from decimal import Decimal
from pathlib import Path

from aiohttp import WSMsgType, web

FIXTURE_KEYS = {
    'a': dict(api_key='offline-key', secret_key='offline-secret', passphrase='offline-pass'),
    'b': dict(api_key='offline-key-b', secret_key='offline-secret-b', passphrase='offline-pass-b'),
}


def millis():
    return str(int(time.time() * 1000))


class MarketDiagnostics:
    """Opt-in producer evidence; only the latest 64 observed seconds are kept."""

    def __init__(self):
        self.origin = time.monotonic()
        self.process_cpu_origin = time.process_time()
        self.seconds = deque(maxlen=64)
        self.discarded_seconds = 0
        self.delivered_frames = 0
        self.failed_frames = 0
        self.cpu_origin = self.cpu_stat()

    @staticmethod
    def cpu_stat():
        try:
            return {key: int(value) for key, value in (line.split() for line in Path('/sys/fs/cgroup/cpu.stat').read_text().splitlines())}
        except (OSError, ValueError):
            return {}

    def bucket(self, at):
        second = int(at - self.origin)
        if not self.seconds or self.seconds[-1]['second'] != second:
            self.discarded_seconds += int(len(self.seconds) == self.seconds.maxlen)
            self.seconds.append(dict(second=second, delivered_frames=0, failed_frames=0,
                                     max_schedule_lag_ms=0, max_book_build_ms=0, max_send_ms=0,
                                     min_subscriptions=None, max_subscriptions=0,
                                     process_cpu_seconds=time.process_time() - self.process_cpu_origin))
        return self.seconds[-1]

    def cycle(self, scheduled, actual, subscriptions):
        row = self.bucket(actual)
        row['max_schedule_lag_ms'] = max(row['max_schedule_lag_ms'], max(0, actual - scheduled) * 1000)
        row['min_subscriptions'] = subscriptions if row['min_subscriptions'] is None else min(row['min_subscriptions'], subscriptions)
        row['max_subscriptions'] = max(row['max_subscriptions'], subscriptions)

    def send(self, at, build_seconds, send_seconds, *, delivered):
        row = self.bucket(at)
        key = 'delivered_frames' if delivered else 'failed_frames'
        row[key] += 1
        if delivered:
            self.delivered_frames += 1
        else:
            self.failed_frames += 1
        row['max_book_build_ms'] = max(row['max_book_build_ms'], build_seconds * 1000)
        row['max_send_ms'] = max(row['max_send_ms'], send_seconds * 1000)

    def snapshot(self):
        cpu = self.cpu_stat()
        return dict(seconds=[dict(row) for row in self.seconds], discarded_seconds=self.discarded_seconds,
                    delivered_frames=self.delivered_frames, failed_frames=self.failed_frames,
                    process_cpu_seconds=time.process_time() - self.process_cpu_origin,
                    cgroup_cpu=cpu, cgroup_cpu_delta={key: value - self.cpu_origin[key]
                                                      for key, value in cpu.items() if key in self.cpu_origin})


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
        self.cleanup_market_closes = False
        self.cleanup_status_not_found_reads = 0
        self.cleanup_hidden_status_reads = {}
        self.mutations = []
        self.connections = 0
        self.subscriptions = []
        self.hold_ack = False
        self.submission_busy = False
        self.accepted_response_lost = 0
        self.cancel_before_ack = False
        self.cancel_before_ack_remaining = None
        self.cancel_delay_seconds = 0
        self.cancel_pending_visibility = {}
        self.cancel_pending_visibility_served = 0
        self.cancel_pending_visibility_reads = 0
        self.fill_on_cancel = {}
        self.release = asyncio.Event()
        self.release.set()
        self.socket_args = {}
        self.prices = {"BTC-USDT": "100", "ETH-USDT": "10", "AVAX-USDT": "20", "LIFE-USDT": "0.1"}
        self.stale_pairs = set()
        self.stale_instruments = set()
        self.rest_stale_instruments = set()
        self.rest_failed_instruments = set()
        self.book_overrides = {}
        self.enforce_post_only = False
        self.post_only_move_on_submit = {}
        self.include_own_orders = False
        self.markets_task = None
        self.target_updates_per_second = 0
        self.market_update_count = 0
        self.market_rate_changed = asyncio.Event()
        self.market_diagnostics = None
        self.quote_activity = None
        self.credential_generations = {'a'}
        self.socket_credentials = {}
        self.cleanup_barrier = 'none'
        self.cleanup_barrier_hits = deque(maxlen=16)
        self.cleanup_close_id = None
        self.cleanup_partial = 'none'
        self.cleanup_partial_event = None
        self.cleanup_release = asyncio.Event()
        self.cleanup_release.set()

    def authenticate(self, key, passphrase, signature, signed):
        generation = next((name for name, values in FIXTURE_KEYS.items() if values['api_key'] == key), None)
        if generation is not None and generation not in self.credential_generations:
            return generation, '50119'
        values = FIXTURE_KEYS.get(generation, FIXTURE_KEYS['a'])
        expected = base64.b64encode(hmac.new(values['secret_key'].encode(), signed.encode(), hashlib.sha256).digest()).decode()
        valid = generation is not None and passphrase == values['passphrase'] and hmac.compare_digest(signature, expected)
        return generation, '0' if valid else '50113'

    async def cleanup_wait(self, stage):
        self.cleanup_barrier_hits.append(dict(stage=stage, clOrdId=self.cleanup_close_id, at_ms=millis()))
        await self.cleanup_release.wait()

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
                    generation, code = self.authenticate(credentials.get('apiKey'), credentials.get('passphrase'), credentials.get('sign', ''), signed)
                    if code != '0':
                        if code != '50119':
                            self.unknown.append({"reason": "invalid WS auth"})
                        await socket.send_json({'event': 'error', 'code': code, 'msg': 'fixture key rejected'})
                        await socket.close()
                        continue
                    self.socket_credentials[socket] = generation
                    await socket.send_json({"event": "login", "code": "0", "msg": ""})
                elif payload["op"] == "subscribe":
                    for arg in payload["args"]:
                        channel = arg["channel"]
                        channels.add(channel)
                        args_list.append(arg)
                        self.subscriptions.append({"channel": channel, "connection": self.connections,
                                                   "credential_generation": self.socket_credentials.get(socket)})
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
            self.socket_credentials.pop(socket, None)
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
            diagnostics = self.market_diagnostics
            if diagnostics is not None:
                diagnostics.cycle(next_update, time.monotonic(), subscriptions)
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
                        if diagnostics is None:
                            await socket.send_json({"arg": arg, "action": "snapshot", "data": [self.book(pair)]})
                        else:
                            started = time.monotonic()
                            book = self.book(pair)
                            built = time.monotonic()
                            try:
                                await socket.send_json({"arg": arg, "action": "snapshot", "data": [book]})
                            except ConnectionError:
                                diagnostics.send(time.monotonic(), built - started, time.monotonic() - built, delivered=False)
                                raise
                            diagnostics.send(time.monotonic(), built - started, time.monotonic() - built, delivered=True)
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
        if path == '/quote-activity':
            return (web.json_response(self.quote_activity.snapshot()) if self.quote_activity else
                    web.json_response({'error': 'quote_activity_not_enabled'}, status=404))
        if path.startswith("/ws/"):
            return await self.websocket(request)
        if path == "/market-metrics":
            return web.json_response({"market_update_count": self.market_update_count,
                                      "observed_at_monotonic": time.monotonic(),
                                      **({'diagnostics': self.market_diagnostics.snapshot()} if self.market_diagnostics else {})})
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
                                      "cleanup_barrier": self.cleanup_barrier,
                                      "cleanup_barrier_hits": list(self.cleanup_barrier_hits),
                                      "cleanup_partial_event": self.cleanup_partial_event,
                                      "cancel_pending_visibility": self.cancel_pending_visibility,
                                      "cancel_pending_visibility_served": self.cancel_pending_visibility_served,
                                      "connections": self.connections, "subscriptions": self.subscriptions})
        if path == "/control":
            return await self.control(await request.json())
        raw = await request.text()
        data = json.loads(raw) if raw else {}
        observed = {"method": method, "path": path, "at_ms": millis(), "simulated": request.headers.get("x-simulated-trading"), "params": dict(request.query)}
        if path == '/api/v5/trade/order' and method == 'POST':
            observed['order'] = {key: data.get(key) for key in ('clOrdId', 'instId', 'ordType', 'posSide', 'side', 'sz')}
        self.requests.append(observed)
        if path.startswith(("/api/v5/trade/", "/api/v5/account/")) and not request.headers.get("OK-ACCESS-KEY"):
            self.unknown.append({"path": path, "reason": "missing authentication"})
            return web.json_response({"code": "50103", "msg": "missing fixture auth", "data": []}, status=401)
        if request.headers.get("OK-ACCESS-KEY"):
            signed = request.headers["OK-ACCESS-TIMESTAMP"] + method + request.path_qs + raw
            generation, code = self.authenticate(request.headers['OK-ACCESS-KEY'], request.headers.get('OK-ACCESS-PASSPHRASE'),
                                                 request.headers.get('OK-ACCESS-SIGN', ''), signed)
            observed.update(credential_generation=generation, authorized=code == '0')
            if code != '0':
                if code != '50119':
                    self.unknown.append({"method": method, "path": path, "reason": "invalid auth"})
                return web.json_response({"code": code, "msg": "fixture key rejected", "data": []}, status=200 if code == '50119' else 401)
        if path == "/api/v5/public/time":
            return self.ok([{"ts": millis()}])
        if path == "/api/v5/public/instruments":
            return web.json_response(self.fixtures["perpetual_instruments" if request.query.get("instType") == "SWAP" else "instruments"])
        if path in ("/api/v5/public/mark-price", "/api/v5/market/index-tickers", "/api/v5/public/funding-rate"):
            return self.ok([self.funding(request.query.get("instId", "BTC-USDT-SWAP"))])
        if path == "/api/v5/account/config":
            return self.ok([{"posMode": self.position_mode, "uid": "offline-account-1", "acctLv": "2", "perm": "read_only,trade"}])
        if path == "/api/v5/account/positions":
            if self.cleanup_barrier == 'verified_fill' and self.cleanup_close_id is not None:
                await self.cleanup_wait('verified_fill')
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
            instrument = request.query.get("instId", "BTC-USDT")
            if instrument in self.rest_failed_instruments:
                return web.json_response({'error': 'snapshot_unavailable'}, status=503)
            result = self.book(instrument)
            if instrument in self.rest_stale_instruments:
                result['ts'] = str(int(result['ts']) - 60000)
            return self.ok([result])
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
            rows = [order for order in self.orders.values() if order["state"] in ("live", "partially_filled")]
            for identity, remaining in list(self.cancel_pending_visibility.items()):
                order = self.orders[identity]
                if remaining and order['state'] == 'canceled':
                    # Only this account-list projection is delayed. Immutable
                    # status/fill/WS views retain their actual terminal truth.
                    rows.append(dict(order, state='live'))
                    self.cancel_pending_visibility[identity] -= 1
                    self.cancel_pending_visibility_served += 1
            return self.ok(rows)
        if path == "/api/v5/trade/fills":
            return self.ok([fill for fill in self.fills if not request.query.get("ordId") or fill["ordId"] == request.query["ordId"]])
        if path == "/api/v5/trade/order" and method == "GET":
            order = self.orders.get(request.query.get("clOrdId"))
            if order is None:
                return web.json_response({"code": "51603", "msg": "order absent", "data": []})
            identity = order['clOrdId']
            hidden = self.cleanup_hidden_status_reads.get(identity, 0)
            if hidden:
                if hidden == 1:
                    del self.cleanup_hidden_status_reads[identity]
                else:
                    self.cleanup_hidden_status_reads[identity] = hidden - 1
                observed['injected_cleanup_order_absence'] = True
                return web.json_response({"code": "51603", "msg": "status not visible yet", "data": []})
            return self.ok([order])
        if path == "/api/v5/trade/order" and method == "POST":
            if self.submission_busy:
                observed['injected_submission_busy'] = True
                return web.json_response({'code': '50013', 'msg': 'System is busy, please try again later.'}, status=429)
            # A bounded terminal-before-ACK fault admits only its selected
            # batch. Replacement requests wait before physical acceptance;
            # unrelated lost-response cases keep their original unbounded hold.
            if self.cancel_before_ack and self.cancel_before_ack_remaining == 0:
                await self.release.wait()
            client_id = data["clOrdId"]
            if client_id in self.orders:
                self.unknown.append({"reason": "duplicate physical submission", "clOrdId": client_id})
                return web.json_response({"code": "1", "msg": "duplicate identity", "data": []})
            if int(request.headers.get("expTime", "0")) <= int(millis()):
                return web.json_response({"code": "50037", "msg": "expired", "data": []})
            market_close = self.cleanup_market_closes and data.get('ordType') == 'market'
            if market_close:
                position = self.positions.get((data['instId'], data.get('posSide')))
                reducing = (data.get('posSide'), data.get('side')) in (('long', 'sell'), ('short', 'buy'))
                if (not reducing or data.get('tdMode') != 'cross' or position is None
                        or not 0 < Decimal(data['sz']) <= Decimal(position['pos'])):
                    return self.ok([dict(clOrdId=client_id, ordId='', sCode='51169', sMsg='not a valid reduce close')])
            order = {**data, "ordId": str(len(self.accepted) + 1), "state": "live", "accFillSz": "0",
                     "uTime": millis(), "cTime": millis(), "tradeId": "", "fillSz": "0", "fillPx": "0"}
            self.orders[client_id] = order
            if self.quote_activity:
                self.quote_activity.accept(order)
            self.accepted.append(copy.deepcopy(order))
            if self.accepted_response_lost:
                self.accepted_response_lost -= 1
                observed['injected_accepted_response_lost'] = True
                # The venue has accepted the physical POST, but neither its
                # HTTP ACK nor a private order event reaches the client.
                return web.json_response({'code': '50013', 'msg': 'Response unavailable'}, status=503)
            if self.enforce_post_only and data.get('ordType') == 'post_only':
                movement = self.post_only_move_on_submit.get(data['instId'])
                if movement and data['side'] == 'sell':
                    self.book_overrides[data['instId']] = self.post_only_move_on_submit.pop(data['instId'])
                book = self.book(data['instId'])
                opposite = book['asks' if data['side'] == 'buy' else 'bids']
                crosses = opposite and (Decimal(data['px']) >= Decimal(opposite[0][0]) if data['side'] == 'buy'
                                        else Decimal(data['px']) <= Decimal(opposite[0][0]))
                if crosses:
                    order.update(state='canceled', uTime=millis(), cancelSource='31',
                                 cancelSourceReason='This post-only order will take liquidity from maker orders.')
                    if self.quote_activity:
                        self.quote_activity.terminal(client_id, 'canceled')
                    await self.broadcast(self.event(order))
                    await self.release.wait()
                    return self.ok([dict(clOrdId=client_id, ordId=order['ordId'], sCode='0', sMsg='')])
            if market_close:
                if self.cleanup_status_not_found_reads:
                    self.cleanup_hidden_status_reads[client_id] = self.cleanup_status_not_found_reads
                self.cleanup_close_id = client_id
                if self.cleanup_barrier == 'accepted':
                    await self.cleanup_wait('accepted')
                # A market order has no wire px; the simulator executes at its
                # independent current reference and uses the ordinary fill/WS path.
                order['px'] = self.prices[data['instId'].removesuffix('-SWAP')]
                partial = self.cleanup_partial != 'none' and self.cleanup_partial_event is None
                amount = str(Decimal(data['sz']) * Decimal('.4')) if partial else data['sz']
                if partial:
                    self.cleanup_partial_event = dict(clOrdId=client_id, outcome=self.cleanup_partial,
                                                      closed_socket_count=len(self.sockets), ws_dropped=True, at_ms=millis())
                    await self.control({'drop_ws': True})
                await self.control({'fill': dict(clOrdId=client_id, amount=amount, fee='0.000001'), 'duplicate': True})
                if partial and self.cleanup_partial == 'canceled':
                    order.update(state='canceled', uTime=millis(), cancel_terminal_ms=millis())
            cancel_before_ack = self.cancel_before_ack and self.cancel_before_ack_remaining != 0
            if cancel_before_ack:
                if self.cancel_before_ack_remaining is not None:
                    self.cancel_before_ack_remaining -= 1
                order.update(state="canceled", uTime=millis())
                if self.quote_activity:
                    self.quote_activity.terminal(client_id, 'canceled')
                await self.broadcast(self.event(order))
            await self.release.wait()
            await self.broadcast(self.event(order))
            return self.ok([{"clOrdId": client_id, "ordId": order["ordId"], "sCode": "0", "sMsg": ""}])
        if path in ("/api/v5/trade/cancel-order", "/api/v5/trade/cancel-batch-orders"):
            received_ns = time.monotonic_ns()
            if self.cancel_delay_seconds:
                await asyncio.sleep(self.cancel_delay_seconds)
            result = []
            for item in data if isinstance(data, list) else [data]:
                order = self.orders.get(item["clOrdId"])
                if order is None:
                    result.append({"clOrdId": item["clOrdId"], "ordId": "", "sCode": "51400", "sMsg": "Order does not exist"})
                    continue
                spec = self.fill_on_cancel.pop(order['clOrdId'], None)
                was_live = order['state'] in ('live', 'partially_filled')
                if spec and order['state'] in ('live', 'partially_filled'):
                    await self.control({'fill': spec, 'duplicate': True})
                if order["state"] != "filled":
                    order.setdefault('cancel_terminal_ms', millis())
                    order.update(state="canceled", uTime=millis())
                    if (was_live and self.cancel_pending_visibility_reads and Decimal(order['accFillSz']) == 0
                            and len(self.cancel_pending_visibility) < 8):
                        self.cancel_pending_visibility.setdefault(order['clOrdId'], self.cancel_pending_visibility_reads)
                    if self.quote_activity and was_live:
                        self.quote_activity.terminal(order['clOrdId'], 'canceled', request_ns=received_ns)
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
        if 'submission_busy' in data:
            if type(data['submission_busy']) is not bool:
                return web.json_response({'error': 'invalid_submission_busy'}, status=400)
            self.submission_busy = data['submission_busy']
        if 'accepted_response_lost' in data:
            count = data['accepted_response_lost']
            if type(count) is not int or not 0 <= count <= 10:
                return web.json_response({'error': 'invalid_accepted_response_lost'}, status=400)
            self.accepted_response_lost = count
        if 'cleanup_partial' in data:
            if data['cleanup_partial'] not in ('none', 'live', 'canceled'):
                return web.json_response({'error': 'invalid_cleanup_partial'}, status=400)
            self.cleanup_partial = data['cleanup_partial']
            self.cleanup_partial_event = None
        if 'credential_generations' in data:
            generations = data['credential_generations']
            if not isinstance(generations, list) or any(name not in FIXTURE_KEYS for name in generations):
                return web.json_response({'error': 'invalid_fixture_credential_generations'}, status=400)
            self.credential_generations = set(generations)
        if 'cleanup_barrier' in data:
            mode = data['cleanup_barrier']
            if mode not in ('none', 'accepted', 'verified_fill'):
                return web.json_response({'error': 'invalid_cleanup_barrier'}, status=400)
            self.cleanup_barrier = mode
            if mode == 'none':
                self.cleanup_release.set()
            else:
                self.cleanup_close_id = None
                self.cleanup_release.clear()
        if 'cleanup_market_closes' in data:
            if type(data['cleanup_market_closes']) is not bool:
                return web.json_response({'error': 'invalid_cleanup_market_closes'}, status=400)
            self.cleanup_market_closes = data['cleanup_market_closes']
        if 'cleanup_status_not_found_reads' in data:
            count = data['cleanup_status_not_found_reads']
            if type(count) is not int or not 0 <= count <= 5:
                return web.json_response({'error': 'invalid_cleanup_status_not_found_reads'}, status=400)
            self.cleanup_status_not_found_reads = count
        if 'cancel_before_ack_limit' in data:
            limit = data['cancel_before_ack_limit']
            if type(limit) is not int or not 1 <= limit <= 200:
                return web.json_response({'error': 'invalid_cancel_before_ack_limit'}, status=400)
            self.cancel_before_ack_remaining = limit
        elif 'cancel_before_ack' in data:
            self.cancel_before_ack_remaining = None
        if 'mm_activity' in data and bool(data['mm_activity']) != bool(self.quote_activity):
            if self.orders:
                return web.json_response({'error': 'configure_activity_before_orders'}, status=409)
            try:
                from .load_mm_activity import VenueQuotes
            except ImportError:  # Standalone fixture inside the isolated container.
                from load_mm_activity import VenueQuotes
            self.quote_activity = VenueQuotes() if data['mm_activity'] else None
        if 'market_diagnostics' in data:
            self.market_diagnostics = MarketDiagnostics() if data['market_diagnostics'] else None
        if 'fill_on_cancel' in data:
            # One-shot deterministic cancellation race, using the normal fill
            # ledger/balance/WS implementation rather than inventing exposure.
            self.fill_on_cancel = {spec['clOrdId']: spec for spec in data['fill_on_cancel']}
        if 'cancel_delay_seconds' in data:
            delay = float(data['cancel_delay_seconds'])
            assert 0 <= delay <= 10
            self.cancel_delay_seconds = delay
        if 'cancel_pending_visibility_reads' in data:
            count = data['cancel_pending_visibility_reads']
            if type(count) is not int or not 0 <= count <= 5:
                return web.json_response({'error': 'invalid_cancel_pending_visibility_reads'}, status=400)
            # Arm future native cancellations, including orders created after a
            # Random rollover. Selecting current live IDs here races that rollover.
            self.cancel_pending_visibility_reads = count
            self.cancel_pending_visibility = {}
            self.cancel_pending_visibility_served = 0
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
        if 'enforce_post_only' in data:
            self.enforce_post_only = bool(data['enforce_post_only'])
        if 'post_only_move_on_submit' in data:
            self.post_only_move_on_submit = copy.deepcopy(data['post_only_move_on_submit'])
        if "stale_instruments" in data:
            self.stale_instruments = set(data["stale_instruments"])
        if 'rest_stale_instruments' in data:
            self.rest_stale_instruments = set(data['rest_stale_instruments'])
        if 'rest_failed_instruments' in data:
            self.rest_failed_instruments = set(data['rest_failed_instruments'])
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
        if "initial_positions" in data:
            # Fixture setup only: represent exposure held before this process.
            assert not self.orders and not self.fills and not self.positions
            for position in data["initial_positions"]:
                assert Decimal(position["pos"]) > 0 and position["posSide"] in ("long", "short")
                self.positions[(position["instId"], position["posSide"])] = copy.deepcopy(position)
            self.mark_positions()
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
            perpetual = order["instId"].endswith("-SWAP")
            closing = perpetual and (order.get('posSide'), order['side']) in (('long', 'sell'), ('short', 'buy'))
            held = self.positions.get((order['instId'], order.get('posSide')))
            if closing and (held is None or not 0 < amount <= Decimal(held['pos'])):
                return web.json_response({'error': 'position_not_closeable'}, status=409)
            total = Decimal(order["accFillSz"]) + amount
            assert total <= Decimal(order["sz"])
            order.update(accFillSz=str(total), state="filled" if total == Decimal(order["sz"]) else "partially_filled", uTime=millis())
            if self.quote_activity and order['state'] == 'filled':
                self.quote_activity.terminal(order['clOrdId'], 'filled')
            perpetual = order["instId"].endswith("-SWAP")
            fill = {"instType": "SWAP" if perpetual else "SPOT", "instId": order["instId"], "tradeId": str(len(self.fills) + 1),
                    "clOrdId": order["clOrdId"], "ordId": order["ordId"], "fillPx": order["px"],
                    "fillSz": str(amount), "feeCcy": "USDT", "fee": str(-Decimal(spec["fee"])), "ts": millis(),
                    "side": order["side"], "posSide": order.get("posSide", "long"), "billId": str(len(self.fills) + 1)}
            self.fills.append(fill)
            # A later terminal status message must describe the actual last
            # trade, not inherit the upstream sample's blank/zero fee fields.
            order.update(tradeId=fill['tradeId'], fillSz=fill['fillSz'], fillPx=fill['fillPx'],
                         fillFee=fill['fee'], fillFeeCcy=fill['feeCcy'], fillTime=fill['ts'])
            if perpetual:
                side = order.get("posSide", "long")
                position = self.positions.setdefault((order["instId"], side), {"instId": order["instId"], "posSide": side, "pos": "0",
                                                                               "avgPx": order["px"], "notionalUsd": "0", "lever": "1", "upl": "0", "mgnMode": "cross"})
                previous = Decimal(position["pos"])
                if not closing:
                    position["avgPx"] = str((previous * Decimal(position["avgPx"]) + amount * Decimal(order["px"])) / (previous + amount))
                position["pos"] = str(previous - amount if closing else previous + amount)
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
