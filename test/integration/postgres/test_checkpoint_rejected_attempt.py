"""A durably rejected create is resolved work, not an uncertain venue send."""
from test.integration.postgres.test_checkpoint_recovery import recovering as _recovering, seed_checkpoint

import pytest
from sqlalchemy import update
from sqlalchemy.orm import Session

from hummingbot.model.order import Order
from hummingbot.model.runtime_journal import SubmissionAttempt

recovering = _recovering


@pytest.mark.parametrize('mutation', [
    None, 'uncertain', 'possibly_sent', 'prepared', 'acknowledged', 'unknown',
    'pending', 'exchange_id', 'not_failed', 'filled', 'missing_order',
])
def test_checkpoint_accepts_only_consistent_rejected_attempt(recovering, monkeypatch, mutation):
    monkeypatch.setenv('OKX_TRADING_ENV', 'demo')
    monkeypatch.setenv('OKX_SITE', 'www')
    store, owner = recovering
    seed_checkpoint(store, orders=5001 if mutation is None else 12, fills=3)
    identity = 'o00002' if mutation == 'filled' else 'missing' if mutation == 'missing_order' else 'o00010'
    state = mutation if mutation in ('uncertain', 'possibly_sent', 'prepared', 'acknowledged', 'unknown') else 'rejected'
    with Session(store.engine) as session, session.begin():
        session.add(SubmissionAttempt(order_id=identity, state=state, context={'offline': True}))
        values = {'exchange_order_id': None}
        if mutation == 'pending':
            values['creation_pending'] = True
        elif mutation == 'exchange_id':
            values['exchange_order_id'] = 'venue-identity'
        elif mutation == 'not_failed':
            values['last_status'] = 'BuyOrderCreated'
        elif mutation == 'filled':
            values['last_status'] = 'OrderFailure'
        session.execute(update(Order).where(Order.id == identity).values(**values))
    snapshot = store.snapshot()
    assert (snapshot.get('mode') == 'verified_checkpoint') is (mutation is None)
    if mutation is None:
        assert not snapshot['truncated'] and snapshot['orders'] == snapshot['fills'] == []
    assert owner.check_owner()['recovery_state']['halt']
