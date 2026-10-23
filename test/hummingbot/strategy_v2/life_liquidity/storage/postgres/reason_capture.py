"""Task-local test diagnostics; aliases do not retain pytest request/items."""
from contextlib import contextmanager
from contextvars import ContextVar

_codes = ContextVar('life_test_pg_reason_codes', default=None)


@contextmanager
def capture_codes(codes):
    token = _codes.set(codes)
    try:
        yield
    finally:
        _codes.reset(token)


def normalize_codes(original):
    def normalized(error):
        result = original(error)
        codes = _codes.get()
        if codes is not None:
            codes.append(str(result)[:128])
        return result
    return normalized
