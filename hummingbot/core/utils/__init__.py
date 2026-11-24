import errno
import functools
import socket
from weakref import ref

import cachetools
import numpy as np
import pandas as pd


def async_ttl_cache(ttl: int = 3600, maxsize: int = 1):
    cache = cachetools.TTLCache(ttl=ttl, maxsize=maxsize)

    def decorator(fn):
        @functools.wraps(fn)
        async def memoize(*args, **kwargs):
            key = str((args, kwargs))
            instance = args[0] if args else kwargs.get("self")
            cached = cache.get(key)
            if cached is not None:
                instance_ref, result = cached
                if instance_ref is None or instance_ref() is instance:
                    return result

            # A new instance can reuse the address in an old instance's repr.
            # Keep only a weak reference so cached results do not keep it alive.
            try:
                instance_ref = ref(instance)
            except TypeError:
                instance_ref = None
            result = await fn(*args, **kwargs)
            cache[key] = (instance_ref, result)
            return result

        memoize.cache_clear = lambda: cache.clear()
        return memoize

    return decorator


def map_df_to_str(df: pd.DataFrame) -> pd.DataFrame:
    return df.apply(lambda series: series.map(lambda x: np.format_float_positional(x, trim="-") if isinstance(x, float) else x)).astype(str)


def detect_available_port(starting_port: int) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        current_port: int = starting_port
        while current_port < 65535:
            try:
                s.bind(("127.0.0.1", current_port))
                break
            except OSError as e:
                if e.errno == errno.EADDRINUSE:
                    current_port += 1
                    continue
        return current_port
