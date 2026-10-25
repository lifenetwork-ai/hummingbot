"""Admin adapters on the existing aiohttp service."""
import asyncio
import gzip
import hashlib
import hmac
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from aiohttp import BasicAuth, web
from cachetools import TTLCache
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from hummingbot.cli.admin_models import (
    COMMAND_REQUEST,
    BalancesResponse,
    CommandRequest,
    CommandResponse,
    DailyQuery,
    DailyResponse,
    FillsPage,
    FillsQuery,
    MarketQuery,
    OrderDetail,
    OrderDetailQuery,
    OrdersPage,
    OrdersQuery,
    PerformanceResponse,
    PositionsResponse,
    ProfilesResponse,
    ReconciliationReceipt,
    ReconciliationRequest,
    ReconciliationResponse,
    ReferencePairsQuery,
    ReferencePairsResponse,
    StatusResponse,
)
from hummingbot.cli.api_documentation import api_operation
from hummingbot.client.config.mm_profiles import MMProfilesConfig
from hummingbot.model.admin_store import AdminStore
from hummingbot.model.bot_state_store import ControlConflict

ADMIN_PREFIX = "/api/v1/admin"
ADMIN_ENDPOINTS = (("GET", "/profiles", "profiles"), ("GET", "/status", "status"),
                   ("POST", "/commands", "commands"), ("GET", "/commands/{command_id}", "command"),
                   ("GET", "/openapi.json", "openapi"), ("GET", "/docs", "docs"),
                   ("GET", "/docs/assets/{asset}", "asset"))
ADMIN_ENDPOINTS += (("GET", "/orders", "orders"), ("GET", "/orders/{order_id}", "order"), ("GET", "/fills", "fills"))
ADMIN_ENDPOINTS += (("GET", "/reference-pairs", "reference_pairs"),)
ADMIN_ENDPOINTS += (("GET", "/balances", "balances"), ("GET", "/positions", "positions"), ("GET", "/performance", "performance"))
ADMIN_ENDPOINTS += (("GET", "/statistics/daily", "daily"),)
ADMIN_ENDPOINTS += (("POST", "/reconciliation/check", "reconciliation_check"), ("GET", "/reconciliation", "reconciliation"))


class APIUnavailable(RuntimeError):
    pass


@web.middleware
async def admin_errors(request, handler):
    if not request.path.startswith(ADMIN_PREFIX + "/"):
        return await handler(request)
    try:
        return await handler(request)
    except ControlConflict:
        return web.json_response({"error": "control_or_idempotency_conflict"}, status=409)
    except (ValidationError, ValueError):
        return web.json_response({"error": "validation_failed"}, status=422)
    except (APIUnavailable, SQLAlchemyError, asyncio.TimeoutError):
        return web.json_response({"error": "unavailable"}, status=503)
    except web.HTTPException as error:
        return web.json_response({"error": {400: "invalid_json", 404: "not_found", 415: "unsupported_media_type"}.get(
            error.status, "http_error")}, status=error.status)
    except Exception as error:
        logging.getLogger(__name__).error("Admin handler failed type=%s", type(error).__name__)
        return web.json_response({"error": "internal_error"}, status=500)


