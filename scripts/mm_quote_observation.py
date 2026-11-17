"""Read-only timed quote observations; failed/old reads never become fresh data.

The caller owns authentication. No credentials, URLs or exception messages are
stored here. Classification/availability targets remain the observer's concern.
"""
import asyncio
import hashlib
import math
import re
import time
import uuid
from datetime import datetime, timezone


async def read_observed_json(session, url, *, timing):
    """Opt-in HTTP evidence; retain only opaque IDs and reviewed numeric timings."""
    identity = uuid.uuid4().hex
    timing.update(client_request_hash=hashlib.sha256(identity.encode()).hexdigest()[:16],
                  client_phase='awaiting_headers')
    async with session.get(url, headers={'X-Request-ID': identity}, allow_redirects=False) as response:
        timing.update(http_status=response.status, client_phase='body')
        server_id = response.headers.get('X-Request-ID', '')
        if re.fullmatch('[0-9a-f]{32}', server_id):
            timing['server_request_id'] = server_id
        header = response.headers.get('Server-Timing', '')
        metrics = {}
        if len(header) <= 1024:
            for part in header.split(','):
                match = re.fullmatch(r'\s*(status|db_queue|db_run);dur=([0-9.]+)(?:;desc="(running|finished)")?\s*', part)
                if match:
                    try:
                        value = float(match[2])
                    except ValueError:
                        continue
                    if math.isfinite(value) and 0 <= value <= 86400000:
                        metrics[match[1]] = value
                        if match[3]:
                            timing['database_phase'] = match[3]
        timing['server_timing_ms'] = metrics
        response.raise_for_status()
        if response.status != 200:
            raise ValueError('Unexpected observation response status')
        value = await response.json()
        timing['client_phase'] = 'complete'
        return value


async def collect_quote_observation(read, *, timeout=10, wall_clock=None, clock=time.monotonic, read_with_timing=False):
    wall_clock = wall_clock or (lambda: datetime.now(timezone.utc))

    async def timed(source, path):
        started_at, started = wall_clock().isoformat(), clock()
        error, value = None, None
        transport = {}
        try:
            operation = read(path, timing=transport) if read_with_timing else read(path)
            value = await asyncio.wait_for(operation, timeout=timeout)
            if not isinstance(value, dict):
                raise TypeError('Expected an observation object')
        except Exception as exception:
            value, error = None, type(exception).__name__
        return source, value, dict(started_at=started_at, finished_at=wall_clock().isoformat(),
                                   elapsed_ms=(clock() - started) * 1000, error_type=error, **transport)

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
