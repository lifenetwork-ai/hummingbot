"""Initial-exposure bootstrap must yield to durable post-fill PostgreSQL holds."""
from decimal import Decimal
from test.hummingbot.strategy.test_mm_initial_position_bootstrap import held, restore, setup
from test.integration.postgres.test_ownership import owned as _owned

import pytest

from hummingbot.connector.markets_recorder import MarketsRecorder
from hummingbot.core.bot_ownership import BotOwnership
from hummingbot.core.data_type.common import PositionSide
from hummingbot.core.order_submission_guard import SubmissionBlocked

owned = _owned


@pytest.mark.parametrize('broker_matches', [True, False])
def test_filled_restart_and_failed_cleanup_preserve_postgres_holds(owned, monkeypatch, broker_matches):
    strategy, connector, _, _ = setup(monkeypatch, [])
    monkeypatch.setattr(MarketsRecorder, 'get_instance', lambda: owned.recorder)
    for row in (held('BUY', '.0084', '.0047181294'), held('SELL', '.0027', '.00786827')):
        owned.recorder.update_or_store_position(row)
    if not broker_matches:
        connector.account_positions[PositionSide.LONG].amount = Decimal('.0099')
    first = restore(strategy)
    if broker_matches:
        BotOwnership._verify_positions(type('Owner', (), {'strategy': strategy})(), connector)
    else:
        with pytest.raises(SubmissionBlocked, match='positions do not match'):
            BotOwnership._verify_positions(type('Owner', (), {'strategy': strategy})(), connector)
    owned.loop.run_until_complete(first.stop(keep_position=True))
    # Read through the real recorder and rebuild a second orchestrator: neither
    # successful nor failed reconciliation may replace quantity/cost/fees by YAML.
    second = restore(strategy)
    positions = second.positions_held['maker']
    assert {p.side.name: abs(p.net_amount_base) for p in positions} == {
        'BUY': Decimal('.0084'), 'SELL': Decimal('.0027')}
    assert sum((p.cum_fees_quote for p in positions), Decimal(0)) == Decimal('.0125863994')
    assert all(p.avg_entry_price == Decimal('78686.5') for p in positions)
    rows = owned.recorder.get_all_positions(controller_ids=['maker'])
    assert len(rows) == 2
    assert {p.side: p.amount for p in rows} == {'BUY': Decimal('.0084'), 'SELL': Decimal('.0027')}
