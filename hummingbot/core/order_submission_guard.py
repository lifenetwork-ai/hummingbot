"""One process-level send gate for the single PostgreSQL bot.

The connector persists an intent, and REST rechecks ownership after throttling and
authentication, immediately before transport. Only the reviewed OKX connectors are
enabled for the service. Legacy tests/tooling without PG retain existing behavior.
"""
import logging
from contextlib import contextmanager
from contextvars import ContextVar

from hummingbot.client.config.runtime_config import postgres_requested

_guard = None
SUPPORTED_CONNECTORS = ("okx", "okx_perpetual")
_session_scope = ContextVar("mm_order_session_scope", default=None)
_quote_level_scope = ContextVar("mm_order_quote_level", default=None)


@contextmanager
def session_order_scope(session_id, generation, *, level_id=None):
    """Carry executor authority into connector tasks, including delayed retries."""
    token = _session_scope.set((session_id, generation) if session_id is not None else None)
    level_token = _quote_level_scope.set(level_id if session_id is not None else None)
    try:
        yield
    finally:
        _session_scope.reset(token)
        _quote_level_scope.reset(level_token)


def current_session_scope():
    return _session_scope.get()


def current_quote_level():
    return _quote_level_scope.get()


class SubmissionBlocked(RuntimeError):
    pass


class SessionSubmissionBlocked(SubmissionBlocked):
    """A stopped/superseded session or shutdown no longer permits submission."""


class MarketReadinessBlocked(SubmissionBlocked):
    """Required market/data/exposure readiness currently prevents submission."""


class QuoteRefreshRequired(SubmissionBlocked):
    """The fresh pre-transport spread check rejected a configured entry quote."""


def submission_block_log_context(error_type):
    """Classify the existing failure-event type; unknown failures stay errors."""
    return {
        "SessionSubmissionBlocked": (logging.INFO, "session_submission_closed"),
        "MarketReadinessBlocked": (logging.WARNING, "market_not_ready"),
        "QuoteRefreshRequired": (logging.WARNING, "spread_policy_quote_rejected"),
        "SubmissionBlocked": (logging.ERROR, "submission_safety_blocked"),
        "OwnershipLost": (logging.ERROR, "ownership_lost"),
    }.get(error_type)


def install_guard(guard) -> None:
    global _guard
    _guard = guard


def active_guard():
    if not postgres_requested():
        return None
    if _guard is None:
        raise SubmissionBlocked("PostgreSQL trading requires the service ownership gate.")
    return _guard


async def before_order(connector, order) -> None:
    guard = active_guard()
    if guard is not None:
        guard.before_order(connector, order)


async def before_rest(request):
    if not postgres_requested() or not request.is_auth_required or request.method.value in ("GET", "HEAD"):
        return None
    guard = active_guard()
    token = await guard.authorize_rest(request)
    guard.transport_started()
    return guard, token


async def after_rest(context, response) -> None:
    if context is not None:
        guard, token = context
        if token is not None:
            await guard.finish_account_mutation(token, response)


def failed_rest(context) -> None:
    if context is not None:
        guard, token = context
        if token is not None:
            guard.account_mutation_failed(token)
        else:
            guard.require_recovery("An exchange mutation transport ended without a confirmed outcome.")


def completed_rest(context) -> None:
    if context is not None:
        context[0].transport_finished()


def order_failed(connector, order, exception=None) -> bool:
    """Return true only when a managed transport may still have venue effects."""
    if postgres_requested() and _guard is not None:
        if exception is None:
            return bool(_guard.order_failed(connector, order))
        else:
            return bool(_guard.order_failed(connector, order, exception))
    return False


def fill_committed(row) -> None:
    if postgres_requested() and _guard is not None:
        _guard.fill_committed(row)


def spot_buy_preview(connector, amount, price):
    if not postgres_requested() or _guard is None or _guard.spot_outflow is None:
        return True
    return _guard.spot_outflow.preview(connector, amount, price)


def fence_session(session) -> None:
    guard = active_guard()
    if guard is not None:
        guard.fence_session(session)
