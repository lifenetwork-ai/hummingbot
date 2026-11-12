"""Read-only timed quote observations; failed/old reads never become fresh data.

The caller owns authentication. No credentials, URLs or exception messages are
stored here. Classification/availability targets remain the observer's concern.
"""
import asyncio
import time
from datetime import datetime, timezone


async def collect_quote_observation(read, *, timeout=10, wall_clock=None, clock=time.monotonic):
    wall_clock = wall_clock or (lambda: datetime.now(timezone.utc))

    async def timed(source, path):
        started_at, started = wall_clock().isoformat(), clock()
        error, value = None, None
        try:
            value = await asyncio.wait_for(read(path), timeout=timeout)
            if not isinstance(value, dict):
                raise TypeError('Expected an observation object')
        except Exception as exception:
            value, error = None, type(exception).__name__
        return source, value, dict(started_at=started_at, finished_at=wall_clock().isoformat(),
                                   elapsed_ms=(clock() - started) * 1000, error_type=error)

    reads = await asyncio.gather(timed('status', '/status'), timed('liquidity', '/statistics/liquidity'))
    now = wall_clock()
    result = dict(at=now.isoformat(), reads={}, sample_errors=[], observation_ages_seconds={})
    observations = {}
    for source, value, timing in reads:
        result[source], result['reads'][source] = value, timing
        if timing['error_type']:
            result['sample_errors'].append(dict(source=source, error_type=timing['error_type']))
        stamp = ((value.get('observation') or {}).get('observed_at') if source == 'status'
                 else value.get('observed_at')) if value else None
        try:
            parsed = datetime.fromisoformat(stamp.replace('Z', '+00:00'))
            age = (now - parsed).total_seconds()
            observations[source] = parsed
        except (AttributeError, TypeError, ValueError):
            age = None
        result['observation_ages_seconds'][source] = age
    result['observation_skew_seconds'] = None
    if len(observations) == 2:
        result['observation_skew_seconds'] = abs((observations['status'] - observations['liquidity']).total_seconds())
    return result
