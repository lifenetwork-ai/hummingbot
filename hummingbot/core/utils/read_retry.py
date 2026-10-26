"""Retry classification for explicitly read-only initialization operations."""
import math
import random
import re
import time
from email.utils import parsedate_to_datetime

from aiohttp import ClientConnectionError, ClientResponseError


def transient_read(error):
    if isinstance(error, ClientResponseError):
        return error.status in (408, 429) or 500 <= error.status < 600
    if isinstance(error, (TimeoutError, ClientConnectionError)):
        return True
    if isinstance(error, OSError):
        # RESTAssistant's existing error format retains status, but no headers.
        status = re.search(r"HTTP status is (\d{3})", str(error))
        return status is None or int(status[1]) in (408, 429) or 500 <= int(status[1]) < 600
    return False


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
