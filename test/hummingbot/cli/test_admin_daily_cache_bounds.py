"""Original observation age, filter identity and clocks bound every reuse."""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import OperationalError, ProgrammingError, TimeoutError as PoolTimeout

from hummingbot.cli.admin_daily_cache import DailyStatisticsCache, transient_reporting_error
from hummingbot.model.db_migration.postgres.io_deadline import DatabaseIOTimeout


class Clock:
    wall = datetime(2026, 10, 29, 16, 59, 58, tzinfo=timezone.utc).timestamp()
    monotonic = 100

    def advance(self, seconds):
        self.wall += seconds
        self.monotonic += seconds


def cache_and_snapshot(ttl=5, age=0, filters=None, zone='UTC'):
    clock = Clock()
    if zone == 'UTC':
        clock.wall += 7 * 3600
    cache = DailyStatisticsCache(ttl, wall_clock=lambda: clock.wall, monotonic_clock=lambda: clock.monotonic)
    filters = filters or {}
    key = cache.key(filters, zone)
    body = dict(reporting_timezone=zone, start_day=key[1], end_day=key[2], freshness='fresh',
                server_time=datetime.fromtimestamp(clock.wall - age, timezone.utc).isoformat())
    cache.remember(key, body)
    return cache, clock, key, body


def test_stale_retry_never_extends_original_age_or_becomes_fresh():
    cache, clock, key, body = cache_and_snapshot(age=10)
    clock.advance(5)
    assert cache.get(key) is None
    stale = cache.fallback(key)
    assert stale['observation_age_seconds'] == 15
    assert stale['server_time'] == body['server_time']
    clock.advance(4)
    assert cache.get(key)['freshness'] == 'stale'
    clock.advance(1)
    assert cache.get(key) is None
    assert cache.fallback(key)['observation_age_seconds'] == 20
    clock.advance(39.5)
    stale = cache.fallback(key)
    assert stale['retry_after_seconds'] == 1
    clock.advance(.5)
    assert cache.get(key) is cache.fallback(key) is None
    assert body['freshness'] == 'fresh', 'Never mutate the successful response into an error snapshot'


@pytest.mark.parametrize('axis,delta', [
    ('wall', -.001), ('monotonic', -.001), ('wall', 61), ('monotonic', 61),
    ('wall', float('nan')), ('monotonic', float('inf')),
])
def test_reversed_invalid_or_expired_clocks_cannot_reuse_an_observation(axis, delta):
    cache, clock, key, _ = cache_and_snapshot()
    setattr(clock, axis, getattr(clock, axis) + delta)
    assert cache.get(key) is cache.fallback(key) is None


@pytest.mark.parametrize('age', [-1, 60, 61])
def test_future_or_already_old_snapshots_are_never_retained(age):
    cache, _, key, _ = cache_and_snapshot(age=age)
    assert cache.get(key) is cache.fallback(key) is None


@pytest.mark.parametrize('zone', ['UTC', 'Asia/Ho_Chi_Minh'])
def test_relative_reporting_day_rollover_does_not_reuse_previous_range(zone):
    cache, clock, key, _ = cache_and_snapshot(ttl=60, zone=zone)
    clock.advance(3)
    assert cache.key({}, zone) != key
    assert cache.get(cache.key({}, zone)) is None


def test_explicit_range_survives_midnight_within_its_original_age_limit():
    filters = dict(start_day='2026-10-28', end_day='2026-10-29')
    cache, clock, key, body = cache_and_snapshot(filters=filters, zone='Asia/Ho_Chi_Minh')
    clock.advance(3)
    assert cache.key(filters, 'Asia/Ho_Chi_Minh') == key
    assert cache.get(key) == body


def test_cache_is_bounded_and_never_reuses_evicted_or_mismatched_data():
    cache, _, key, body = cache_and_snapshot()
    for i in range(40):
        other = (*key[:-1], f'COIN{i}-USDT')
        cache.remember(other, body)
    assert len(cache.entries) == 32 and cache.fallback(key) is None
    wrong = (key[0], '2025-01-01', '2025-01-02', *key[3:])
    cache.remember(wrong, body)
    assert cache.get(wrong) is None
    assert cache.get(('Asia/Ho_Chi_Minh', *key[1:])) is None


def test_successful_refresh_restores_freshness_and_resets_only_to_new_observation():
    cache, clock, key, body = cache_and_snapshot()
    clock.advance(5)
    assert cache.fallback(key)['freshness'] == 'stale'
    clock.advance(5)
    body = {**body, 'server_time': datetime.fromtimestamp(clock.wall, timezone.utc).isoformat()}
    cache.remember(key, body)
    assert cache.get(key)['freshness'] == 'fresh'
    assert cache.get(key)['server_time'] == body['server_time']


@pytest.mark.parametrize('error,transient', [
    (TimeoutError(), True), (PoolTimeout(), True),
    (OperationalError('query', {}, SimpleNamespace(pgcode='57014')), True),
    (OperationalError('query', {}, SimpleNamespace(pgcode='08006')), True),
    (OperationalError('query', {}, DatabaseIOTimeout('redacted')), True),
    (OperationalError('query', {}, Exception(), connection_invalidated=True), True),
    (OperationalError('query', {}, SimpleNamespace(pgcode='28P01'), connection_invalidated=True), False),
    (OperationalError('query', {}, SimpleNamespace(pgcode='42P01')), False),
    (OperationalError('query', {}, Exception()), False),
    (ProgrammingError('query', {}, Exception()), False), (ValueError(), False),
])
def test_only_identifiable_transient_failures_allow_fallback(error, transient):
    assert transient_reporting_error(error) is transient


@pytest.mark.parametrize('operation', ['get', 'fallback'])
def test_expiry_between_lookup_and_eviction_is_a_miss_not_an_api_error(monkeypatch, operation):
    cache, clock, key, _ = cache_and_snapshot(age=.1, filters=dict(start_day='2026-10-29', end_day='2026-10-30'))
    clock.advance(59.999)
    lookup = cache.entries.get

    def paused_lookup(key):
        entry = lookup(key)
        # A scheduler/GC pause may cross cachetools' TTL after successful lookup.
        clock.advance(.002)
        return entry

    monkeypatch.setattr(cache.entries, 'get', paused_lookup)
    assert getattr(cache, operation)(key) is None
