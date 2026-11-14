"""Bounded optional fee reads sharing the existing heavy database lane."""
import asyncio
import math
import time

from hummingbot.cli.admin_overview import unavailable
from hummingbot.cli.admin_overview_models import OverviewObservation, OverviewSessionFees


class OverviewFeeCache:
    READ_WAIT_SECONDS = .2

    def __init__(self, *, clock=time.monotonic):
        self.clock = clock
        self.cache = {}
        self.flights = {}
        self.closed = False

    async def read(self, row, overview, query):
        if self.closed:
            return unavailable('session_fees_unavailable')
        try:
            scope = overview.account_scope
            now = float(row['database_now'])
            age = float(row['heartbeat_age_s'])
            if (not scope.identity_verified or not row.get('owner_uuid') or not 0 <= age <= 5
                    or not overview.session_id or overview.session_started_at is None
                    or not math.isfinite(now) or not isinstance(row.get('bot_id'), str)):
                return unavailable('session_fee_scope_unavailable')
            args = dict(bot_id=row['bot_id'], session_id=overview.session_id, account_ids=list(scope.account_ids),
                        environment=scope.environment, start_ms=int(overview.session_started_at.timestamp() * 1000), end_ms=int(now * 1000))
            if args['end_ms'] <= args['start_ms']:
                return unavailable('session_fee_history_unavailable')
            key = (args['bot_id'], args['session_id'], tuple(args['account_ids']), args['environment'], args['start_ms'])
        except (TypeError, ValueError, AttributeError, KeyError, OverflowError):
            return unavailable('session_fee_scope_unavailable')
        cached = self.cache.get(key)
        if cached is not None and cached[0] > self.clock():
            return cached[1].model_dump(mode='json')
        task = self.flights.get(key)
        if task is None:
            task = asyncio.create_task(self._refresh(key, args, query))
            self.flights[key] = task
        # A disconnected request cannot cancel another reader or free physical
        # database capacity; the existing executor owns that work until done.
        try:
            result = await asyncio.wait_for(asyncio.shield(task), timeout=self.READ_WAIT_SECONDS)
        except asyncio.TimeoutError:
            return unavailable('session_fees_refreshing')
        return result.model_dump(mode='json')

    async def _refresh(self, key, args, query):
        try:
            try:
                result = OverviewObservation[OverviewSessionFees].model_validate(await query(**args))
            except Exception:
                result = OverviewObservation[OverviewSessionFees](**unavailable('session_fees_unavailable'))
            if len(self.cache) >= 4 and key not in self.cache:
                self.cache.pop(next(iter(self.cache)))
            ttl = 15 if result.value is not None else 3
            self.cache[key] = (self.clock() + ttl, result)
            return result
        finally:
            self.flights.pop(key, None)

    async def close(self):
        self.closed = True
        tasks = tuple(self.flights.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.flights.clear()
        self.cache.clear()
