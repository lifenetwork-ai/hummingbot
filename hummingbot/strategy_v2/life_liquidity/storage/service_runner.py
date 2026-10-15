"""Repository-only V2 runner: controller queue, no interactive application."""
import asyncio

from hummingbot.strategy.strategy_v2_base import StrategyV2Base
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError


class FileServiceRunner(StrategyV2Base):
    def __init__(self, service):
        self.service = service
        self._started_headless = False
        super().__init__(service.connectors,
                         repository_reporting={service.config.controller.id: service.reporting},
                         dispatch_max_pending=service.config.max_pending,
                         dispatch_shutdown_timeout_s=service.config.shutdown_timeout_s)

    def start(self, clock, timestamp):
        if self._is_stop_triggered or self.service._revoked or self._started_headless:
            raise StorageError('SERVICE_RUNNER_ALREADY_STARTED_OR_STOPPED')
        self._started_headless = True
        self._last_timestamp = timestamp
        # Controllers own planning. Neither process start nor connector readiness
        # provides activation, MQTT/UI state, a financial policy or a send grant.
        for controller in self.controllers.values():
            controller.start()

    def stop(self, clock):
        # Clock teardown is synchronous. Revoke now; the owning service joins
        # controller/dispatcher/recorder/file-worker tasks in close().
        self._is_stop_triggered = True
        self.service.revoke()
        for controller in self.controllers.values():
            controller.stop()

    def create_actions_proposal(self):
        return []

    def stop_actions_proposal(self):
        return []

    async def on_stop(self):
        self.stop(None)
        tasks = set()
        for controller in self.controllers.values():
            for name in ('_control_loop_task', 'order_safety_watchdog_task', 'order_safety_task',
                         '_joint_reconciliation_task', '_fee_refresh_task', '_perpetual_poll_task'):
                task = getattr(controller, name, None)
                if isinstance(task, asyncio.Task) and task is not asyncio.current_task():
                    tasks.add(task)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await super().on_stop()
