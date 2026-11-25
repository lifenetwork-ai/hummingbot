"""Warm public dependencies while the current MM session still owns its orders."""
import asyncio

from controllers.market_making.life_mm import LifeMMConfig, LifeMMController
from hummingbot.strategy_v2.utils.mm_spread import prepare_natr

PREPARE_SECONDS = 20


class ProfileSwitchPreparation:
    def __init__(self, session, provider, configs, *, automatic=False):
        self.session = session
        self.identity = session.id, session.generation
        self.automatic = automatic
        self.provider = provider
        self.controllers = [LifeMMController(LifeMMConfig.model_validate(config), provider, asyncio.Queue())
                            for config in configs]
        self.strict_guards = [controller for controller in self.controllers if controller.config.quote_guards.enabled
                              and controller.config.quote_guards.mode == 'strict']
        self.references = {}
        for controller in self.controllers:
            config = controller.config
            if config.references:
                self.references.setdefault(config.reference_connector, set()).update(ref.pair for ref in config.references)
        self.candles = []
        self.guard_samples = {}
        self.guard_task = asyncio.create_task(self.sample_guards()) if self.strict_guards else None
        self.task = asyncio.create_task(self.prepare())
        self.promoted = False

    @property
    def ready(self):
        return self.task.done() and not self.task.cancelled() and self.task.exception() is None

    @property
    def guards_ready(self):
        return all(self.guard_samples.get(controller.config.id) is not None for controller in self.strict_guards)

    async def prepare(self):
        async with asyncio.timeout(PREPARE_SECONDS):
            async with asyncio.TaskGroup() as tasks:
                for name, pairs in self.references.items():
                    tasks.create_task(self._prepare_reference_books(name, pairs))
                tasks.create_task(self._prepare_candles())
            while True:
                try:
                    for controller in self.controllers:
                        config = controller.config
                        controller._fresh_price(config.connector_name, config.trading_pair, config.initial_anchor)
                        for reference in config.references:
                            controller._fresh_price(config.reference_connector, reference.pair)
                    break
                except (ValueError, EnvironmentError):
                    await asyncio.sleep(.1)

    async def _prepare_reference_books(self, name, pairs):
        connector = self.provider.get_connector_with_fallback(name)
        if not connector.is_trading_required and not pairs.issubset(connector.trading_rules):
            await connector._update_trading_rules()
        trading_pairs = sorted(pairs)
        while True:
            results = await self.provider.acquire_order_books(name, trading_pairs, self)
            if all(results.get(pair) for pair in trading_pairs):
                return
            await asyncio.sleep(.2)

    async def _prepare_candles(self):
        feeds = []
        for controller in self.controllers:
            volatility = controller.config.spread_policy.volatility
            if volatility.enabled:
                config = controller._spread_candle_config()
                self.candles.append(config)
                feed = self.provider.acquire_candle_feed(config, self)
                if volatility.on_unavailable == 'wait':
                    feeds.append(feed)
        if self.candles and not await prepare_natr() and feeds:
            raise OSError('Profile volatility indicator unavailable')
        while not all(feed.ready for feed in feeds):
            await asyncio.sleep(.1)

    async def sample_guards(self):
        while True:
            for controller in self.strict_guards:
                self.guard_samples[controller.config.id] = controller.sample_profile_guard()
            await asyncio.sleep(.25)

    def apply_guards(self, controllers):
        previews = {controller.config.id: controller for controller in self.controllers}
        for controller in controllers:
            preview = previews.get(controller.config.id)
            if preview is not None:
                controller.use_profile_guard(preview, self.guard_samples.get(controller.config.id))
        if self.guard_task is not None:
            self.guard_task.cancel()

    def retain_valuation_books(self, strategy):
        for name, pair in self.provider.retain_reference_books_for(strategy, self):
            self.references.setdefault(name, set()).add(pair)

    async def close(self):
        if not self.task.done():
            self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)
        if self.guard_task is not None:
            self.guard_task.cancel()
            await asyncio.gather(self.guard_task, return_exceptions=True)
        for config in self.candles:
            self.provider.release_candle_feed(config, self)
        for name, pairs in self.references.items():
            await self.provider.release_order_books(name, sorted(pairs), self)