class AdminAPI:
    def __init__(self, host, settings, *, store=None, catalogue=None):
        self.host = host
        self.settings = settings
        self.store_override = store
        self.catalogue = catalogue
        self.reference_observed_at = None
        self._reference_pairs = []
        self._reference_retry_at = 0
        self._reference_lock = asyncio.Lock()
        self._reference_provider = None
        self._daily_cache = TTLCache(maxsize=32, ttl=settings.statistics_cache_ttl_seconds)
        self._daily_lock = asyncio.Lock()

    def register(self, app):
        app.middlewares.append(admin_errors)
        app.on_cleanup.append(self.close)
        for method, path, name in ADMIN_ENDPOINTS:
            if method == "GET":
                app.router.add_get(ADMIN_PREFIX + path, getattr(self, name))
            else:
                app.router.add_route(method, ADMIN_PREFIX + path, getattr(self, name))

    async def close(self, app):
        if self._reference_provider is not None:
            for connector in self._reference_provider._non_trading_connectors.values():
                await connector.stop_network()

    def reference_source(self):
        # Public instrument discovery uses the existing connector parser and
        # throttler. It does not subscribe to books or construct trading keys.
        if self._reference_provider is None:
            from hummingbot.data_feed.market_data_provider import MarketDataProvider
            self._reference_provider = MarketDataProvider({})
        return self._reference_provider.get_connector_with_fallback("okx")

    async def reference_catalogue(self):
        async with self._reference_lock:
            now = time.time()
            if (self.reference_observed_at is None or now - self.reference_observed_at >= 60) and now >= self._reference_retry_at:
                try:
                    source = self.reference_source()
                    await asyncio.wait_for(source._update_trading_rules(), timeout=5)
                    self._reference_pairs = sorted(pair for pair in source.trading_rules if pair.endswith("-USDT"))
                    self.reference_observed_at = time.time()
                except Exception as error:
                    logging.getLogger(__name__).warning("Reference metadata unavailable type=%s", type(error).__name__)
                    self._reference_retry_at = time.time() + 5
            return dict(pairs=list(self._reference_pairs), observed_at=self.reference_observed_at,
                        stale=self.reference_observed_at is None or time.time() - self.reference_observed_at >= 60)

    def storage(self):
        if self.store_override is not None:
            return self.store_override
        if self.host is None or self.host.store is None:
            raise APIUnavailable("Control store is not ready")
        return AdminStore(self.host.store)

    async def deployed_profiles(self):
        if self.catalogue is None:
            from hummingbot.client.settings import CONF_DIR_PATH
            path = CONF_DIR_PATH / "life_mm_profiles.yaml"
            if not path.is_file():
                path = Path(__file__).resolve().parents[2] / "deploy/cloudrun/conf/life_mm_profiles.yaml"
            try:
                self.catalogue = await MMProfilesConfig.load(path)
            except Exception:
                raise APIUnavailable("Deployed profiles are unavailable") from None
        return self.catalogue

    @staticmethod
    def command_response(row):
        return CommandResponse.model_validate({key: value for key, value in row.items() if key in CommandResponse.model_fields})

    @staticmethod
    def query(request, model):
        if any(len(request.query.getall(key)) != 1 for key in request.query):
            raise ValueError("Query parameters cannot be repeated")
        return model.model_validate(dict(request.query))

    @api_operation(operation_id="listProfiles", summary="Read deployed MM defaults and limits", tag="Runtime", response_model=ProfilesResponse)
    async def profiles(self, request):
        model = ProfilesResponse(catalogue=await self.deployed_profiles(), market_modes=["spot", "perpetual", "both"],
                                 reporting_timezone=self.settings.reporting_timezone)
        return web.json_response(model.model_dump(mode="json"))

    @api_operation(operation_id="listReferencePairs", summary="Search supported OKX spot USDT references, including pairs outside YAML",
                   tag="Runtime", response_model=ReferencePairsResponse, query_model=ReferencePairsQuery)
    async def reference_pairs(self, request):
        query = self.query(request, ReferencePairsQuery)
        catalogue = await self.reference_catalogue()
        pairs = [pair for pair in catalogue["pairs"] if query.search.upper() in pair and pair > query.after]
        model = ReferencePairsResponse(items=pairs[:query.limit],
                                       next_after=pairs[query.limit - 1] if len(pairs) > query.limit else None,
                                       observed_at=catalogue["observed_at"], stale=catalogue["stale"])
        return web.json_response(model.model_dump(mode="json"))

    @api_operation(operation_id="getRuntimeStatus", summary="Read requested and observed bot state", tag="Runtime", response_model=StatusResponse)
    async def status(self, request):
        if self.host is None or self.host.store is None:
            raise APIUnavailable("Control store is not ready")
        row = await asyncio.to_thread(self.host.store.read)
        if row is None:
            raise APIUnavailable("Bot identity is not ready")
        snapshot = row.get("status") or {}
        age = float(row["heartbeat_age_s"]) if row.get("heartbeat_age_s") is not None else None
        owner_available = bool(row.get("owner_uuid")) and age is not None and 0 <= age <= 5
        session = {key: value for key, value in row["mm_session"].items() if key != "catalogue"} if row.get("mm_session") else None
        now = float(row["database_now"]) if row.get("database_now") is not None else None
        remaining = max(0, session["expires_at"] - now) if session and session.get("expires_at") is not None and now is not None else None
        model = StatusResponse(bot_id=row["bot_id"], desired_state=row["desired_state"], control_version=row["control_version"],
                               acknowledged_version=row["acknowledged_version"], owner_available=owner_available,
                               heartbeat_age_seconds=age, running=owner_available and bool(snapshot.get("running")),
                               phase=snapshot.get("phase", "unknown") if owner_available else "owner_unavailable",
                               session=session, observed_session=snapshot.get("mm_session"), markets=snapshot.get("mm_markets") or [],
                               server_time=datetime.fromtimestamp(now, tz=timezone.utc) if now is not None else None,
                               time_remaining_seconds=remaining, error=snapshot.get("error"))
        return web.json_response(model.model_dump(mode="json"))

    @api_operation(operation_id="submitCommand", summary="Accept an idempotent start, switch or stop", tag="Control",
                   request_model=CommandRequest, response_model=CommandResponse, status=202, headers=("Idempotency-Key",),
                   examples={
                       "fixed_profile": {"summary": "Run one specific profile", "value": {
                           "action": "start", "expected_version": 0, "market_mode": "spot", "selection": "fixed",
                           "profile_id": "follow_market", "duration_seconds": 3600}},
                       "random_profiles": {"summary": "Random profile selection every ten minutes", "value": {
                           "action": "start", "expected_version": 0, "market_mode": "both", "selection": "random",
                           "profile_ids": ["follow_market", "sideways", "follow_token"],
                           "duration_seconds": 3600, "step_duration_seconds": 600}},
                       "runtime_reference": {"summary": "Follow a supported reference pair outside the YAML defaults", "value": {
                           "action": "start", "expected_version": 0, "market_mode": "perpetual", "selection": "fixed",
                           "profile_id": "follow_token", "references": [{"pair": "AVAX-USDT", "weight": "1"}],
                           "duration_seconds": 3600}},
                   })
    async def commands(self, request):
        if request.content_type != "application/json":
            raise web.HTTPUnsupportedMediaType()
        try:
            payload = await request.json()
        except (json.JSONDecodeError, UnicodeError):
            raise web.HTTPBadRequest() from None
        command = COMMAND_REQUEST.validate_python(payload)
        key = request.headers.get("Idempotency-Key", "")
        if not 1 <= len(key) <= 128 or any(not 33 <= ord(character) <= 126 for character in key):
            raise ValueError("Idempotency-Key is required")
        catalogue = await self.deployed_profiles() if command.action != "stop" else None
        session_request = command.session_request(catalogue) if command.action != "stop" else None
        receipt = await asyncio.to_thread(self.storage().command, key, payload, catalogue=catalogue, request=session_request)
        return web.json_response(self.command_response(receipt).model_dump(mode="json"), status=202)

    @api_operation(operation_id="getCommand", summary="Read accepted, applying or final command outcome", tag="Control",
                   response_model=CommandResponse, path_parameters=("command_id",))
    async def command(self, request):
        command_id = request.match_info["command_id"]
        if len(command_id) > 128:
            raise ValueError("Command identity is too long")
        receipt = await asyncio.to_thread(self.storage().command_by_id, command_id)
        if receipt is None or receipt["action"] not in ("start", "switch", "stop"):
            raise web.HTTPNotFound()
        return web.json_response(self.command_response(receipt).model_dump(mode="json"))

    @api_operation(operation_id="listOrders", summary="Read bounded bot-owned order history", tag="History",
                   response_model=OrdersPage, query_model=OrdersQuery)
    async def orders(self, request):
        query = self.query(request, OrdersQuery)
        page = await asyncio.to_thread(self.storage().orders, **query.filters())
        return web.json_response(OrdersPage.model_validate(page).model_dump(mode="json"))

    @api_operation(operation_id="getOrder", summary="Read one recorded order and its bounded status timeline", tag="History",
                   response_model=OrderDetail, query_model=OrderDetailQuery, path_parameters=("order_id",))
    async def order(self, request):
        order_id = request.match_info["order_id"]
        if len(order_id) > 128:
            raise ValueError("Order identity is too long")
        query = self.query(request, OrderDetailQuery)
        row = await asyncio.to_thread(self.storage().order, order_id, timeline_limit=query.timeline_limit)
        if row is None:
            raise web.HTTPNotFound()
        return web.json_response(OrderDetail.model_validate(row).model_dump(mode="json"))

    @api_operation(operation_id="listFills", summary="Read bounded fills, original fees and conversion coverage", tag="History",
                   response_model=FillsPage, query_model=FillsQuery)
    async def fills(self, request):
        query = self.query(request, FillsQuery)
        page = await asyncio.to_thread(self.storage().fills, **query.filters())
        return web.json_response(FillsPage.model_validate(page).model_dump(mode="json"))

    @api_operation(operation_id="getBalances", summary="Read account cash and equity once, with observation freshness", tag="Reporting",
                   response_model=BalancesResponse)
    async def balances(self, request):
        row = await asyncio.to_thread(self.storage().observations)
        model = BalancesResponse(**{key: row[key] for key in ("accounts", "server_time", "owner_available")})
        return web.json_response(model.model_dump(mode="json"))

    @api_operation(operation_id="getPositions", summary="Read last observed actual perpetual exposure, including after stop", tag="Reporting",
                   response_model=PositionsResponse, query_model=MarketQuery)
    async def positions(self, request):
        query = self.query(request, MarketQuery)
        row = await asyncio.to_thread(self.storage().observations)
        markets = []
        for market in (("spot", "perpetual") if query.market == "all" else (query.market,)):
            observation = next((item for item in row["positions"] if item["market"] == market), None)
            markets.append({**(observation or {}), "market": market, "connector": "okx" if market == "spot" else "okx_perpetual",
                            "coverage": "not_applicable" if market == "spot" else ("observed" if observation else "unavailable")})
        model = PositionsResponse(markets=markets, server_time=row["server_time"], owner_available=row["owner_available"])
        return web.json_response(model.model_dump(mode="json"))

    @api_operation(operation_id="getPerformance", summary="Read separate cached V2 controller performance; daily recorded fees/funding are linked", tag="Reporting",
                   response_model=PerformanceResponse, query_model=MarketQuery)
    async def performance(self, request):
        query = self.query(request, MarketQuery)
        row = await asyncio.to_thread(self.storage().observations)
        markets = []
        for market in (("spot", "perpetual") if query.market == "all" else (query.market,)):
            observations = [item for item in row["performance"] if item["market"] == market]
            markets.append(dict(market=market, coverage="observed" if observations else "unavailable",
                                funding_status="not_applicable" if market == "spot" else "observed_only",
                                observations=observations, daily_statistics_url=f"{ADMIN_PREFIX}/statistics/daily?market={market}"))
        model = PerformanceResponse(markets=markets, server_time=row["server_time"], owner_available=row["owner_available"])
        return web.json_response(model.model_dump(mode="json"))

    @api_operation(operation_id="getDailyStatistics", summary="Read separate market event-day fees/volume/funding and shared last-observed account balances",
                   tag="Reporting", response_model=DailyResponse, query_model=DailyQuery)
    async def daily(self, request):
        query = self.query(request, DailyQuery)
        key = query.model_dump_json(), self.settings.reporting_timezone
        # One loop owns this small read-only cache. Coalesce misses so FE polling
        # cannot run the same large aggregate concurrently. Never cache failures
        # or alter observation timestamps; trading/control do not read this cache.
        async with self._daily_lock:
            body = self._daily_cache.get(key)
            if body is None:
                row = await asyncio.to_thread(self.storage().daily, **query.model_dump(), reporting_timezone=self.settings.reporting_timezone)
                body = DailyResponse.model_validate(row).model_dump(mode="json")
                self._daily_cache[key] = body
        return web.json_response(body)

    @staticmethod
    def check_response(row):
        result = {key: value for key, value in (row.get("result") or {}).items() if key != "lease"}
        return ReconciliationReceipt(**{key: row[key] for key in ("id", "status", "payload", "created_at", "updated_at", "error")}, report=result)

    @api_operation(operation_id="requestReconciliation", summary="Request one bounded observational check; does not repair or control trading",
                   tag="Reconciliation", request_model=ReconciliationRequest, response_model=ReconciliationReceipt,
                   status=202, headers=("Idempotency-Key",))
    async def reconciliation_check(self, request):
        if request.content_type != "application/json":
            raise web.HTTPUnsupportedMediaType()
        try:
            payload = await request.json()
        except (json.JSONDecodeError, UnicodeError):
            raise web.HTTPBadRequest() from None
        ReconciliationRequest.model_validate(payload)
        row = await asyncio.to_thread(self.storage().request_check, request.headers.get("Idempotency-Key", ""), payload)
        return web.json_response(self.check_response(row).model_dump(mode="json"), status=202)

    @api_operation(operation_id="getReconciliation", summary="Read the latest stored check outcome without contacting OKX", tag="Reconciliation",
                   response_model=ReconciliationResponse)
    async def reconciliation(self, request):
        row = await asyncio.to_thread(self.storage().latest_check)
        check = self.check_response(row) if row else None
        model = ReconciliationResponse(check=check, report=check.report if check else {})
        return web.json_response(model.model_dump(mode="json"))

    @api_operation(operation_id="getOpenAPI", summary="Read the generated API contract", tag="Documentation", response_model=dict)
    async def openapi(self, request):
        from hummingbot.cli.swagger import SPEC_PATH
        try:
            spec = await asyncio.to_thread(SPEC_PATH.read_text)
        except OSError:
            raise APIUnavailable("Packaged OpenAPI schema is unavailable") from None
        return web.Response(text=spec, content_type="application/json")

    @api_operation(operation_id="getSwaggerUI", summary="Open the protected interactive API documentation", tag="Documentation",
                   response_model=str, response_content_type="text/html")
    async def docs(self, request):
        page = f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Hummingbot Admin API</title>
