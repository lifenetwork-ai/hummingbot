"""Driver deadlines do not slide forward as partial data or signals arrive."""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from psycopg2.extensions import POLL_OK, POLL_READ, POLL_WRITE

from hummingbot.client.config.runtime_config import RuntimeConfigurationError
from hummingbot.model.db_migration.postgres import io_deadline


class Connection:
    def __init__(self, states):
        self.poll = MagicMock(side_effect=states)
        self.fileno = lambda: 17
        self.io_timeout, self.io_deadline = 3, None
        self.close = MagicMock()
        self.dsn = 'private-dsn-must-not-appear'


@pytest.fixture
def managed(monkeypatch):
    monkeypatch.setattr(io_deadline, 'DeadlineConnection', Connection)
    return Connection


def test_partial_read_progress_cannot_extend_operation_deadline(managed, monkeypatch, caplog):
    connection = managed([POLL_READ, POLL_READ, POLL_READ])
    clock = iter([100, 101, 102.5, 103.1])
    monkeypatch.setattr(io_deadline.time, 'monotonic', lambda: next(clock))
    wait = MagicMock(return_value=([17], [], []))
    monkeypatch.setattr(io_deadline.select, 'select', wait)
    with pytest.raises(io_deadline.DatabaseIOTimeout):
        io_deadline.bounded_wait(connection)
    assert [call.args[3] for call in wait.call_args_list] == [2, .5]
    # Psycopg closes on callback failure; never re-enter its held mutex here.
    connection.close.assert_not_called()
    assert len(caplog.records) == 1 and caplog.records[0].event == 'postgres_io_timeout'
    assert 'private-dsn' not in caplog.text


def test_idle_socket_deadline_does_not_spin_poll(managed, monkeypatch):
    connection = managed([POLL_READ])
    monkeypatch.setattr(io_deadline.select, 'select', MagicMock(return_value=([], [], [])))
    with pytest.raises(io_deadline.DatabaseIOTimeout):
        io_deadline.bounded_wait(connection)
    assert connection.poll.call_count == 1


def test_ready_write_then_read_keeps_connection_open(managed, monkeypatch):
    connection = managed([POLL_WRITE, POLL_READ, POLL_OK])
    wait = MagicMock(side_effect=[([], [17], []), ([17], [], [])])
    monkeypatch.setattr(io_deadline.select, 'select', wait)
    io_deadline.bounded_wait(connection)
    assert wait.call_args_list[0].args[:3] == ([], [17], [])
    assert wait.call_args_list[1].args[:3] == ([17], [], [])
    connection.close.assert_not_called()


def test_signal_interruption_does_not_reset_deadline(managed, monkeypatch):
    connection = managed([POLL_READ, POLL_READ])
    clock = iter([100, 100.5, 103.1])
    monkeypatch.setattr(io_deadline.time, 'monotonic', lambda: next(clock))
    wait = MagicMock(side_effect=InterruptedError)
    monkeypatch.setattr(io_deadline.select, 'select', wait)
    with pytest.raises(io_deadline.DatabaseIOTimeout):
        io_deadline.bounded_wait(connection)
    assert wait.call_count == 1


def test_foreign_callback_is_not_silently_replaced(monkeypatch):
    monkeypatch.setattr(io_deadline, 'get_wait_callback', lambda: object())
    install = MagicMock()
    monkeypatch.setattr(io_deadline, 'set_wait_callback', install)
    with pytest.raises(RuntimeConfigurationError, match='incompatible'):
        io_deadline.install_io_deadline()
    install.assert_not_called()


def test_bootstrap_long_budget_restores_runtime_limit_on_failure(managed, monkeypatch):
    raw = managed([])
    monkeypatch.setattr(io_deadline.time, 'monotonic', lambda: 100)
    connection = SimpleNamespace(connection=SimpleNamespace(driver_connection=raw))
    with pytest.raises(ValueError):
        with io_deadline.bootstrap_io_budget(connection, 280):
            assert raw.io_timeout == 180 and raw.io_deadline < 280
            outer = raw.io_deadline
            with io_deadline.bootstrap_io_budget(connection, 400):
                assert raw.io_deadline == outer
            raise ValueError('DDL failure')
    assert raw.io_timeout == 3 and raw.io_deadline is None
