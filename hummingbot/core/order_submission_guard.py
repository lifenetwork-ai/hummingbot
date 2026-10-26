"""One process-level send gate for the single PostgreSQL bot.

The connector persists an intent, and REST rechecks ownership after throttling and
authentication, immediately before transport. Only the reviewed OKX connectors are
enabled for the service. Legacy tests/tooling without PG retain existing behavior.
"""
from contextlib import contextmanager
from contextvars import ContextVar

from hummingbot.client.config.runtime_config import postgres_requested

_guard = None
SUPPORTED_CONNECTORS = ("okx", "okx_perpetual")
_session_scope = ContextVar("mm_order_session_scope", default=None)


@contextmanager
def session_order_scope(session_id, generation):
    """Carry executor authority into connector tasks, including delayed retries."""
    token = _session_scope.set((session_id, generation) if session_id is not None else None)
    try:
        yield
    finally:
        _session_scope.reset(token)


def current_session_scope():
    return _session_scope.get()


class SubmissionBlocked(RuntimeError):
    pass


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


def order_failed(connector, order) -> None:
    if postgres_requested() and _guard is not None:
        _guard.order_failed(connector, order)


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
