"""OKX connector lifetimes whose native Clock shutdown can be awaited."""
import asyncio

from hummingbot.connector.derivative.okx_perpetual.okx_perpetual_derivative import OkxPerpetualDerivative
from hummingbot.connector.exchange.okx.okx_exchange import OkxExchange


class JoinedNetworkShutdown:
    def stop_network(self):
        # NetworkIterator.c_stop calls this synchronously and schedules the
        # awaitable it returns. Capture that exact task before yielding, so a
        # headless host need not guess which global tasks belong to a connector.
        task = asyncio.create_task(super().stop_network())
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
            results = await asyncio.gather(*batch, return_exceptions=True)
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
