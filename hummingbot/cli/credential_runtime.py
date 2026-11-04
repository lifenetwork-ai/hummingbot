"""Load credentials only between stopped runtimes, using bounded off-loop work."""
import functools

from hummingbot.cli.admin_database import AdminDatabaseExecutor
from hummingbot.client.config.managed_credentials import create_managed_source
from hummingbot.client.config.security import Security


class ManagedCredentialRuntime:
    def __init__(self, state, *, factory=create_managed_source):
        self.state, self.factory = state, factory
        self.source = None
        self.versions = {}
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
        self.versions, self.loaded = {}, False
        configs, versions = await self.call(self.source.load_configs, sorted(versions))
        await self.source.verify_configs(configs)
        self.install(configs)
        self.versions, self.loaded = versions, True
        return versions.copy()

    async def close(self):
        await self.work.close()
        self.install({})
        self.versions, self.loaded = {}, False
        if self.source is not None:
            self.source.close()
            self.source = None
