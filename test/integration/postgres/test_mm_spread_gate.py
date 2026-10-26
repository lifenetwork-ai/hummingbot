"""Actual fee/quote checks run before intent and again after REST throttling."""
import asyncio
from test.integration.postgres.test_listing_bootstrap import admit, proposal, request, setup
from test.integration.postgres.test_ownership import owned as _owned
from unittest.mock import MagicMock

import pytest

from hummingbot.core.order_submission_guard import SubmissionBlocked

owned = _owned


def test_actual_quote_callback_runs_at_intent_and_final_transport(owned):
    _, session, controller, guard, _ = setup(owned)
    admit(owned, session, controller, guard)
    check = MagicMock()
    owned.owner.mm_quote_check = check
    order = proposal(owned, session)
    assert check.call_count == 1
    assert check.call_args.args == (owned.market, order)
    check.side_effect = SubmissionBlocked('fee floor changed while queued')
    with pytest.raises(SubmissionBlocked, match='fee floor changed'):
        asyncio.run(owned.owner.authorize_rest(request()))
    assert order.client_order_id not in owned.owner.sent_intents


def test_final_payload_cannot_bypass_policy_after_leaving_bootstrap(owned):
    _, session, controller, guard, _ = setup(owned)
    session = admit(owned, session, controller, guard)
    owned.owner.listing_bootstrap = None
    owned.owner.spot_outflow = None
    check = MagicMock()
    owned.owner.mm_quote_check = check
    proposal(owned, session)
    with pytest.raises(SubmissionBlocked, match='payload changed'):
        asyncio.run(owned.owner.authorize_rest(request(price='.1')))
    assert check.call_count == 1
    asyncio.run(owned.owner.authorize_rest(request()))
    assert check.call_count == 2
    assert owned.owner.sent_intents == {'a'}
