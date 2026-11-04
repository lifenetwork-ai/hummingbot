"""Small exchange boundary; shared lifecycle/storage never selects a venue implicitly."""
import asyncio

from pydantic import ValidationError

from hummingbot.client.config.config_helpers import (
    ClientConfigAdapter,
    api_keys_from_connector_config_map,
    get_connector_class,
)
from hummingbot.client.config.environment_credentials import connector_environment_fields
from hummingbot.connector.okx_environment import OKXEnvironment


class CredentialAdapterError(RuntimeError):
    def __init__(self, code, *, status=422):
        super().__init__(code)
        self.code, self.status = code, status


def read_only_connector(connector, config):
    from hummingbot.client.settings import AllConnectorSettings
    setting = AllConnectorSettings.get_connector_settings()[connector]
    parameters = setting.conn_init_parameters(trading_pairs=[], trading_required=False,
                                              api_keys=api_keys_from_connector_config_map(config))
    return get_connector_class(connector)(**parameters)


class OKXCredentialAdapter:
    exchange = "okx"
    connectors = ("okx", "okx_perpetual")
    fields = ("api_key", "secret_key", "passphrase")

    def __init__(self, *, environ=None):
        self.deployment = OKXEnvironment.load(environ=environ)
        self.environment = self.deployment.mode

    def descriptor(self):
        return dict(exchange=self.exchange, environment=self.environment, site=self.deployment.site,
                    fields=[dict(name=name, label={"api_key": "API key", "secret_key": "Secret key", "passphrase": "Passphrase"}[name],
                                 secret=True, required=True) for name in self.fields],
                    cleanup_mode="bot_orders_and_managed_hedge_cross_positions")

    def config(self, connector, values):
        if connector not in self.connectors:
            raise CredentialAdapterError("unsupported_connector")
        if (not isinstance(values, dict) or set(values) != set(self.fields)
                or any(not isinstance(value, str) or not 1 <= len(value) <= 4096 for value in values.values())):
            raise CredentialAdapterError("validation_failed")
        model, _ = connector_environment_fields(connector)
        payload = {"connector": connector, **{connector + "_" + name: value for name, value in values.items()}}
        if connector == "okx":
            payload["okx_registration_sub_domain"] = self.deployment.site
        try:
            return ClientConfigAdapter(model.model_validate(payload))
        except ValidationError:
            raise CredentialAdapterError("validation_failed") from None

    def reader(self, connector, config):
        return read_only_connector(connector, config)

    async def preflight(self, connector, values):
        config = self.config(connector, values)
        try:
            reader = self.reader(connector, config)
            response = await asyncio.wait_for(reader._api_get(
                path_url="/api/v5/account/config", is_auth_required=True, return_err=True), 10)
        except asyncio.CancelledError:
            raise
        except Exception:
            raise CredentialAdapterError("exchange_unavailable", status=503) from None
        if not isinstance(response, dict):
            raise CredentialAdapterError("exchange_unavailable", status=503)
        code = str(response.get("code"))
        if code != "0":
            if code in {"50101", "50103", "50104", "50105", "50106", "50107", "50108", "50109", "50110", "50111", "50112", "50113", "50114", "50119", "50120"}:
                raise CredentialAdapterError("exchange_credentials_rejected")
            raise CredentialAdapterError("exchange_unavailable", status=503)
        data = response.get("data")
        if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict):
            raise CredentialAdapterError("exchange_identity_unavailable", status=503)
        account = data[0]
        uid = account.get("uid")
        if not isinstance(uid, str) or not 1 <= len(uid) <= 128 or not uid.isascii() or not uid.replace("-", "").isalnum():
            raise CredentialAdapterError("exchange_identity_unavailable", status=503)
        permissions = set(str(account.get("perm", "")).split(","))
        if not {"read_only", "trade"} <= permissions or "withdraw" in permissions:
            raise CredentialAdapterError("exchange_permissions_required")
        if connector == "okx_perpetual" and (account.get("acctLv") not in ("2", "3") or account.get("posMode") != "long_short_mode"):
            raise CredentialAdapterError("unsupported_cleanup_mode")
        # Site belongs to deployment, and request UID distinguishes subaccounts.
        # Keep this binding after removal so site/account changes cannot adopt a ledger.
        return self.deployment.site + ":" + uid


class CredentialAdapters:
    def __init__(self, adapters, *, enabled_connectors):
        self.adapters = {adapter.exchange: adapter for adapter in adapters}
        self.enabled_connectors = frozenset(enabled_connectors)

    def get(self, exchange, connector=None):
        adapter = self.adapters.get(exchange)
        if adapter is None:
            raise CredentialAdapterError("unsupported_exchange")
        if connector is not None and (connector not in self.enabled_connectors or connector not in adapter.connectors):
            raise CredentialAdapterError("unsupported_connector")
        return adapter
