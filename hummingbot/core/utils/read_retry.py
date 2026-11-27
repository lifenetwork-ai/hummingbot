"""Retry handling for explicitly read-only venue operations."""
import asyncio
import math
import random
import re
import time
from email.utils import parsedate_to_datetime

from aiohttp import ClientConnectionError, ClientResponseError


def _http_status(error):
    if isinstance(error, ClientResponseError):
        return error.status
    # RESTAssistant's existing error format retains status, but no headers.
    status = re.search(r"HTTP status is (\d{3})", str(error))
    return int(status[1]) if status else None


def transient_read(error):
    if isinstance(error, ClientResponseError):
        return error.status in (408, 429) or 500 <= error.status < 600
    if isinstance(error, (TimeoutError, ClientConnectionError)):
        return True
    if isinstance(error, OSError):
        status = _http_status(error)
        return status is None or status in (408, 429) or 500 <= status < 600
    return False


async def shutdown_order_read(fetch, *args, deadline=None, **kwargs):
    """Retry brief venue gateway failures inside the existing shutdown deadline."""
    if deadline is None:
        return await fetch(*args, **kwargs)
    loop = asyncio.get_running_loop()
    for delay in (.15, .35, None):
        try:
            return await fetch(*args, **kwargs)
        except (OSError, ClientResponseError) as error:
            if delay is None or _http_status(error) not in (502, 503, 504) or loop.time() + delay >= deadline:
                raise
            await asyncio.sleep(delay)
            if loop.time() >= deadline:
                raise


def retry_delay(attempt, error=None):
    delay = min(30, 2 ** min(max(0, attempt - 1), 5)) * random.uniform(.8, 1)
    headers = getattr(error, "headers", None)
    value = headers.get("Retry-After") if headers else None
    if value is not None:
        try:
            minimum = float(value)
        except ValueError:
            try:
                minimum = parsedate_to_datetime(value).timestamp() - time.time()
            except (ValueError, TypeError, OverflowError):
                minimum = 0
        if math.isfinite(minimum):
            delay = max(delay, minimum)
    return delay
