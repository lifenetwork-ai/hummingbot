"""Read-only history pages should not monopolize connections or ORM identity maps."""
from test.integration.postgres import test_admin_history as history_tests

import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session

from hummingbot.model import admin_store
from hummingbot.model.order import Order
from hummingbot.model.trade_fill import TradeFill

history = history_tests.history


@pytest.mark.parametrize('method', ['orders', 'fills'])
def test_history_page_returns_connection_before_formatting_loaded_values(history, monkeypatch, method):
    assert history.engine.pool.checkedout() == 0
    timestamp = admin_store._timestamp
    observed = []

    def format_timestamp(value):
        observed.append(history.engine.pool.checkedout())
        return timestamp(value)

    monkeypatch.setattr(admin_store, '_timestamp', format_timestamp)
    page = getattr(history, method)(limit=5)
    assert len(page['items']) == 5 and page['next_cursor']
    assert observed and set(observed) == {0}
    assert history.engine.pool.checkedout() == 0


@pytest.mark.parametrize('method', ['orders', 'fills'])
def test_history_page_avoids_persistent_entity_allocation_for_scalar_output(history, method):
    loaded = []

    def load(session, instance):
        if isinstance(instance, (Order, TradeFill)):
            loaded.append(type(instance).__name__)

    event.listen(Session, 'loaded_as_persistent', load)
    try:
        page = getattr(history, method)(limit=5)
    finally:
        event.remove(Session, 'loaded_as_persistent', load)
    assert len(page['items']) == 5 and page['next_cursor']
    assert loaded == []
