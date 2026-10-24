from contextlib import asynccontextmanager
from unittest.mock import patch
from urllib.parse import urlsplit

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from hummingbot.connector.derivative.okx_perpetual.okx_perpetual_derivative import OkxPerpetualDerivative
from hummingbot.connector.exchange.okx.okx_exchange import OkxExchange


@asynccontextmanager
async def transport(monkeypatch):
    from hummingbot.core.web_assistant.connections.connections_factory import ConnectionsFactory
    connections = object.__new__(ConnectionsFactory)
    connections._shared_client = None
    connections._ws_independent_session = None
    rest, routes, logins = [], [], []

    async def handle(request):
        if request.path.startswith("/ws/"):
            socket = web.WebSocketResponse()
            await socket.prepare(request)
            async for message in socket:
                if message.data == "ping":
                    await socket.send_str("pong")
                    continue
                payload = message.json()
                if payload.get("op") == "login":
                    logins.append(payload["args"][0]["apiKey"])
                    await socket.send_json({"event": "login", "code": "0"})
            return socket
        rest.append((request.method, request.path, dict(request.headers)))
        return web.json_response({"code": "0", "data": [{"ts": "1700000000000"}]})

    app = web.Application()
    app.router.add_route("*", "/{path:.*}", handle)
    async with TestServer(app) as server:
        def redirect(url):
            routes.append(url)
            parsed = urlsplit(url)
            local = str(server.make_url(parsed.path + ("?" + parsed.query if parsed.query else "")))
            return local.replace("http://", "ws://") if parsed.scheme == "wss" else local
        with patch("hummingbot.core.web_assistant.connections.rest_connection.endpoint", side_effect=redirect), \
                patch("hummingbot.core.web_assistant.connections.ws_connection.endpoint", side_effect=redirect), \
                patch("hummingbot.core.web_assistant.web_assistants_factory.ConnectionsFactory", return_value=connections):
            try:
                yield rest, routes, logins
            finally:
                await connections.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["spot", "perpetual"])
@pytest.mark.parametrize("mode,site,host", [("live", "www", "ws.okx.com"), ("demo", "www", "wspap.okx.com"),
                                            ("demo", "app", "wsuspap.okx.com"), ("live", "my", "wseea.okx.com")])
async def test_real_connector_final_rest_headers_and_websocket_reconnect_route(monkeypatch, kind, mode, site, host):
    monkeypatch.setenv("OKX_TRADING_ENV", mode)
    monkeypatch.setenv("OKX_SITE", site)
    monkeypatch.setenv("STORAGE_BACKEND", "file")
    async with transport(monkeypatch) as (requests, routes, logins):
        connector = (OkxExchange("offline-key", "offline-secret", "offline-pass", trading_pairs=["LIFE-USDT"], trading_required=False)
                     if kind == "spot" else OkxPerpetualDerivative(okx_perpetual_api_key="offline-key",
                                                                   okx_perpetual_secret_key="offline-secret", okx_perpetual_passphrase="offline-pass",
                                                                   trading_pairs=["LIFE-USDT"], trading_required=False))
        connector._time_synchronizer.add_time_offset_ms_sample(0)
        for path in ("/api/v5/public/instruments", "/api/v5/account/config", "/api/v5/account/balance",
                     "/api/v5/account/set-leverage", "/api/v5/trade/order", "/api/v5/trade/cancel-order", "/api/v5/trade/fills"):
            from hummingbot.connector.exchange.okx import okx_constants as constants
            from hummingbot.core.web_assistant.connections.data_types import RESTMethod
            private = "/public/" not in path
            method = RESTMethod.POST if path.endswith(("set-leverage", "cancel-order", "trade/order")) else RESTMethod.GET
            limit = constants.OKX_ORDER_DETAILS_PATH if kind == "spot" else "GET-/api/v5/trade/order"
            await connector._api_request(path_url=path, method=method, is_auth_required=private, limit_id=limit,
                                         data={"fixture": True} if method == RESTMethod.POST else None)
        public = connector._create_order_book_data_source()
        private = connector._create_user_stream_data_source()
        for _ in range(2):
            ws = await public._connected_websocket_assistant()
            await ws.disconnect()
            if kind == "spot":
                ws = await private._connected_websocket_assistant()
            else:
                from hummingbot.connector.derivative.okx_perpetual import okx_perpetual_web_utils
                ws = await private._get_connected_websocket_assistant(okx_perpetual_web_utils.wss_linear_private_url(connector.domain))
            await ws.disconnect()
        for method, path, headers in requests:
            assert headers.get("x-simulated-trading") == ("1" if mode == "demo" else None)
            if "/public/" not in path:
                assert headers["OK-ACCESS-KEY"] == "offline-key"
                assert headers["OK-ACCESS-SIGN"]
        websocket_routes = [url for url in routes if url.startswith("wss://")]
        assert len(websocket_routes) == 4
        assert all(urlsplit(url).hostname == host and urlsplit(url).port in (None, 443) for url in websocket_routes)
        assert logins == ["offline-key", "offline-key"]
        # Close the real aiohttp sessions without starting any trading/network loop.
