"""Admin adapters on the existing aiohttp service."""
import asyncio
import contextvars
import functools
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
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from hummingbot.cli.admin_access_api import AdminAccessAPI
from hummingbot.cli.admin_daily_cache import (
    DailyStatisticsCache,
    retryable_statement_timeout,
    transient_reporting_error,
)
from hummingbot.cli.admin_database import AdminDatabaseExecutor, DatabaseBusy
from hummingbot.cli.admin_errors import error_response, request_id, validation_fields
from hummingbot.cli.admin_models import (
    COMMAND_REQUEST,
    BalancesResponse,
    CommandRequest,
    CommandResponse,
    CommandsPage,
    CommandsQuery,
    DailyQuery,
    DailyResponse,
    FillsPage,
    FillsQuery,
    LiquidityQuery,
    LiquidityResponse,
    MarketQuery,
    MMEventsPage,
    MMEventsQuery,
    OrderDetail,
    OrderDetailQuery,
    OrdersPage,
    OrdersQuery,
    PageQuery,
    PerformanceResponse,
    PositionsResponse,
    ProfilesResponse,
    ReconciliationReceipt,
    ReconciliationRequest,
    ReconciliationResponse,
    ReferencePairsQuery,
    ReferencePairsResponse,
    ServiceStatus,
    SessionConfigurationResponse,
    SessionsPage,
    StatusResponse,
)
from hummingbot.cli.admin_operations import operator_view, process_identity
from hummingbot.cli.api_documentation import api_operation
from hummingbot.client.config.mm_profiles import MMProfilesConfig
from hummingbot.core.admin_identity import AccessDenied, Principal
from hummingbot.core.control_errors import RequestError
from hummingbot.core.credential_encryption import CredentialCryptoError
from hummingbot.model.admin_store import AdminStore
from hummingbot.model.bot_state_store import ControlConflict

ADMIN_PREFIX = "/api/v1/admin"
ADMIN_IDENTITY_KEY = web.AppKey("admin_identity", object)
ADMIN_ACCESS_KEY = web.AppKey("admin_access_api", object)
# Match the existing default page size; bulk pages share the aggregate lane.
OPERATIONAL_HISTORY_LIMIT = 100
ADMIN_ENDPOINTS = (("GET", "/profiles", "profiles"), ("GET", "/status", "status"),
                   ("POST", "/commands", "commands"), ("GET", "/commands/{command_id}", "command"),
                   ("GET", "/openapi.json", "openapi"), ("GET", "/docs", "docs"),
                   ("GET", "/docs/assets/{asset}", "asset"))
ADMIN_ENDPOINTS += (("GET", "/orders", "orders"), ("GET", "/orders/{order_id}", "order"), ("GET", "/fills", "fills"))
ADMIN_ENDPOINTS += (("GET", "/reference-pairs", "reference_pairs"),)
ADMIN_ENDPOINTS += (("GET", "/balances", "balances"), ("GET", "/positions", "positions"), ("GET", "/performance", "performance"))
ADMIN_ENDPOINTS += (("GET", "/statistics/daily", "daily"),
                    ("GET", "/sessions/{session_id}/events", "session_events"),
                    ("GET", "/statistics/liquidity", "liquidity"))
ADMIN_ENDPOINTS += (("POST", "/reconciliation/check", "reconciliation_check"), ("GET", "/reconciliation", "reconciliation"))
ADMIN_ENDPOINTS += (("GET", "/sessions/{session_id}/configuration", "session_configuration"),)
ADMIN_ENDPOINTS += (("GET", "/commands", "commands_history"), ("GET", "/sessions", "sessions"),
                    ("GET", "/reconciliation/{check_id}", "reconciliation_by_id"))
ADMIN_ENDPOINTS += (("POST", "/access/login", "access_login"), ("GET", "/access/me", "access_me"),
                    ("GET", "/access/bootstrap", "access_bootstrap"), ("GET", "/access/accounts", "access_accounts"),
                    ("POST", "/access/accounts", "access_add_account"), ("POST", "/access/accounts/{account_id}", "access_change_account"),
                    ("GET", "/audit", "access_audit"))

