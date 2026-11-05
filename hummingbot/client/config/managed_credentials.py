"""Explicit worker-side managed loads into existing connector configuration models.

No database/KMS callbacks are installed on Security.api_keys, ticks or order gates.
"""
from hummingbot.client.config.runtime_config import RuntimeConfigurationError
from hummingbot.core.credential_encryption import CredentialContext


def create_managed_source(state, *, enabled_connectors=("okx", "okx_perpetual")):
    from hummingbot.client.config.credential_config import CredentialSettings
    from hummingbot.connector.credential_adapters import CredentialAdapters, OKXCredentialAdapter
    from hummingbot.core.credential_encryption import credential_cipher
    from hummingbot.model.credential_store import CredentialStore
    return ManagedCredentialSource(CredentialStore(state), credential_cipher(CredentialSettings.from_environment()),
                                   CredentialAdapters([OKXCredentialAdapter()], enabled_connectors=enabled_connectors))


def initialize_managed_credentials(state, *, names=None):
    """Synchronous CLI preflight only; service invokes loads on its bounded worker."""
    from hummingbot.client.config.security import Security
    source = create_managed_source(state)
    try:
        selected = names if names is not None else [row["connector"] for row in source.store.metadata() if row["state"] == "active"]
        configs, versions = source.load_configs(selected)
        Security.initialize_from_managed(configs)
        return versions
    finally:
        source.close()


class ManagedCredentialSource:
    def __init__(self, store, cipher, adapters):
        self.store, self.cipher, self.adapters = store, cipher, adapters
        self.loaded_bindings = {}

    def load_configs(self, names):
        return self._load_configs(names)

    def load_cleanup_configs(self, operation_id):
        state = self.store.store.read() or {}
        operation = (state.get("recovery_state") or {}).get("credential_operation") or {}
        if (operation.get("id") != operation_id or operation.get("action") != "credentials_disconnect"
                or state.get("desired_state") != "stopped" or not operation.get("affected_versions")):
            raise RuntimeConfigurationError("credential_operation_in_progress")
        return self._load_configs(sorted(operation["affected_versions"]), cleanup=operation)

    def _load_configs(self, names, *, cleanup=None):
        configs, versions, bindings = {}, {}, {}
        for name in names:
            candidates = [adapter for adapter in self.adapters.adapters.values() if name in adapter.connectors]
            if len(candidates) != 1:
                raise RuntimeConfigurationError("unsupported_connector")
            adapter = self.adapters.get(candidates[0].exchange, name)
            row = self.store.load(adapter.exchange, name, adapter.environment)
            if row is None:
                raise RuntimeConfigurationError("credentials_not_configured")
            allowed = row["state"] == "active"
            if cleanup is not None:
                selected = name == cleanup["connector"]
                allowed = (row["version"] == cleanup["affected_versions"].get(name)
                           and adapter.exchange == cleanup["exchange"] and adapter.environment == cleanup["environment"]
                           and (row["state"] == "revoking" and row.get("operation_id") == cleanup["id"] if selected else row["state"] == "active"))
            if not allowed:
                raise RuntimeConfigurationError("credentials_not_configured")
            if adapter.exchange == "okx" and not row["account_id"].startswith(adapter.deployment.site + ":"):
                raise RuntimeConfigurationError("exchange_account_mismatch")
            context = CredentialContext(self.store.bot_id, adapter.exchange, name, adapter.environment, row["version"], row["account_id"])
            configs[name] = adapter.config(name, self.cipher.decrypt(row["envelope"], context))
            versions[name] = row["version"]
            bindings[name] = row["account_id"]
        if len(set(bindings.values())) > 1:
            raise RuntimeConfigurationError("exchange_account_mismatch")
        self.loaded_bindings = bindings
        return configs, versions

    async def verify_configs(self, configs):
        for name, config in configs.items():
            adapter = next(adapter for adapter in self.adapters.adapters.values() if name in adapter.connectors)
            values = {field: getattr(config, name + "_" + field).get_secret_value() for field in adapter.fields}
            account_id = await adapter.preflight(name, values)
            if account_id != self.loaded_bindings[name]:
                raise RuntimeConfigurationError("exchange_account_mismatch")

    def close(self):
        self.cipher.close()
