"""Small access/audit handlers on the existing admin HTTP/database adapters."""
import asyncio
import json
import logging

from aiohttp import web

from hummingbot.cli.admin_access_models import (
    AccountResponse,
    AccountsPage,
    AddAccountRequest,
    AuditEventResponse,
    AuditPage,
    AuditQuery,
    BootstrapResponse,
    ChangeAccountRequest,
    PrincipalResponse,
)
from hummingbot.cli.admin_errors import request_id
from hummingbot.cli.admin_models import PageQuery
from hummingbot.cli.api_documentation import api_operation


class AdminAccessAPI:
    async def audit_denial(self, *, actor, reason_code, request_identity, login=False, mutation=False):
        logger = logging.getLogger(__name__)
        logger.warning("Admin access denied code=%s", reason_code,
                       extra={"event": "admin_access_denied", "request_id": request_identity,
                              "actor_id": actor.id if actor else None, "reason_code": reason_code})
        # Polling and malformed anonymous traffic use bounded platform access logs.
        # Explicit sign-in/mutation denials also have durable security history.
        if not (login or mutation and actor is not None):
            return
        try:
            await asyncio.wait_for(self.run_database(self.access_store().record_denial, actor,
                                                     request_id=request_identity, reason_code=reason_code, login=login), 1)
        except Exception as error:
            logger.error("Security audit unavailable type=%s", type(error).__name__,
                         extra={"event": "admin_audit_unavailable", "request_id": request_identity,
                                "reason_code": "audit_persistence_unavailable"})

    @staticmethod
    def principal_response(actor):
        return PrincipalResponse(id=actor.id, kind=actor.kind, role=actor.role, email=actor.email,
                                 role_label={"admin": "Administrator", "write": "Operator", "read": "Viewer"}[actor.role],
                                 permissions={"read": actor.allows("read"), "operate": actor.allows("write"),
                                              "manage_accounts": actor.allows("admin"), "manage_credentials": actor.allows("write")})

    @staticmethod
    def account_response(row):
        return AccountResponse(**{field: row[field] for field in AccountResponse.model_fields if field != "bound"},
                               bound=row["subject"] is not None)

    @staticmethod
    async def access_body(request, model):
        if request.content_type != "application/json":
            raise web.HTTPUnsupportedMediaType()
        try:
            data = bytearray()
            async for chunk in request.content.iter_chunked(8192):
                data.extend(chunk)
                if len(data) > 16384:
                    raise web.HTTPRequestEntityTooLarge(max_size=16384, actual_size=len(data))
            return model.model_validate(json.loads(data))
        except (json.JSONDecodeError, UnicodeError):
            raise web.HTTPBadRequest() from None

    @api_operation(operation_id="adminLogin", summary="Sign in with verified Google identity or enabled bootstrap password",
                   tag="Access", response_model=PrincipalResponse)
    async def access_login(self, request):
        actor = request["admin_principal"]
        await self.run_control_database(self.access_store().record_login, actor, request_id=request_id(request))
        return web.json_response(self.principal_response(actor).model_dump(mode="json"))

    @api_operation(operation_id="getAdminIdentity", summary="Read your current role and permissions", tag="Access", response_model=PrincipalResponse)
    async def access_me(self, request):
        return web.json_response(self.principal_response(request["admin_principal"]).model_dump(mode="json"))

    @api_operation(operation_id="getAdminBootstrap", summary="Check readiness before disabling bootstrap password sign-in",
                   tag="Access", response_model=BootstrapResponse)
    async def access_bootstrap(self, request):
        state = await self.run_database(self.access_store().bootstrap_status)
        ready = state["google_admin_ready"] and self.settings.google_auth_enabled
        result = BootstrapResponse(**{**state, "google_admin_ready": ready}, basic_auth_enabled=self.settings.basic_auth_enabled,
                                   google_auth_enabled=self.settings.google_auth_enabled,
                                   detail=("Google administration is ready. Disable password sign-in in deployment settings after verification."
                                           if ready and self.settings.basic_auth_enabled else "Google administration is ready."
                                           if ready else "Keep bootstrap access enabled until a Google administrator has signed in successfully."))
        return web.json_response(result.model_dump(mode="json"))

    @api_operation(operation_id="listAdminAccounts", summary="List approved accounts and roles", tag="Access", response_model=AccountsPage, query_model=PageQuery)
    async def access_accounts(self, request):
        query = self.query(request, PageQuery)
        page = await self.run_database(self.access_store().accounts_page, **query.model_dump())
        result = AccountsPage(items=[self.account_response(row) for row in page["items"]], next_cursor=page["next_cursor"])
        return web.json_response(result.model_dump(mode="json"))

    @api_operation(operation_id="addAdminAccount", summary="Approve one Google account and assign its role", tag="Access",
                   request_model=AddAccountRequest, response_model=AccountResponse, status=201)
    async def access_add_account(self, request):
        data = await self.access_body(request, AddAccountRequest)
        row = await self.run_control_database(self.access_store().add_account, request["admin_principal"],
                                              **data.model_dump(), request_id=request_id(request))
        return web.json_response(self.account_response(row).model_dump(mode="json"), status=201)

    @api_operation(operation_id="changeAdminAccount", summary="Change an approved account role or disable its access",
                   tag="Access", request_model=ChangeAccountRequest, response_model=AccountResponse, path_parameters=("account_id",))
    async def access_change_account(self, request):
        data = await self.access_body(request, ChangeAccountRequest)
        account_id = request.match_info["account_id"]
        if not 1 <= len(account_id) <= 128:
            raise ValueError("Invalid account identity")
        row = await self.run_control_database(self.access_store().change_account, request["admin_principal"], account_id,
                                              **data.model_dump(), request_id=request_id(request))
        return web.json_response(self.account_response(row).model_dump(mode="json"))

    @api_operation(operation_id="getAdminAudit", summary="Read security history: who changed access or connections and when",
                   tag="Audit", response_model=AuditPage, query_model=AuditQuery)
    async def access_audit(self, request):
        query = self.query(request, AuditQuery)
        page = await self.run_database(self.access_store().audit_page, heavy=query.limit > 100, **query.model_dump())
        events = [AuditEventResponse(**{key: str(row[key]) if key == "id" else row[key]
                                        for key in AuditEventResponse.model_fields}) for row in page["items"]]
        return web.json_response(AuditPage(items=events, next_cursor=page["next_cursor"]).model_dump(mode="json"))