READ_HANDLERS = frozenset({"profiles", "status", "command", "openapi", "docs", "asset", "orders", "order", "fills",
                           "reference_pairs", "balances", "positions", "performance", "daily", "session_events", "liquidity",
                           "reconciliation", "session_configuration", "commands_history", "sessions", "reconciliation_by_id",
                           "access_login", "access_me", "access_audit"})
WRITE_HANDLERS = frozenset({"commands", "reconciliation_check"})
ACCOUNT_HANDLERS = frozenset({"access_bootstrap", "access_accounts", "access_add_account", "access_change_account"})
ADMIN_PERMISSIONS = {(method, ADMIN_PREFIX + path): "read" if name in READ_HANDLERS else "write" if name in WRITE_HANDLERS else "admin"
                     for method, path, name in ADMIN_ENDPOINTS if name in READ_HANDLERS | WRITE_HANDLERS | ACCOUNT_HANDLERS}


class APIUnavailable(RuntimeError):
    pass


@web.middleware
async def admin_errors(request, handler):
    if not request.path.startswith(ADMIN_PREFIX + "/"):
        return await handler(request)
    try:
        return await handler(request)
    except AccessDenied as error:
        return error_response(request, error.code, status=error.status)
    except CredentialCryptoError as error:
        logging.getLogger(__name__).warning("Credential provider unavailable code=%s request_id=%s", error.code, request_id(request),
                                            extra={"event": "credential_provider_unavailable", "scope": "credentials", "reason_code": error.code})
        return error_response(request, error.code, status=503)
    except ControlConflict as error:
        return error_response(request, "control_or_idempotency_conflict", status=409, code=error.code,
                              retry_after_seconds=error.retry_after_seconds)
    except ValidationError as error:
        fields = validation_fields(error)
        code = "yaml_configuration_read_only" if any(f["code"] == "yaml_configuration_read_only" for f in fields) else "validation_failed"
        return error_response(request, "validation_failed", status=422, code=code, fields=fields)
    except RequestError as error:
        return error_response(request, "validation_failed", status=422, code=error.code,
                              fields=[dict(path=error.field, code=error.code)] if error.field else [])
    except ValueError:
        return error_response(request, "validation_failed", status=422)
    except (APIUnavailable, SQLAlchemyError, asyncio.TimeoutError) as error:
        # Preserve the safe response contract while distinguishing pool/DB and
        # application timeouts. Exception messages can contain SQL parameters.
        kind = type(error).__module__ + "." + type(error).__name__
        logging.getLogger(__name__).warning("Admin request unavailable type=%s request_id=%s", kind, request_id(request),
                                            extra={"event": "admin_request_unavailable", "error_type": kind})
        return error_response(request, "unavailable", status=503)
    except web.HTTPException as error:
        return error_response(request, {400: "invalid_json", 404: "not_found", 413: "request_too_large", 415: "unsupported_media_type"}.get(
            error.status, "http_error"), status=error.status)
    except Exception as error:
        logging.getLogger(__name__).error("Admin handler failed type=%s request_id=%s", type(error).__name__, request_id(request),
                                          extra={"event": "admin_request_failed", "request_id": request_id(request)})
        return error_response(request, "internal_error", status=500)


