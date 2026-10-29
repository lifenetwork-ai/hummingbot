"""Bounded last-good daily observations; never a trading/account-state cache."""
import math
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from cachetools import TTLCache
from sqlalchemy.exc import DBAPIError, TimeoutError as PoolTimeout

from hummingbot.model.admin_reporting import day_range
from hummingbot.model.db_migration.postgres.io_deadline import DatabaseIOTimeout

MAX_OBSERVATION_AGE_SECONDS = 60
RETRY_SECONDS = 5


def transient_reporting_error(error):
    if isinstance(error, (TimeoutError, PoolTimeout)):
        return True
    if not isinstance(error, DBAPIError):
        return False
    code = getattr(error.orig, 'pgcode', None)
    if code is not None:
        return code.startswith(('08', '53')) or code in {'57014', '57P01', '57P02', '57P03', '55P03', '40001', '40P01'}
    return error.connection_invalidated or isinstance(error.orig, DatabaseIOTimeout)


@dataclass
class Observation:
    body: dict
    observed_at: float
    saved_wall: float
    saved_monotonic: float
    failed_monotonic: float | None = None


class DailyStatisticsCache:
    """Owned by one API event loop; database workers never mutate entries."""

    def __init__(self, ttl, *, wall_clock=time.time, monotonic_clock=time.monotonic):
        self.ttl = ttl
        self.wall_clock = wall_clock
        self.monotonic_clock = monotonic_clock
        self.entries = TTLCache(maxsize=32, ttl=MAX_OBSERVATION_AGE_SECONDS, timer=monotonic_clock)

    def key(self, filters, reporting_timezone):
        wall = self.wall_clock()
        if not math.isfinite(wall):
            return None
        try:
            now = datetime.fromtimestamp(wall, timezone.utc)
        except (ValueError, OverflowError, OSError):
            return None
        start, end, *_ = day_range(filters.get('start_day'), filters.get('end_day'), reporting_timezone, now)
        return reporting_timezone, str(start), str(end), filters.get('market', 'all'), filters.get('pair')

    def remember(self, key, body):
        if key is None or self.ttl == 0:
            return
        if key[:3] != (body['reporting_timezone'], str(body['start_day']), str(body['end_day'])):
            return
        wall, monotonic = self.wall_clock(), self.monotonic_clock()
        observed = datetime.fromisoformat(body['server_time'].replace('Z', '+00:00')).timestamp()
        if not all(math.isfinite(value) for value in (wall, monotonic, observed)):
            return
        if not 0 <= wall - observed < MAX_OBSERVATION_AGE_SECONDS:
            return
        self.entries[key] = Observation(body, observed, wall, monotonic)

    def eligible(self, key):
        if key is None or self.ttl == 0:
            return None
        entry = self.entries.get(key)
        if entry is None:
            return None
        wall, monotonic = self.wall_clock(), self.monotonic_clock()
        if (not all(math.isfinite(value) for value in (wall, monotonic))
                or wall < entry.saved_wall or monotonic < entry.saved_monotonic):
            del self.entries[key]
            return None
        # Wall-clock rollback cannot rejuvenate an observation, and forward
        # jumps cannot keep it alive simply because monotonic time barely moved.
        age = max(wall - entry.observed_at,
                  entry.saved_wall - entry.observed_at + monotonic - entry.saved_monotonic)
        if not 0 <= age < MAX_OBSERVATION_AGE_SECONDS:
            del self.entries[key]
            return None
        return entry, age, monotonic

    @staticmethod
    def stale(entry, age, monotonic):
        retry = min(RETRY_SECONDS - (monotonic - entry.failed_monotonic), MAX_OBSERVATION_AGE_SECONDS - age)
        return {**entry.body, 'freshness': 'stale', 'observation_age_seconds': age,
                'retry_after_seconds': max(1, math.ceil(retry)),
                'message': 'Statistics could not be refreshed. Showing the last successful observation. Retrying shortly.'}

    def get(self, key):
        found = self.eligible(key)
        if found is None:
            return None
        entry, age, monotonic = found
        if entry.failed_monotonic is not None:
            if monotonic - entry.failed_monotonic < RETRY_SECONDS:
                return self.stale(entry, age, monotonic)
            return None
        if monotonic - entry.saved_monotonic < self.ttl:
            return entry.body
        return None

    def fallback(self, key):
        found = self.eligible(key)
        if found is None:
            return None
        entry, age, monotonic = found
        entry.failed_monotonic = monotonic
        return self.stale(entry, age, monotonic)
