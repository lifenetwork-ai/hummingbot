"""Load credentials only between stopped runtimes, using bounded off-loop work."""
import functools

from hummingbot.cli.admin_database import AdminDatabaseExecutor
from hummingbot.client.config.managed_credentials import create_managed_source
from hummingbot.client.config.runtime_config import RuntimeConfigurationError
from hummingbot.client.config.security import Security
from hummingbot.core.credential_encryption import CredentialCryptoError


class ManagedCredentialRuntime:
    def __init__(self, state, *, factory=create_managed_source):
        self.state, self.factory = state, factory
        self.source = None
        self.versions = {}
        self.configs = {}
        self.loaded = False
        self.work = AdminDatabaseExecutor()

    def install(self, configs):
        Security.initialize_from_managed(configs)

    async def call(self, function, *args, **kwargs):
        return await self.work.submit(functools.partial(function, *args, **kwargs), heavy=True)

    def ensure_source(self):
        if self.source is None:
            self.source = self.factory(self.state)

    async def reload(self):
        await self.call(self.ensure_source)
        metadata = await self.call(self.source.store.metadata)
        versions = {row["connector"]: row["version"] for row in metadata if row["state"] == "active"}
        if self.loaded and versions == self.versions:
            return self.versions.copy()
        # The caller has already stopped/detached all runtime consumers. Do not
        # retain a prior authorized bundle as an implicit fallback after commit.
        self.install({})
        self.versions, self.configs, self.loaded = {}, {}, False
        configs, versions = await self.call(self.source.load_configs, sorted(versions))
        await self.source.verify_configs(configs)
        self.install(configs)
        self.configs = configs
        self.versions, self.loaded = versions, True
        return versions.copy()

    async def reload_cleanup(self, operation):
        try:
            await self.call(self.ensure_source)
        except RuntimeConfigurationError:
            raise CredentialCryptoError("kms_configuration_invalid") from None
        expected = operation["affected_versions"]
        if self.loaded and self.versions == expected and set(self.configs) == set(expected):
            configs, versions = self.configs, self.versions
        else:
            self.install({})
            self.versions, self.configs, self.loaded = {}, {}, False
            configs, versions = await self.call(self.source.load_cleanup_configs, operation["id"])
        await self.source.verify_configs(configs)
        self.install(configs)
        self.configs, self.versions, self.loaded = configs, versions, True
        return versions.copy()

    def detach_selected(self, connector):
        self.configs = {name: value for name, value in self.configs.items() if name != connector}
        self.versions = {name: value for name, value in self.versions.items() if name != connector}
        self.install(self.configs)
        if self.source is not None:
            self.source.loaded_bindings.pop(connector, None)

    async def close(self):
        await self.work.close()
        self.install({})
        self.versions, self.configs, self.loaded = {}, {}, False
        if self.source is not None:
            self.source.close()
            self.source = None
