"""Bounded optional fee reads sharing the existing heavy database lane."""
import asyncio
import math
import time

from hummingbot.cli.admin_overview import build_overview, unavailable
from hummingbot.cli.admin_overview_models import OverviewObservation, OverviewSessionFees


class OverviewFeeCache:
    READ_WAIT_SECONDS = .2
    CATCHUP_SECONDS = .05
    MAX_AGE_SECONDS = 15
    # The query can sample DB time just after the status row was read. Keep the
    # same one-second observation tolerance used by resident market capture.
    MAX_FUTURE_SECONDS = 1

    def __init__(self, *, clock=time.monotonic):
        self.clock = clock
        self.cache = {}
        self.flights = {}
        self.closed = False
        self.maintainers = {}
        self.scope_args = {}
        self.observer = None

    def _retire(self, keep=None):
        for key in tuple(self.maintainers):
            if key != keep:
                self.maintainers.pop(key).cancel()
                self.scope_args.pop(key, None)

    def watch(self, read_row, query, *, interval=5):
        """Discover the current scope even before the first UI request."""
        if self.observer is not None or self.closed:
            return

        async def observe():
            while not self.closed:
                try:
                    row = await read_row()
                    if row:
                        overview = build_overview(row)
                        if overview is not None:
                            await self.read(row, overview, query)
                    else:
                        self._retire()
                except Exception:
                    # Reporting discovery cannot make the API/engine unavailable.
                    pass
                await asyncio.sleep(interval)

        self.observer = asyncio.create_task(observe())

    async def read(self, row, overview, query):
        if self.closed:
            return unavailable('session_fees_unavailable')
        try:
            scope = overview.account_scope
            now = float(row['database_now'])
            age = float(row['heartbeat_age_s'])
            site = getattr(scope, 'site', None)
            if (not scope.identity_verified or not row.get('owner_uuid') or not 0 <= age <= 5
                    or not overview.session_id or overview.session_started_at is None
                    or not math.isfinite(now) or not isinstance(row.get('bot_id'), str)
                    or site not in ('www', 'app', 'my') or not scope.account_ids
                    or any(not isinstance(uid, str) or not 1 <= len(uid) <= 128 or not uid.isascii()
                           or not uid.replace('-', '').isalnum() for uid in scope.account_ids)):
                self._retire()
                return unavailable('session_fee_scope_unavailable')
            args = dict(bot_id=row['bot_id'], session_id=overview.session_id, account_ids=[site + ':' + uid for uid in scope.account_ids],
                        environment=scope.environment, start_ms=int(overview.session_started_at.timestamp() * 1000), end_ms=int(now * 1000))
            if args['end_ms'] <= args['start_ms']:
                return unavailable('session_fee_history_unavailable')
            key = (args['bot_id'], args['session_id'], tuple(args['account_ids']), args['environment'], args['start_ms'])
        except (TypeError, ValueError, AttributeError, KeyError, OverflowError):
            self._retire()
            return unavailable('session_fee_scope_unavailable')
        self.scope_args[key] = args
        self._retire(key)
        cached = self.cache.get(key)
        if cached is not None and cached[0] > self.clock():
            return self._view(cached[1], now)
        task = self.flights.get(key)
        if task is None:
            task = asyncio.create_task(self._refresh(key, args, query))
            self.flights[key] = task
        # A disconnected request cannot cancel another reader or free physical
        # database capacity; the existing executor owns that work until done.
        try:
            result = await asyncio.wait_for(asyncio.shield(task), timeout=self.READ_WAIT_SECONDS)
        except asyncio.TimeoutError:
            if cached and cached[1].value is not None:
                return cached[1].model_copy(update=dict(stale=True, unavailable_reason='session_fees_refreshing')).model_dump(mode='json')
            return unavailable('session_fees_refreshing')
        return self._view(result, now)

    def _view(self, result, now):
        stamp = result.observed_at.timestamp() if result.observed_at else None
        if result.value is not None and not result.stale and (stamp is None or not -self.MAX_FUTURE_SECONDS <= now - stamp <= self.MAX_AGE_SECONDS):
            result = result.model_copy(update=dict(stale=True, unavailable_reason='observation_stale'))
        return result.model_dump(mode='json')

    async def _refresh(self, key, args, query):
        try:
            failed = False
            try:
                result = OverviewObservation[OverviewSessionFees].model_validate(await query(**args))
            except Exception:
                failed = True
                previous = self.cache.get(key)
                result = (previous[1].model_copy(update=dict(stale=True, unavailable_reason='session_fees_unavailable'))
                          if previous and previous[1].value is not None else
                          OverviewObservation[OverviewSessionFees](**unavailable('session_fees_unavailable')))
            if len(self.cache) >= 4 and key not in self.cache:
                self.cache.pop(next(iter(self.cache)))
            catching_up = (result.value is not None and result.value.processing_state == 'catching_up'
                           or result.unavailable_reason == 'session_fees_catching_up')
            if result.unavailable_reason == 'session_fee_rebuild_incomplete':
                catching_up = False
            ttl = 3 if failed else self.CATCHUP_SECONDS if catching_up else 15 if result.value is not None and not result.stale else 3
            self.cache[key] = (self.clock() + ttl, result)
            if not self.closed and key not in self.maintainers and key in self.scope_args:
                self.maintainers[key] = asyncio.create_task(self._maintain(key, query))
            return result
        finally:
            self.flights.pop(key, None)

    async def _maintain(self, key, query):
        while not self.closed and key in self.scope_args:
            cached = self.cache.get(key)
            delay = max(self.CATCHUP_SECONDS, cached[0] - self.clock()) if cached else 3
            await asyncio.sleep(delay)
            if key not in self.scope_args or self.closed:
                return
            task = self.flights.get(key)
            if task is None:
                args = {**self.scope_args[key], 'end_ms': max(self.scope_args[key]['end_ms'], int(time.time() * 1000))}
                task = asyncio.create_task(self._refresh(key, args, query))
                self.flights[key] = task
            await asyncio.shield(task)

    async def close(self):
        self.closed = True
        if self.observer is not None:
            self.observer.cancel()
            await asyncio.gather(self.observer, return_exceptions=True)
            self.observer = None
        maintainers = tuple(self.maintainers.values())
        for task in maintainers:
            task.cancel()
        await asyncio.gather(*maintainers, return_exceptions=True)
        self.maintainers.clear()
        self.scope_args.clear()
        tasks = tuple(self.flights.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.flights.clear()
        self.cache.clear()
