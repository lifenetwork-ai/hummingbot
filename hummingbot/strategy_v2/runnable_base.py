import asyncio
import inspect
import logging
import time
from abc import ABC

from hummingbot.core.utils.async_utils import safe_ensure_future
from hummingbot.logger import HummingbotLogger
from hummingbot.strategy_v2.models.base import RunnableStatus


class RunnableBase(ABC):
    """
    Base class for smart components in the Hummingbot application.
    This class provides a basic structure for components that need to perform tasks at regular intervals.
    """
    _logger = None

    @classmethod
    def logger(cls) -> HummingbotLogger:
        if cls._logger is None:
            cls._logger = logging.getLogger(__name__)
        return cls._logger

    def __init__(self, update_interval: float = 0.5):
        """
        Initialize a new instance of the SmartComponentBase class.

        :param update_interval: The interval at which the control loop should be executed, in seconds.
        """
        self.update_interval = update_interval
        self._status: RunnableStatus = RunnableStatus.NOT_STARTED
        self.terminated = asyncio.Event()
        self._control_progress_at = None
        self._control_error = None
        self._control_stage = "not_started"
        self._control_wait_until = None

    @property
    def status(self):
        """
        Get the current status of the smart component.

        :return: The current status of the smart component.
        """
        return self._status

    def start(self):
        """
        Start the control loop of the smart component.
        If the component is not already started, it will start the control loop.
        """
        if self._status == RunnableStatus.NOT_STARTED:
            self.terminated.clear()
            self._status = RunnableStatus.RUNNING
            safe_ensure_future(self.control_loop())

    def stop(self):
        """
        Stop the control loop of the smart component.
        If the component is active or not started, it will stop the control loop.
        """
        if self._status != RunnableStatus.TERMINATED:
            self._status = RunnableStatus.TERMINATED
            self.terminated.set()

    async def control_loop(self):
        """
        The main control loop of the smart component.
        This method is responsible for executing the control task at the specified interval.
        """
        try:
            self._control_progress_at = time.monotonic()
            self._control_stage = "startup"
            await self.on_start()
            while not self.terminated.is_set():
                try:
                    self._control_progress_at = time.monotonic()
                    self._control_stage = "control"
                    await self.control_task()
                except Exception as e:
                    self._control_error = type(e).__name__
                    self.logger().error(e, exc_info=True)
                self._control_progress_at = time.monotonic()
                self._control_stage = "sleep"
                if not self.terminated.is_set():
                    await asyncio.sleep(self.update_interval)
        except Exception as error:
            self._control_error = type(error).__name__
            raise
        finally:
            self._control_stage = "stopped"
            cleanup = self.on_stop()
            if inspect.isawaitable(cleanup):
                await cleanup

    def on_stop(self):
        """
        Method to be executed when the control loop is stopped.
        This method should be overridden in subclasses to provide specific behavior.
        """
        pass

    async def on_start(self):
        """
        Method to be executed when the control loop is started.
        This method should be overridden in subclasses to provide specific behavior.
        """
        pass

    async def control_task(self):
        """
        The main task to be executed in the control loop.
        This method should be overridden in subclasses to provide specific behavior.
        """
        pass
