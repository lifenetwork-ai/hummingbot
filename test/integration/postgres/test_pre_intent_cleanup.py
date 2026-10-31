"""Admission rollback must distinguish local rejection from durable/unknown intents."""
from decimal import Decimal
from test.integration.postgres.test_ownership import owned as _owned
from test.integration.postgres.test_spot_outflow import buy, setup
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import text

from hummingbot.core.data_type.in_flight_order import OrderState
from hummingbot.core.order_submission_guard import SubmissionBlocked

owned = _owned


def bootstrap_guard(owner):
    guard = SimpleNamespace(deadlines={})

    def before_order(connector, order, row):
        guard.deadlines[order.client_order_id] = (connector.name, 1000)

    guard.before_order = before_order
    owner.listing_bootstrap = guard
    return guard


def test_quote_rejected_before_intent_releases_only_local_admission(owned):
    session, _ = setup(owned)
    bootstrap = bootstrap_guard(owned.owner)
    owned.owner.mm_quote_check = MagicMock(side_effect=SubmissionBlocked('quote_rejected'))
    with pytest.raises(SubmissionBlocked, match='quote_rejected'):
        buy(owned, session, 'rejected', amount='1')
    item = owned.market.in_flight_orders['rejected']
    owned.owner.order_failed(owned.market, item)  # Connector hook precedes tracker failure.
    item.current_state = OrderState.FAILED
    guard = owned.owner.spot_outflow
    assert guard.status()['reserved_quote'] == 0
    assert not guard.orders and not guard.terminal_pending and not bootstrap.deadlines
    assert 'rejected' not in owned.owner.active_intents
    with owned.engine.connect() as connection:
        assert connection.execute(text('SELECT count(*) FROM orders')).scalar() == 0
    owned.owner.mm_quote_check = None
    buy(owned, session, 'next', amount='1')
    assert guard.status()['reserved_quote'] == Decimal('10.10')
    assert not owned.store.read()['recovery_state'].get('halt')


def test_outflow_denial_also_rolls_back_earlier_bootstrap_admission(owned):
    session, _ = setup(owned)
    bootstrap = bootstrap_guard(owned.owner)
    with pytest.raises(SubmissionBlocked, match='spot_outflow_limit'):
        buy(owned, session, 'too-large', amount='20')
    assert not bootstrap.deadlines
    assert not owned.owner.spot_outflow.orders


@pytest.mark.parametrize('commit_first', [False, True])
def test_intent_write_error_still_gets_durable_unsent_resolution(owned, commit_first):
    session, _ = setup(owned)
    bootstrap = bootstrap_guard(owned.owner)
    real_write = owned.recorder.record_order_intent

    def failing_write(*args):
        if commit_first:
            real_write(*args)
        raise OSError('Synthetic lost intent-write outcome')

    with patch.object(owned.recorder, 'record_order_intent', side_effect=failing_write):
        with pytest.raises(OSError):
            buy(owned, session, 'write-failed', amount='1')
    owned.owner.order_failed(owned.market, owned.market.in_flight_orders['write-failed'])
    assert owned.owner.spot_outflow.status()['reserved_quote'] == 0
    assert not bootstrap.deadlines
    assert 'write-failed' not in owned.owner.active_intents
    with owned.engine.connect() as connection:
        assert connection.execute(text('SELECT count(*) FROM orders WHERE creation_pending')).scalar() == 0
    assert not owned.store.read()['recovery_state'].get('halt')


def test_failed_durable_rejection_keeps_reservation_and_requires_recovery(owned):
    session, _ = setup(owned)
    real_write = owned.recorder.record_order_intent

    def failing_write(*args):
        real_write(*args)
        raise OSError('Synthetic unknown commit acknowledgment')

    with patch.object(owned.recorder, 'record_order_intent', side_effect=failing_write):
        with pytest.raises(OSError):
            buy(owned, session, 'uncertain', amount='1')
    with patch.object(owned.recorder, 'reject_unsent_order', side_effect=OSError('DB unavailable')):
        owned.owner.order_failed(owned.market, owned.market.in_flight_orders['uncertain'])
    assert owned.owner.spot_outflow.status()['reserved_quote'] == Decimal('10.10')
    assert 'uncertain' in owned.owner.active_intents
    assert owned.store.read()['recovery_state']['halt']


def test_known_sent_order_is_never_unwound_as_pre_intent_rejection(owned):
    session, _ = setup(owned)
    item = buy(owned, session, 'sent', amount='1')
    owned.owner.sent_intents.add(item.client_order_id)
    owned.owner.mm_quote_check = MagicMock(side_effect=SubmissionBlocked('quote_changed'))
    with pytest.raises(SubmissionBlocked):
        buy(owned, session, 'sent', amount='1')
    owned.owner.order_failed(owned.market, item)
    assert owned.owner.spot_outflow.status()['reserved_quote'] == Decimal('10.10')
    assert 'sent' in owned.owner.sent_intents
    assert owned.store.read()['recovery_state']['halt']


def test_duplicate_client_identity_cannot_rewrite_acknowledged_history(owned):
    session, _ = setup(owned)
    buy(owned, session, 'existing', amount='1')
    with owned.engine.begin() as connection:
        connection.execute(text("UPDATE orders SET creation_pending=false, last_status='BuyOrderCompleted', "
                                "exchange_order_id='venue-known' WHERE id='existing'"))
    with pytest.raises(RuntimeError, match='identity already exists'):
        buy(owned, session, 'existing', amount='1')
    owned.owner.order_failed(owned.market, owned.market.in_flight_orders['existing'])
    with owned.engine.connect() as connection:
        row = connection.execute(text("SELECT creation_pending,last_status,exchange_order_id FROM orders WHERE id='existing'")).one()
    assert tuple(row) == (False, 'BuyOrderCompleted', 'venue-known')