<link rel="stylesheet" href="{ADMIN_PREFIX}/docs/assets/swagger-ui.css"></head>
<body><div id="swagger-ui"></div>
<script src="{ADMIN_PREFIX}/docs/assets/swagger-ui-bundle.js"></script>
<script>SwaggerUIBundle({{
  url: "{ADMIN_PREFIX}/openapi.json", dom_id: "#swagger-ui",
  persistAuthorization: false, validatorUrl: null, withCredentials: false,
  supportedSubmitMethods: ["get", "post"]
}});</script></body></html>'''
        return web.Response(text=page, content_type="text/html")

    @api_operation(operation_id="getSwaggerAsset", summary="Read bundled Swagger UI assets", tag="Documentation",
                   response_model=bytes, path_parameters=("asset",), response_content_type="application/octet-stream")
    async def asset(self, request):
        name = request.match_info["asset"]
        if name not in ("swagger-ui-bundle.js", "swagger-ui.css"):
            raise web.HTTPNotFound()
        path = Path(__file__).parent / "static/swagger_ui" / (name + ".gz")
        try:
            content = await asyncio.to_thread(path.read_bytes)
        except OSError:
            raise APIUnavailable("Packaged Swagger UI asset is unavailable") from None
        headers = {"Vary": "Accept-Encoding"}
        if "gzip" in request.headers.get("Accept-Encoding", ""):
            headers["Content-Encoding"] = "gzip"
        else:
            content = await asyncio.to_thread(gzip.decompress, content)
        return web.Response(body=content, headers=headers,
                            content_type="application/javascript" if name.endswith(".js") else "text/css")


def admin_middleware(settings):
    expected_user = hashlib.sha256(settings.username.encode("utf-8")).digest()
    expected_password = hashlib.sha256(settings.password.encode("utf-8")).digest()

    @web.middleware
    async def authenticate(request, handler):
        if request.path != ADMIN_PREFIX and not request.path.startswith(ADMIN_PREFIX + "/"):
            return await handler(request)
        if not settings.enabled:
            return web.json_response({"error": "admin_api_disabled"}, status=404)
        origin = request.headers.get("Origin")
        wildcard = "*" in settings.cors_origins
        if origin:
            try:
                parsed = urlsplit(origin)
                same_origin = parsed.netloc == request.host and parsed.scheme in ("http", "https")
            except ValueError:
                same_origin = False
            if not wildcard and origin not in settings.cors_origins and not same_origin:
                return web.json_response({"error": "origin_not_allowed"}, status=403)
        if request.method == "OPTIONS":
            headers = {value.strip().lower() for value in request.headers.get("Access-Control-Request-Headers", "").split(",") if value.strip()}
            method = request.headers.get("Access-Control-Request-Method", "")
            if not origin or method not in ("GET", "POST", "HEAD") or not headers <= {"authorization", "content-type", "idempotency-key"}:
                return web.json_response({"error": "preflight_not_allowed"}, status=403)
            response = web.Response(status=204, headers={
                "Access-Control-Allow-Methods": "GET, POST, HEAD, OPTIONS",
                "Access-Control-Allow-Headers": "Authorization, Content-Type, Idempotency-Key",
            })
        else:
            try:
                auth = BasicAuth.decode(request.headers.get("Authorization", ""), encoding="utf-8")
                user_ok = hmac.compare_digest(hashlib.sha256(auth.login.encode("utf-8")).digest(), expected_user)
                password_ok = hmac.compare_digest(hashlib.sha256(auth.password.encode("utf-8")).digest(), expected_password)
                authenticated = user_ok & password_ok
            except ValueError:
                authenticated = False
            if not authenticated:
                response = web.json_response({"error": "authentication_required"}, status=401,
                                             headers={"WWW-Authenticate": 'Basic realm="Hummingbot admin", charset="UTF-8"'})
            else:
                response = await handler(request)
        response.headers["Cache-Control"] = "no-store"
        if origin:
            response.headers["Access-Control-Allow-Origin"] = "*" if wildcard else origin
            if not wildcard:
                response.headers["Access-Control-Allow-Credentials"] = "true"
            response.headers["Vary"] = ", ".join(filter(None, (response.headers.get("Vary"), "Origin")))
        return response
    return authenticate
