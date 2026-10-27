"""In-process funding scheduling; the existing FundingSync owns backfill logic."""
import asyncio
import os
import time


class FundingObserver:
    def __init__(self, bot_id, availability, log):
        from hummingbot.client.config.defaults import DEFAULT_ENV_CONFIG
        self.bot_id, self.availability, self.log = bot_id, availability, log
        self.task = self.sync = self.store = None
        self.state = {}
        self.error = None
        self.configured = False
        self.next_poll = 0
        self.template_pairs = None
        self.interval = int(os.environ.get("FUNDING_SYNC_INTERVAL_SECONDS",
                                           DEFAULT_ENV_CONFIG["FUNDING_SYNC_INTERVAL_SECONDS"]))
        if not 1 <= self.interval <= 3600:
            raise ValueError("FUNDING_SYNC_INTERVAL_SECONDS must be within 1..3600")

    def poll(self, engine, owner, core):
        from hummingbot.client.config.environment_credentials import configured_environment_connectors
        if (self.task is not None and not self.task.done()) or time.monotonic() < self.next_poll:
            return
        self.next_poll = time.monotonic() + self.interval
        try:
            self.configured = bool(configured_environment_connectors(["okx_perpetual"]))
        except Exception as error:
            self.availability.fail("funding", "funding_configuration_unavailable", error, operator=True)
            return
        if not self.configured:
            self.availability.clear("funding")
            return
        self.task = asyncio.create_task(self.collect(engine, owner, core))

    async def collect(self, engine, owner, core):
        from hummingbot.client.config.mm_profiles import MMProfilesConfig
        from hummingbot.client.settings import CONF_DIR_PATH
        from hummingbot.core.funding_sync import FundingReader, FundingSync
        from hummingbot.model.funding_bill import FundingBillStore
        try:
            row = owner.check_owner()
            if self.store is None:
                self.store = FundingBillStore(engine, self.bot_id,
                                              owner_backend_pid=owner.backend_pid)
            self.state = await asyncio.to_thread(self.store.load)
            pairs, earliest = await asyncio.to_thread(self.store.scope)
            pairs.update(self.state.get("pairs", []))
            if self.template_pairs is None:
                self.template_pairs = set()
                path = CONF_DIR_PATH / "life_mm_profiles.yaml"
                if path.exists():
                    catalogue = await MMProfilesConfig.load(path)
                    template = catalogue.markets.get("perpetual")
                    if template is not None:
                        self.template_pairs.add(template.trading_pair)
            pairs.update(self.template_pairs)
            connector = core.connector_manager.connectors.get("okx_perpetual")
            if connector is not None:
                pairs.update(connector.trading_pairs or [])
            if not pairs:
                return
            # Fresh REST assistant per batch survives the shared HTTP session's
            # shutdown/recreation when the trading core stops. No network tasks,
            # market data, RateOracle registration or exchange mutations start.
            reader = FundingReader.from_environment()
            if self.sync is None:
                self.sync = FundingSync(self.store, reader,
                                        environment=reader.connector.trading_environment,
                                        poll_interval_seconds=self.interval)
            else:
                self.sync.getter = reader
            self.state = await self.sync.sync(
                owner.owner_uuid, owner.generation, now_ms=int(float(row["database_now"]) * 1000),
                pairs=pairs, earliest_ms=earliest)
            self.error = None
            self.availability.clear("funding")
            now_ms = int(time.time() * 1000)
            if self.state.get("pending") or self.state.get("covered_until_ms", now_ms) < now_ms - 120000:
                self.next_poll = time.monotonic() + 1
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self.availability.fail("funding", "funding_observation_unavailable", error)
            self.error = type(error).__name__
            self.log.warning("Funding observation unavailable type=%s", self.error)
            if self.store is not None:
                try:
                    checkpoint = await asyncio.to_thread(self.store.load)
                    checkpoint = {**checkpoint, "last_error": self.error}
                    await asyncio.to_thread(self.store.commit, owner.owner_uuid,
                                            owner.generation, [], checkpoint)
                    self.state = checkpoint
                except Exception:
                    pass  # Lost ownership/DB failure cannot acknowledge progress.

    def status(self, *, now_ms):
        from hummingbot.core.funding_sync import public_status
        return {**public_status(self.state, now_ms=now_ms, error=self.error),
                **({"state": "not_configured"} if not self.configured else {})}

    async def close(self):
        if self.task is not None and not self.task.done():
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
