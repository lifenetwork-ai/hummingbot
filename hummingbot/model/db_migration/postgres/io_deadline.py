"""Bound libpq response waits without abandoning a worker or reusing its session.

Use Psycopg's documented wait callback, preserving synchronous DBAPI semantics.
On a callback exception Psycopg discards the physical session (green_panic);
closing it from the callback itself would re-enter the held connection lock.
"""
import logging
import select
import time
from contextlib import contextmanager

from psycopg2 import OperationalError
from psycopg2.extensions import POLL_OK, POLL_READ, POLL_WRITE, connection, get_wait_callback, set_wait_callback
from psycopg2.extras import wait_select

from hummingbot.client.config.runtime_config import RuntimeConfigurationError

RUNTIME_IO_SECONDS = 3.0
CONNECT_IO_SECONDS = 5.0


def response_timeout():
    message = 'PostgreSQL response deadline exceeded; session discarded.'
    logging.getLogger(__name__).warning(message, extra={'event': 'postgres_io_timeout'})
    return DatabaseIOTimeout(message)


class DatabaseIOTimeout(OperationalError):
    """No SQL text, parameters, connection URL or credentials in the exception."""


class DeadlineConnection(connection):
    def __init__(self, *args, **kwargs):
        self.io_timeout = CONNECT_IO_SECONDS
        self.io_deadline = None
        super().__init__(*args, **kwargs)
        self.io_timeout = RUNTIME_IO_SECONDS


def bounded_wait(conn):
    if not isinstance(conn, DeadlineConnection):
        return wait_select(conn)
    deadline = time.monotonic() + conn.io_timeout
    if conn.io_deadline is not None:
        deadline = min(deadline, conn.io_deadline)
    while True:
        state = conn.poll()
        if state == POLL_OK:
            return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise response_timeout()
        try:
            if state == POLL_READ:
                ready = select.select([conn.fileno()], [], [], remaining)[0]
            elif state == POLL_WRITE:
                ready = select.select([], [conn.fileno()], [], remaining)[1]
            else:
                raise OperationalError('Unexpected PostgreSQL I/O polling state.')
        except InterruptedError:
            continue
        if not ready:
            raise response_timeout()


def install_io_deadline():
    previous = get_wait_callback()
    if previous not in (None, bounded_wait, wait_select):
        raise RuntimeConfigurationError('PostgreSQL wait callback is incompatible with bounded service I/O.')
    set_wait_callback(bounded_wait)


@contextmanager
def bootstrap_io_budget(connection, deadline):
    """Keep the existing total bootstrap budget instead of clipping DDL to 3s."""
    raw = connection.connection.driver_connection
    if not isinstance(raw, DeadlineConnection):
        yield
        return
    previous = raw.io_timeout, raw.io_deadline
    remaining = max(0, deadline - time.monotonic())
    raw.io_timeout = max(CONNECT_IO_SECONDS, remaining)
    # Finish before the runner's cancellation watchdog, including a lost reply
    # to COMMIT/ROLLBACK. The callback failure discards rather than reconnects.
    limit = deadline - min(.1, remaining * .05)
    raw.io_deadline = min(limit, previous[1]) if previous[1] is not None else limit
    try:
        yield
    finally:
        raw.io_timeout, raw.io_deadline = previous
