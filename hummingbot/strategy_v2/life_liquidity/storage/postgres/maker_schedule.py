"""Serial, bounded two-sided maker generations over the original service API.

This loop grants no financial or SDK permission. Every callback still crosses
the configured native source, ownership, economics and final-send checks.
"""
import asyncio
from dataclasses import dataclass

from hummingbot.core.data_type.common import TradeType
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError, fingerprint, identifier


@dataclass(frozen=True)
class PgMakerSchedule:
    schedule_id: str
    levels: tuple[int, ...]
    refresh_interval_ms: int
    max_cycles: int

    def __post_init__(self):
        identifier(self.schedule_id)
        if (type(self.levels) is not tuple or not self.levels
                or any(type(v) is not int or not 0 <= v < 64 for v in self.levels) or len(set(self.levels)) != len(self.levels)
                or type(self.refresh_interval_ms) is not int or not 0 < self.refresh_interval_ms <= 86400000
                or type(self.max_cycles) is not int or not 0 < self.max_cycles <= 4096):
            raise StorageError('PG_MAKER_SCHEDULE_INVALID')


class PgMakerLoop:
    def __init__(self, schedule, *, namespace, quote, cancel, ready, can_quote=None):
        if (type(schedule) is not PgMakerSchedule or type(namespace) is not str
                or any(not callable(v) for v in (quote, cancel, ready))
                or can_quote is not None and not callable(can_quote)):
            raise StorageError('PG_MAKER_SCHEDULE_INVALID')
        identifier(namespace)
        self.schedule, self.namespace = schedule, namespace
        self._quote, self._cancel, self._ready = quote, cancel, ready
        self._can_quote = (lambda remaining: ready()) if can_quote is None else can_quote
        self._context = schedule, namespace, quote, cancel, ready, self._can_quote
        self._stop = asyncio.Event()
        self.task = None
        self.completed_cycles = 0
        self.targets = ()
        self.failure = None

    def start(self):
        self._original()
        if self.task is not None or self._stop.is_set():
            raise StorageError('PG_MAKER_SCHEDULE_ALREADY_STARTED_OR_STOPPED')
        self.task = asyncio.create_task(self._run())
        # Observe the exception without converting unknown state to success.
        self.task.add_done_callback(lambda t: None if t.cancelled() else t.exception())
        return self.task

    def request_stop(self):
        self._stop.set()

    async def stop(self):
        self.request_stop()
        if self.task is not None:
            await asyncio.shield(self.task)

    def _original(self):
        if self._context != (self.schedule, self.namespace, self._quote, self._cancel, self._ready, self._can_quote):
            raise StorageError('PG_MAKER_SCHEDULE_CONTEXT_CHANGED')

    async def _run(self):
        try:
            for cycle in range(self.schedule.max_cycles):
                self._original()
                if self._stop.is_set() or self._ready() is not True:
                    break
                for level in self.schedule.levels:
                    for side in (TradeType.BUY, TradeType.SELL):
                        self._original()
                        if self._stop.is_set() or self._ready() is not True:
                            break
                        if self._can_quote(2 * len(self.schedule.levels) - len(self.targets)) is not True:
                            self.request_stop()  # Retire existing targets while ownership remains valid.
                            break
                        intent = 'maker:' + fingerprint([self.namespace, self.schedule.schedule_id, cycle, level, side.name])
                        self.targets += (intent,)  # Record original identity before the first await.
                        action = await self._quote(intent, side, level)
                        self._original()
                        if action.executor_config.id != intent:
                            raise StorageError('PG_MAKER_SCHEDULE_TARGET_CHANGED')
                    if self._stop.is_set() or self._ready() is not True:
                        break
                if self.targets and not self._stop.is_set() and self._ready() is True:
                    try:
                        await asyncio.wait_for(self._stop.wait(), self.schedule.refresh_interval_ms / 1000)
                    except TimeoutError:
                        pass
                if self._ready() is not True:
                    break  # Revocation retains original holds; never issue a new grant.
                if self.targets:
                    self._original()
                    # No subsequent generation until original signed closure,
                    # retirement, reconciliation and archival all complete.
                    result = await self._cancel(self.targets)
                    if set(result) != set(self.targets) or len(result) != len(self.targets):
                        raise StorageError('PG_MAKER_SCHEDULE_RETIREMENT_REQUIRED')
                    full_generation = len(self.targets) == 2 * len(self.schedule.levels)
                    self.targets = ()
                    self.completed_cycles += int(full_generation)
        except BaseException as error:
            self.failure = error
            raise
