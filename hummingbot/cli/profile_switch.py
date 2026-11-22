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
        self.references = {}
        for controller in self.controllers:
            config = controller.config
            if config.references:
                self.references.setdefault(config.reference_connector, set()).update(ref.pair for ref in config.references)
        self.candles = []
        self.task = asyncio.create_task(self.prepare())
        self.promoted = False

    @property
    def ready(self):
        return self.task.done() and not self.task.cancelled() and self.task.exception() is None

    async def prepare(self):
        async with asyncio.timeout(PREPARE_SECONDS):
            for name, pairs in self.references.items():
                connector = self.provider.get_connector_with_fallback(name)
                if not connector.is_trading_required and not pairs.issubset(connector.trading_rules):
                    await connector._update_trading_rules()
                while True:
                    results = await self.provider.acquire_order_books(name, sorted(pairs), self)
                    if all(results.get(pair) for pair in pairs):
                        break
                    await asyncio.sleep(.2)
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
            while True:
                try:
                    for controller in self.controllers:
                        config = controller.config
                        controller._fresh_price(config.connector_name, config.trading_pair, config.initial_anchor)
                        for reference in config.references:
                            controller._fresh_price(config.reference_connector, reference.pair)
                    return
                except (ValueError, EnvironmentError):
                    await asyncio.sleep(.1)

    async def close(self):
        if not self.task.done():
            self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)
        for config in self.candles:
            self.provider.release_candle_feed(config, self)
        for name, pairs in self.references.items():
            await self.provider.release_order_books(name, sorted(pairs), self)
