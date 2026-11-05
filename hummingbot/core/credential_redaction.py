"""Short-lived candidate redaction; loaded values come from the existing Security cache."""
import sys
from contextlib import contextmanager
from contextvars import ContextVar

_candidate = ContextVar("credential_redaction_values", default=())


@contextmanager
def redact_candidate(values):
    token = _candidate.set(tuple(value for value in values if isinstance(value, str)))
    try:
        yield
    finally:
        _candidate.reset(token)


def sensitive_values():
    module = sys.modules.get("hummingbot.client.config.security")
    security = getattr(module, "Security", None)
    return (*_candidate.get(), *getattr(security, "_redaction_secrets", ()))
