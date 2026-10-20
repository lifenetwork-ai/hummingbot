"""OKX connector lifetimes whose native Clock shutdown can be awaited."""
import asyncio

from hummingbot.connector.derivative.okx_perpetual.okx_perpetual_derivative import OkxPerpetualDerivative
from hummingbot.connector.exchange.okx.okx_exchange import OkxExchange
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError


class JoinedNetworkShutdown:
    def retain_network_until(self, owner):
        if not isinstance(owner, asyncio.Task) or owner.get_loop() is not asyncio.get_running_loop():
            raise StorageError('HEADLESS_NETWORK_LIFETIME_INVALID')
        original = self.__dict__.get('_headless_network_owner')
        if original is not None and original is not owner and not original.done():
            raise StorageError('HEADLESS_NETWORK_LIFETIME_CHANGED')
        self._headless_network_owner = owner

    def stop_network(self):
        # NetworkIterator.c_stop calls this synchronously and schedules the
        # awaitable it returns. Capture that exact task before yielding, so a
        # headless host need not guess which global tasks belong to a connector.
        parent_stop = super().stop_network
        owner = self.__dict__.get('_headless_network_owner')

        async def physical_stop():
            if owner is not None:
                # Failure means uncertainty, not permission to interrupt a
                # still-running original ACK/DB task. Join its termination.
                await asyncio.shield(asyncio.gather(owner, return_exceptions=True))
            await parent_stop()
        task = asyncio.create_task(physical_stop())
        tasks = self.__dict__.setdefault('_headless_network_stops', set())
        tasks.add(task)
        task.add_done_callback(self._network_stop_done)
        return task

    def _network_stop_done(self, task):
        self.__dict__['_headless_network_stops'].discard(task)
        error = asyncio.CancelledError() if task.cancelled() else task.exception()
        if error is not None:
            self.__dict__.setdefault('_headless_network_failure', error)

    async def join_network_shutdown(self):
        tasks = self.__dict__.setdefault('_headless_network_stops', set())
        while tasks:
            batch = tuple(tasks)
            # A bounded host wait may expire while these original tasks still
            # retain SDK resources. It cannot cancel the physical network stop.
            results = await asyncio.shield(asyncio.gather(*batch, return_exceptions=True))
            tasks.difference_update(batch)
            for result in results:
                if isinstance(result, BaseException):
                    raise result
        if '_headless_network_failure' in self.__dict__:
            raise self.__dict__['_headless_network_failure']


class HeadlessOkxExchange(JoinedNetworkShutdown, OkxExchange):
    pass


class HeadlessOkxPerpetualDerivative(JoinedNetworkShutdown, OkxPerpetualDerivative):
    pass
