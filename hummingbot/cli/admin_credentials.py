"""Secret-input handlers; deployment configuration and trading commands stay separate."""
import asyncio
import contextvars
import functools

from aiohttp import web

from hummingbot.cli.admin_credential_models import (
    CredentialCatalogue,
    CredentialReceipt,
    CredentialSetRequest,
    ExchangeCredentialStatus,
)
from hummingbot.cli.admin_database import AdminDatabaseExecutor, DatabaseBusy
from hummingbot.cli.admin_errors import MESSAGES, request_id
from hummingbot.cli.api_documentation import api_operation
from hummingbot.client.config.credential_config import CredentialSettings, credential_source_mode
from hummingbot.client.config.runtime_config import RuntimeConfigurationError
from hummingbot.connector.credential_adapters import CredentialAdapterError, CredentialAdapters, OKXCredentialAdapter
from hummingbot.core.admin_identity import AccessDenied
from hummingbot.core.credential_encryption import CredentialContext, CredentialCryptoError, credential_cipher
from hummingbot.model.bot_state_store import ControlConflict
from hummingbot.model.credential_store import CredentialStore


class AdminCredentialsAPI:
    def credential_storage(self):
        return CredentialStore(self.storage().store)

    async def credential_adapters(self):
        override = getattr(self, "_credential_adapters_override", None)
        if override is not None:
            return override
        catalogue = await self.deployed_profiles()
        return CredentialAdapters([OKXCredentialAdapter()], enabled_connectors={config.connector_name for config in catalogue.markets.values()})

    async def run_credential_work(self, function, *args):
        if getattr(self, "_credential_work", None) is None:
            self._credential_work = AdminDatabaseExecutor()
        try:
            result = self._credential_work.submit(functools.partial(contextvars.copy_context().run, function, *args), heavy=True)
            return await asyncio.wait_for(result, 15)
        except DatabaseBusy:
            raise AccessDenied("security_work_busy", status=429) from None
        except asyncio.TimeoutError:
            raise CredentialCryptoError("kms_unavailable") from None

    def encrypt_credentials(self, values, context):
        if getattr(self, "_credential_cipher", None) is None:
            try:
                self._credential_cipher = credential_cipher(CredentialSettings.from_environment())
            except RuntimeConfigurationError:
                raise CredentialCryptoError("kms_configuration_invalid") from None
        return self._credential_cipher.encrypt(values, context)

    @staticmethod
    def credential_receipt(row):
        return CredentialReceipt.model_validate({field: row[field] for field in CredentialReceipt.model_fields if field in row})

    @api_operation(operation_id="listCredentialInputs", summary="Read deployment-enabled connection inputs; saved secrets are never returned",
                   tag="Credentials", response_model=CredentialCatalogue)
    async def credentials_catalogue(self, request):
        registry = await self.credential_adapters()
        result = CredentialCatalogue(source=credential_source_mode(), exchanges=[dict(
            **adapter.descriptor(), connectors=sorted(registry.enabled_connectors.intersection(adapter.connectors)))
            for adapter in registry.adapters.values() if registry.enabled_connectors.intersection(adapter.connectors)])
        return web.json_response(result.model_dump(mode="json"))

    @api_operation(operation_id="getExchangeCredentialStatus", summary="Read saved versus loaded connection state without revealing credentials",
                   tag="Credentials", response_model=ExchangeCredentialStatus, path_parameters=("exchange",))
    async def credentials_status(self, request):
        exchange = request.match_info["exchange"]
        registry = await self.credential_adapters()
        adapter = registry.get(exchange)
        store = self.credential_storage()
        metadata, state, operations = await self.run_database(store.status_snapshot, exchange)
        source, actor = credential_source_mode(), request["admin_principal"]
        slots = []
        for connector in sorted(registry.enabled_connectors.intersection(adapter.connectors)):
            row = next((item for item in metadata if item["connector"] == connector and item["environment"] == adapter.environment), None)
            operation = operations.get(row["operation_id"]) if row else None
            status, label, detail, reason = "not_configured", "Not configured", "Configure this connection before starting trading.", None
            snapshot = (state or {}).get("status") or {}
            observed_at = (snapshot.get("availability") or {}).get("observed_at")
            owner = snapshot.get("credential_owner") or {}
            fresh = bool(state and state.get("owner_uuid") and state.get("heartbeat_age_s") is not None
                         and 0 <= float(state["heartbeat_age_s"]) <= 5 and observed_at is not None
                         and 0 <= float(state["database_now"]) - float(observed_at) <= 5
                         and owner == dict(owner_uuid=state["owner_uuid"], generation=state["owner_generation"]))
            if source == "environment":
                status, label, detail, reason = "managed_by_deployment", "Managed by deployment", "Update credentials in deployment settings and restart.", "credentials_managed_externally"
            elif operation and operation["status"] in ("accepted", "applying"):
                status, label, detail = "checking", "Checking connection", "Waiting for the engine to load and confirm this connection."
            elif row is not None and fresh and row["state"] == "active" and (snapshot.get("credential_versions") or {}).get(connector) == row["version"]:
                status, label, detail = "connected", "Connected", "The engine loaded this connection. Trading has its own separate running or stopped state."
            elif operation and operation["status"] == "failed":
                reason = operation["error"]
                status, label, detail = "failed", "Connection failed", MESSAGES.get(reason, "The saved connection could not be loaded. Contact support with the operation ID.")
            elif row is not None:
                status, label, detail, reason = "unavailable", "Status unavailable", "Current engine evidence is unavailable. Saved credentials do not prove an active connection.", "credential_observation_unavailable"
            allowed = actor.allows("write") and source == "postgres_kms"
            reason_action = None if allowed else "insufficient_role" if not actor.allows("write") else "credentials_managed_externally"
            operation_pending = bool((state or {}).get("recovery_state", {}).get("credential_operation"))
            stopped = bool(state and state["desired_state"] == "stopped" and state["control_version"] == state["acknowledged_version"]
                           and not any((state["recovery_state"] or {}).get(key) for key in ("runtime_active", "halt", "pending_mutations")))
            if allowed and (operation_pending or not stopped):
                allowed, reason_action = False, "credential_operation_in_progress" if operation_pending else "bot_must_be_stopped"
            slots.append(dict(connector=connector, configured=bool(row and row["state"] != "revoked"), version=row["version"] if row else 0,
                              status=status, label=label, detail=detail, reason_code=reason,
                              changed_at=row["changed_at"] if row else None, changed_by=row["changed_by"] if row else None,
                              operation_id=row["operation_id"] if row else None, observed_at=(state or {}).get("heartbeat") if fresh else None,
                              actions={"configure": dict(allowed=allowed, reason_code=reason_action,
                                       detail="Configure or replace while trading is stopped." if allowed else "Check your permission and stop/connection state first.")}))
        return web.json_response(ExchangeCredentialStatus(exchange=exchange, environment=adapter.environment,
                                                          source=source, slots=slots).model_dump(mode="json"))

    @api_operation(operation_id="setExchangeCredentials", summary="Validate and save a full connection after acknowledged Stop; never resumes trading",
                   tag="Credentials", request_model=CredentialSetRequest, response_model=CredentialReceipt, status=202,
                   path_parameters=("exchange", "connector"), headers=("Idempotency-Key",))
    async def credentials_set(self, request):
        if credential_source_mode() != "postgres_kms":
            raise ControlConflict("Credentials are managed by deployment", code="credentials_managed_externally")
        if getattr(self, "_credential_active", 0) >= 2:
            raise AccessDenied("security_work_busy", status=429)
        self._credential_active = getattr(self, "_credential_active", 0) + 1
        try:
            data = await self.access_body(request, CredentialSetRequest)
            exchange, connector = request.match_info["exchange"], request.match_info["connector"]
            registry = await self.credential_adapters()
            adapter = registry.get(exchange, connector)
            request["credential_scope"] = (exchange, connector, adapter.environment)
            values = {key: value.get_secret_value() for key, value in data.credentials.items()}
            adapter.config(connector, values)  # Reject routing/unknown fields before any request.
            scope = dict(exchange=exchange, connector=connector, environment=adapter.environment,
                         expected_version=data.expected_version, expected_control_version=data.expected_control_version,
                         idempotency_key=request.headers.get("Idempotency-Key"))
            store, actor = self.credential_storage(), request["admin_principal"]
            previous = await self.run_control_database(store.prepare_set, actor, **scope)
            if previous:
                return web.json_response(self.credential_receipt(previous).model_dump(mode="json"), status=202)
            account_id = await adapter.preflight(connector, values)
            context = CredentialContext(store.bot_id, exchange, connector, adapter.environment, data.expected_version + 1, account_id)
            envelope = await self.run_credential_work(self.encrypt_credentials, values, context)
            receipt = await self.run_control_database(store.accept_set, actor, **scope, account_id=account_id,
                                                      envelope=envelope, request_id=request_id(request))
            return web.json_response(self.credential_receipt(receipt).model_dump(mode="json"), status=202)
        except CredentialAdapterError as error:
            await self.record_credential_failure(request, error.code)
            raise
        except CredentialCryptoError as error:
            await self.record_credential_failure(request, error.code)
            raise
        finally:
            self._credential_active -= 1

    async def record_credential_failure(self, request, code):
        exchange, connector, environment = request.get("credential_scope", (None, None, None))
        await self.run_control_database(self.credential_storage().record_failure, request["admin_principal"],
                                        request_id=request_id(request), exchange=exchange, environment=environment,
                                        connector=connector, reason_code=code)

    @api_operation(operation_id="getCredentialOperation", summary="Read durable connection operation progress and its confirmed outcome",
                   tag="Credentials", response_model=CredentialReceipt, path_parameters=("operation_id",))
    async def credentials_operation(self, request):
        identity = request.match_info["operation_id"]
        if not 1 <= len(identity) <= 128:
            raise ValueError("Invalid operation identity")
        row = await self.run_database(self.credential_storage().operation, identity)
        if row is None:
            raise web.HTTPNotFound()
        return web.json_response(self.credential_receipt(row).model_dump(mode="json"))