class AdminAPI(AdminAccessAPI):
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
        self._daily_cache = DailyStatisticsCache(settings.statistics_cache_ttl_seconds)
        self._daily_flights = {}
        self._daily_closing = False
        # Reserve independent operations/heavy slots within the existing
        # two-worker bound; reports cannot consume every operational slot.
        self._database_executor = AdminDatabaseExecutor()
        self._identity_service = None

    def register(self, app):
        app.middlewares.append(admin_errors)
        app.on_cleanup.append(self.close)
        app[ADMIN_ACCESS_KEY] = self
        if self.settings.enabled and self.settings.google_auth_enabled:
            from hummingbot.cli.admin_identity import AdminIdentityService
            self._identity_service = AdminIdentityService(self)
            app[ADMIN_IDENTITY_KEY] = self._identity_service
        for method, path, name in ADMIN_ENDPOINTS:
            if (method, ADMIN_PREFIX + path) not in ADMIN_PERMISSIONS:
                raise ValueError("Admin handler has no reviewed permission")
            if method == "GET":
                app.router.add_get(ADMIN_PREFIX + path, getattr(self, name))
            else:
                app.router.add_route(method, ADMIN_PREFIX + path, getattr(self, name))

    async def run_database(self, function, /, *args, heavy=False, **kwargs):
        return await self._run_database(False, function, *args, heavy=heavy, **kwargs)

    async def run_control_database(self, function, /, *args, **kwargs):
        return await self._run_database(True, function, *args, **kwargs)

    async def _run_database(self, control, function, /, *args, heavy=False, **kwargs):
        # Preserve tracing/query context just as asyncio.to_thread does. Canceling
        # an awaiter cannot release a worker that is still using a connection.
        call = functools.partial(contextvars.copy_context().run, function, *args, **kwargs)
        try:
            result = self._database_executor.submit(call, control=control, heavy=heavy)
        except DatabaseBusy:
            raise APIUnavailable('Admin database capacity is busy') from None
        return await result

    async def close(self, app):
        try:
            if self._reference_provider is not None:
                for connector in self._reference_provider._non_trading_connectors.values():
                    await connector.stop_network()
        finally:
            self._daily_closing = True
            tasks = tuple(self._daily_flights.values())
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self._daily_flights.clear()
            if self._identity_service is not None:
                await self._identity_service.close()
            await self._database_executor.close()

    def access_store(self):
        from hummingbot.model.admin_access import AdminAccessStore
        state = self.storage().store
        return AdminAccessStore(state.engine, state.bot_id)

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
        catalogue = await self.deployed_profiles()
        modes = [mode for mode in ("spot", "perpetual") if mode in catalogue.markets]
        if len(modes) == 2:
            modes.append("both")
        model = ProfilesResponse(catalogue=catalogue, market_modes=modes, catalogue_hash=catalogue.catalogue_hash(),
                                 duration_modes=['timed', 'until_stopped'] if catalogue.allow_until_stopped else ['timed'],
                                 process_identity=process_identity(self.host), reporting_timezone=self.settings.reporting_timezone)
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
        try:
            row = await self.run_database(self.host.store.read, publication=True)
        except (SQLAlchemyError, asyncio.TimeoutError):
            if not hasattr(self.host, "service_status"):
                raise APIUnavailable("Control store is unavailable") from None
            service = ServiceStatus.model_validate(self.host.service_status())
            return error_response(request, "unavailable", status=503, service=service)
        if row is None:
            raise APIUnavailable("Bot identity is not ready")
        snapshot = row.get("status") or {}
        age = float(row["heartbeat_age_s"]) if row.get("heartbeat_age_s") is not None else None
        owner_available = bool(row.get("owner_uuid")) and age is not None and 0 <= age <= 5
        session = {key: value for key, value in row["mm_session"].items() if key != "catalogue"} if row.get("mm_session") else None
        now = float(row["database_now"]) if row.get("database_now") is not None else None
        remaining = max(0, session["expires_at"] - now) if session and session.get("expires_at") is not None and now is not None else None
        service = snapshot.get("availability")
        if service is not None:
            service = {**service, "stale": not owner_available}
        outflow = snapshot.get("spot_outflow")
        if outflow is not None and not owner_available:
            outflow = {**outflow, "ready": False, "buy_allowed": False, "stale": True,
                       "reason_code": "spot_outflow_owner_unavailable", "recorded_net_outflow_quote": None,
                       "reserved_quote": None, "remaining_quote": None}
        model = StatusResponse(service=service, bot_id=row["bot_id"], desired_state=row["desired_state"], control_version=row["control_version"],
                               acknowledged_version=row["acknowledged_version"], owner_available=owner_available,
                               heartbeat_age_seconds=age, running=owner_available and bool(snapshot.get("running")),
                               phase=snapshot.get("phase", "unknown") if owner_available else "owner_unavailable",
                               session=session, observed_session=snapshot.get("mm_session"),
                               **operator_view(row, process_identity(self.host)),
                               spot_outflow=outflow,
                               server_time=datetime.fromtimestamp(now, tz=timezone.utc) if now is not None else None,
                               time_remaining_seconds=remaining, error=snapshot.get("error"),
                               funding_sync=({**snapshot["funding_sync"], **({"state": "unavailable", "stale": True, "error": "owner_unavailable"} if not owner_available else {})}
                                             if snapshot.get("funding_sync") else None))
        return web.json_response(model.model_dump(mode="json"))

    @api_operation(operation_id="submitCommand", summary="Accept an idempotent start, switch or stop", tag="Control",
                   request_model=CommandRequest, response_model=CommandResponse, status=202, headers=("Idempotency-Key",),
                   examples={
                       "fixed_profile": {"summary": "Run one specific profile", "value": {
                           "action": "start", "expected_version": 0, "market_mode": "spot", "selection": "fixed",
                           "profile_id": "follow_market", "duration_seconds": 3600}},
                       "random_profiles": {"summary": "Random rotation using the deployed YAML pool and timing", "value": {
                           "action": "start", "expected_version": 0, "market_mode": "both", "selection": "random",
                           "duration_seconds": 3600}},
                       'continuous_random': {'summary': 'Rotate configured profiles until stopped, when enabled in YAML', 'value': {
                           'action': 'start', 'expected_version': 0, 'market_mode': 'both', 'selection': 'random',
                           'duration_mode': 'until_stopped'}},
                       "configured_reference": {"summary": "Follow the token configured in the deployed YAML profile", "value": {
                           "action": "start", "expected_version": 0, "market_mode": "perpetual", "selection": "fixed",
                           "profile_id": "follow_token",
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
            raise RequestError("Idempotency-Key is required", code="idempotency_key_required", field="Idempotency-Key")
        catalogue = await self.deployed_profiles() if command.action != "stop" else None
        session_request = command.session_request(catalogue) if command.action != "stop" else None
        receipt = await self.run_control_database(self.storage().command, key, payload, catalogue=catalogue,
                                                  request=session_request, principal=request["admin_principal"],
                                                  audit_request_id=request_id(request))
        return web.json_response(self.command_response(receipt).model_dump(mode="json"), status=202)

    @api_operation(operation_id="getCommand", summary="Read accepted, applying or final command outcome", tag="Control",
                   response_model=CommandResponse, path_parameters=("command_id",))
    async def command(self, request):
        command_id = request.match_info["command_id"]
        if len(command_id) > 128:
            raise ValueError("Command identity is too long")
        receipt = await self.run_control_database(self.storage().command_by_id, command_id)
        if receipt is None or receipt["action"] not in ("start", "switch", "stop"):
            raise web.HTTPNotFound()
        return web.json_response(self.command_response(receipt).model_dump(mode="json"))

    @api_operation(operation_id="listCommands", summary="Find previous session-control receipts after reload or operator handover", tag="Control",
                   response_model=CommandsPage, query_model=CommandsQuery)
    async def commands_history(self, request):
        query = self.query(request, CommandsQuery)
        page = await self.run_database(self.storage().commands_page, heavy=query.limit > OPERATIONAL_HISTORY_LIMIT, **query.model_dump())
        model = CommandsPage(items=[self.command_response(row) for row in page["items"]], next_cursor=page["next_cursor"])
        return web.json_response(model.model_dump(mode="json"))

    @api_operation(operation_id="listSessions", summary="Find recorded sessions; legacy sessions without creation history may be absent", tag="History",
                   response_model=SessionsPage, query_model=PageQuery)
    async def sessions(self, request):
        query = self.query(request, PageQuery)
        page = await self.run_database(self.storage().sessions_page, heavy=query.limit > OPERATIONAL_HISTORY_LIMIT, **query.model_dump())
        return web.json_response(SessionsPage.model_validate(page).model_dump(mode="json"))

    @api_operation(operation_id="listOrders", summary="Read bounded bot-owned order history", tag="History",
                   response_model=OrdersPage, query_model=OrdersQuery)
    async def orders(self, request):
        query = self.query(request, OrdersQuery)
        page = await self.run_database(self.storage().orders, heavy=query.limit > OPERATIONAL_HISTORY_LIMIT, **query.filters())
        return web.json_response(text=OrdersPage.model_validate(page).model_dump_json())

    @api_operation(operation_id="getOrder", summary="Read one recorded order and its bounded status timeline", tag="History",
                   response_model=OrderDetail, query_model=OrderDetailQuery, path_parameters=("order_id",))
    async def order(self, request):
        order_id = request.match_info["order_id"]
        if len(order_id) > 128:
            raise ValueError("Order identity is too long")
        query = self.query(request, OrderDetailQuery)
        row = await self.run_database(self.storage().order, order_id,
                                      heavy=query.timeline_limit > OPERATIONAL_HISTORY_LIMIT, timeline_limit=query.timeline_limit)
        if row is None:
            raise web.HTTPNotFound()
        return web.json_response(OrderDetail.model_validate(row).model_dump(mode="json"))

    @api_operation(operation_id="listFills", summary="Read bounded fills, original fees and conversion coverage", tag="History",
                   response_model=FillsPage, query_model=FillsQuery)
    async def fills(self, request):
        query = self.query(request, FillsQuery)
        page = await self.run_database(self.storage().fills, heavy=query.limit > OPERATIONAL_HISTORY_LIMIT, **query.filters())
        return web.json_response(text=FillsPage.model_validate(page).model_dump_json())

    @api_operation(operation_id="getBalances", summary="Read account cash and equity once, with observation freshness", tag="Reporting",
                   response_model=BalancesResponse)
    async def balances(self, request):
        row = await self.run_database(self.storage().observations)
        model = BalancesResponse(**{key: row[key] for key in ("accounts", "server_time", "owner_available")})
        return web.json_response(model.model_dump(mode="json"))

    @api_operation(operation_id="getPositions", summary="Read last observed actual perpetual exposure, including after stop", tag="Reporting",
                   response_model=PositionsResponse, query_model=MarketQuery)
    async def positions(self, request):
        query = self.query(request, MarketQuery)
        row = await self.run_database(self.storage().observations)
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
        row = await self.run_database(self.storage().observations)
        markets = []
        for market in (("spot", "perpetual") if query.market == "all" else (query.market,)):
            observations = [item for item in row["performance"] if item["market"] == market]
            markets.append(dict(market=market, coverage="observed" if observations else "unavailable",
                                funding_status="not_applicable" if market == "spot" else "observed_only",
                                observations=observations, daily_statistics_url=f"{ADMIN_PREFIX}/statistics/daily?market={market}"))
        model = PerformanceResponse(markets=markets, server_time=row["server_time"], owner_available=row["owner_available"])
        return web.json_response(model.model_dump(mode="json"))

    @api_operation(operation_id="getDailyStatistics", summary="Read market-day statistics with explicitly stale last-good fallback on transient refresh failure",
                   tag="Reporting", response_model=DailyResponse, query_model=DailyQuery)
    async def daily(self, request):
        query = self.query(request, DailyQuery)
        filters = query.model_dump()
        key = self._daily_cache.key(filters, self.settings.reporting_timezone)
        if self._daily_closing:
            raise APIUnavailable('Daily reporting is closing')
        # The event loop owns cache/task admission. Physical concurrency and
        # control priority remain bounded by the existing database executor.
        body = self._daily_cache.get(key)
        if body is None:
            if key is None or self._daily_cache.ttl == 0:
                body = await self._refresh_daily(filters)
            else:
                task = self._daily_flights.get(key)
                if task is None:
                    if len(self._daily_flights) >= self._database_executor.MAX_HEAVY:
                        body = self._daily_cache.fallback(key)
                        if body is None:
                            raise APIUnavailable('Daily reporting capacity is busy')
                    else:
                        task = asyncio.create_task(self._refresh_daily(filters))
                        self._daily_flights[key] = task
                        task.add_done_callback(functools.partial(self._daily_done, key))
                if task is not None:
                    # A disconnected waiter must not release an ongoing refresh
                    # and allow the next poll to duplicate its physical DB call.
                    body = await asyncio.shield(task)
                    current = self._daily_cache.key(filters, self.settings.reporting_timezone)
                    observed_range = body['reporting_timezone'], body['start_day'], body['end_day']
                    if current != key or observed_range != key[:3]:
                        # Relative and explicit ranges may have shared a key
                        # before midnight. Re-read with this caller's filters;
                        # do not loop indefinitely on host/database clock skew.
                        body = self._daily_cache.get(current) or await self._refresh_daily(filters)
        return web.json_response(body)

    def _daily_done(self, key, task):
        if self._daily_flights.get(key) is task:
            self._daily_flights.pop(key)
        if not task.cancelled():
            task.exception()  # Retrieve failures even if every HTTP waiter disconnected.

    async def _refresh_daily(self, filters):
        for attempt in range(2):
            try:
                row = await self.run_database(self.storage().daily, heavy=True, **filters, reporting_timezone=self.settings.reporting_timezone)
            except (APIUnavailable, SQLAlchemyError, TimeoutError) as error:
                if not attempt and not self._daily_closing and retryable_statement_timeout(error):
                    # daily() has rolled back and returned its physical connection.
                    # Resubmit a complete snapshot through normal report admission;
                    # queued controls dispatch first. No deadline/pool is extended.
                    logging.getLogger(__name__).warning(
                        'Daily statistics statement timed out; retrying one complete read',
                        extra={'event': 'admin_daily_retry', 'reason_code': 'statement_timeout', 'scope': 'reporting'})
                    continue
                if not isinstance(error, APIUnavailable) and not transient_reporting_error(error):
                    raise
                key = self._daily_cache.key(filters, self.settings.reporting_timezone)
                body = self._daily_cache.fallback(key)
                if body is None:
                    raise
                logging.getLogger(__name__).warning(
                    'Daily statistics refresh unavailable; returning a bounded stale observation type=%s',
                    type(error).__name__, extra={'event': 'admin_daily_stale', 'error_type': type(error).__name__})
                return body
            else:
                break
        body = DailyResponse.model_validate(row).model_dump(mode="json")
        key = self._daily_cache.key(filters, self.settings.reporting_timezone)
        self._daily_cache.remember(key, body)
        if attempt:
            logging.getLogger(__name__).info(
                'Daily statistics retry recovered a complete observation',
                extra={'event': 'admin_daily_retry_recovered', 'scope': 'reporting'})
        return body

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
        row = await self.run_control_database(self.storage().request_check, request.headers.get("Idempotency-Key", ""), payload,
                                              principal=request["admin_principal"], audit_request_id=request_id(request))
        return web.json_response(self.check_response(row).model_dump(mode="json"), status=202)

    @api_operation(operation_id="getReconciliation", summary="Read the latest stored check outcome without contacting OKX", tag="Reconciliation",
                   response_model=ReconciliationResponse)
    async def reconciliation(self, request):
        row = await self.run_database(self.storage().latest_check)
        check = self.check_response(row) if row else None
        model = ReconciliationResponse(check=check, report=check.report if check else {})
        return web.json_response(model.model_dump(mode="json"))

    @api_operation(operation_id="getReconciliationById", summary="Read the requested check even after a newer check starts", tag="Reconciliation",
                   response_model=ReconciliationReceipt, path_parameters=("check_id",))
    async def reconciliation_by_id(self, request):
        identity = request.match_info["check_id"]
        if not 1 <= len(identity) <= 128:
            raise ValueError("Invalid check identity")
        row = await self.run_database(self.storage().check_by_id, identity)
        if row is None:
            raise web.HTTPNotFound()
        return web.json_response(self.check_response(row).model_dump(mode="json"))

    @api_operation(operation_id="getSessionEvents", summary="Read recorded session transitions and original configuration", tag="History",
                   response_model=MMEventsPage, query_model=MMEventsQuery, path_parameters=("session_id",))
    async def session_events(self, request):
        from hummingbot.model.mm_event import session_events
        query = self.query(request, MMEventsQuery)
        result = await self.run_database(session_events, self.storage().store, request.match_info["session_id"],
                                         heavy=query.limit > OPERATIONAL_HISTORY_LIMIT, **query.model_dump())
        return web.json_response(MMEventsPage.model_validate(result).model_dump(mode="json"))

    @api_operation(operation_id="getSessionConfiguration", summary="Read pinned session configuration; restart never replaces it with current YAML",
                   tag="Runtime", response_model=SessionConfigurationResponse, path_parameters=("session_id",))
    async def session_configuration(self, request):
        identity = request.match_info["session_id"]
        if not 1 <= len(identity) <= 128:
            raise ValueError("Invalid session identity")
        row = await self.run_database(self.storage().session_configuration, identity)
        if row is None:
            raise web.HTTPNotFound()
        return web.json_response(SessionConfigurationResponse.model_validate(row).model_dump(mode="json"))

    @api_operation(operation_id="getLiquidity", summary="Read sampled own-quote coverage without exchange requests", tag="Statistics",
                   response_model=LiquidityResponse, query_model=LiquidityQuery)
    async def liquidity(self, request):
        from hummingbot.core.mm_liquidity import liquidity_view
        query = self.query(request, LiquidityQuery)
        row = await self.run_database(self.storage().store.read, publication=True)
        if row is None:
            raise APIUnavailable("Bot status unavailable")
        result = liquidity_view(row, **query.model_dump())
        return web.json_response(LiquidityResponse.model_validate(result).model_dump(mode="json"))

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


def admin_middleware(settings, *, identity=None):
    expected_user = hashlib.sha256(settings.username.encode("utf-8")).digest()
    expected_password = hashlib.sha256(settings.password.encode("utf-8")).digest()

    @web.middleware
    async def authenticate(request, handler):
        if request.path != ADMIN_PREFIX and not request.path.startswith(ADMIN_PREFIX + "/"):
            return await handler(request)
        request_id(request)
        if not settings.enabled:
            return error_response(request, "admin_api_disabled", status=404)
        if not settings.documentation_enabled and (request.path == ADMIN_PREFIX + "/openapi.json"
                                                   or request.path == ADMIN_PREFIX + "/docs"
                                                   or request.path.startswith(ADMIN_PREFIX + "/docs/")):
            return error_response(request, "not_found", status=404)
        origin = request.headers.get("Origin")
        wildcard = "*" in settings.cors_origins
        if origin:
            try:
                parsed = urlsplit(origin)
                same_origin = parsed.netloc == request.host and parsed.scheme in ("http", "https")
            except ValueError:
                same_origin = False
            if not wildcard and origin not in settings.cors_origins and not same_origin:
                return error_response(request, "origin_not_allowed", status=403)
        if request.method == "OPTIONS":
            headers = {value.strip().lower() for value in request.headers.get("Access-Control-Request-Headers", "").split(",") if value.strip()}
            method = request.headers.get("Access-Control-Request-Method", "")
            if not origin or method not in ("GET", "POST", "HEAD") or not headers <= {"authorization", "content-type", "idempotency-key"}:
                return error_response(request, "preflight_not_allowed", status=403)
            response = web.Response(status=204, headers={
                "Access-Control-Allow-Methods": "GET, POST, HEAD, OPTIONS",
                "Access-Control-Allow-Headers": "Authorization, Content-Type, Idempotency-Key",
            })
        else:
            authorization = request.headers.get("Authorization", "")
            principal = None
            try:
                scheme, _, value = authorization.partition(" ")
                if scheme.casefold() == "basic":
                    if not settings.basic_auth_enabled:
                        raise AccessDenied("basic_auth_disabled")
                    try:
                        auth = BasicAuth.decode(authorization, encoding="utf-8")
                        user_ok = hmac.compare_digest(hashlib.sha256(auth.login.encode("utf-8")).digest(), expected_user)
                        password_ok = hmac.compare_digest(hashlib.sha256(auth.password.encode("utf-8")).digest(), expected_password)
                    except ValueError:
                        raise AccessDenied() from None
                    if not (user_ok & password_ok):
                        raise AccessDenied()
                    principal = Principal("basic:" + settings.username, "basic", "admin")
                elif scheme.casefold() == "bearer" and settings.google_auth_enabled and 1 <= len(value) <= 16384:
                    service = identity or request.app.get(ADMIN_IDENTITY_KEY)
                    if service is None:
                        raise AccessDenied("identity_provider_unavailable", status=503)
                    canonical = request.match_info.route.resource.canonical if request.match_info.route.resource else ""
                    required = ADMIN_PERMISSIONS.get(("GET" if request.method == "HEAD" else request.method, canonical))
                    login = canonical == ADMIN_PREFIX + "/access/login" and request.method == "POST"
                    principal = await service.authenticate(value, control=required in ("write", "admin") or login,
                                                           bind=login, request_id=request_id(request))
                else:
                    raise AccessDenied()
                canonical = request.match_info.route.resource.canonical if request.match_info.route.resource else ""
                required = ADMIN_PERMISSIONS.get(("GET" if request.method == "HEAD" else request.method, canonical))
                if required is None:
                    raise AccessDenied("insufficient_role", status=403)
                principal.require(required)
                request["admin_principal"] = principal
                response = await handler(request)
                if response.status >= 400 and request.method == "POST" and request.app.get(ADMIN_ACCESS_KEY):
                    try:
                        code = json.loads(response.body).get("code", "request_rejected")
                    except (ValueError, TypeError, AttributeError):
                        code = "request_rejected"
                    await request.app[ADMIN_ACCESS_KEY].audit_denial(
                        actor=principal, reason_code=code, request_identity=request_id(request),
                        login=request.path == ADMIN_PREFIX + "/access/login", mutation=True)
            except AccessDenied as error:
                if request.app.get(ADMIN_ACCESS_KEY):
                    await request.app[ADMIN_ACCESS_KEY].audit_denial(
                        actor=error.actor or principal, reason_code=error.code, request_identity=request_id(request),
                        login=request.path == ADMIN_PREFIX + "/access/login", mutation=request.method == "POST")
                response = error_response(request, error.code, status=error.status,
                                          retry_after_seconds=1 if error.code == "security_work_busy" else None)
                if error.status == 401 and settings.basic_auth_enabled:
                    response.headers["WWW-Authenticate"] = 'Basic realm="Hummingbot admin", charset="UTF-8"'
            except (APIUnavailable, SQLAlchemyError, asyncio.TimeoutError) as error:
                logging.getLogger(__name__).warning("Admin identity storage unavailable type=%s", type(error).__name__,
                                                    extra={"event": "admin_identity_unavailable", "request_id": request_id(request),
                                                           "reason_code": "database_unavailable"})
                response = error_response(request, "database_unavailable", status=503)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Request-ID"] = request_id(request)
        if origin:
            response.headers["Access-Control-Allow-Origin"] = "*" if wildcard else origin
            response.headers["Access-Control-Expose-Headers"] = "X-Request-ID, Retry-After"
            if not wildcard:
                response.headers["Access-Control-Allow-Credentials"] = "true"
            response.headers["Vary"] = ", ".join(filter(None, (response.headers.get("Vary"), "Origin")))
        return response
    return authenticate
