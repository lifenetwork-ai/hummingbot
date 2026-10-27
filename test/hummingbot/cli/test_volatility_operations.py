"""Plain operator status remains observational and becomes unknown when stale."""
from copy import deepcopy

from hummingbot.cli.admin_operations import operator_view


def sample_row():
    session = dict(id='session', generation=0, status='running')
    return dict(owner_uuid='owner', heartbeat_age_s=0, database_now=1200, desired_state='running', mm_session=session,
                status=dict(phase='waiting_for_mm_readiness', mm_session=deepcopy(session), availability=dict(observed_at=1200),
                            mm_markets=[dict(market='spot', ready=False, admitted=False, reference_price=None,
                                             spread_policy=dict(state='dynamic', multiplier='3', raw_natr='.05', buy_spreads=[], sell_spreads=[],
                                                                defense=dict(state='paused', label='Waiting for volatility to settle',
                                                                             next_action='Wait for fresh market data to pass recovery checks.',
                                                                             automatic_recovery_expected=True, effective_allocation_quote='0',
                                                                             cancellation_pending=True, paused_latch_persisted=True)))]))


def test_operator_explains_volatility_pause_in_plain_english():
    result = operator_view(sample_row(), {'trading_environment': 'demo'})
    assert result['operator']['label'] == 'Waiting for volatility to settle'
    assert result['operator']['next_action'] == 'wait'


def test_stale_snapshot_cannot_claim_healthy_volatility_or_pending_cancellation():
    row = sample_row()
    row['heartbeat_age_s'] = 10
    result = operator_view(row, {'trading_environment': 'demo'})
    defense = result['markets'][0]['spread_policy']['defense']
    assert defense['state'] == 'unavailable'
    assert not defense['automatic_recovery_expected']
    assert defense['cancellation_pending'] is None
    assert result['markets'][0]['spread_policy']['raw_natr'] is None
    assert row['status']['mm_markets'][0]['spread_policy']['defense']['state'] == 'paused'


def test_global_halt_takes_precedence_over_volatility_auto_recovery():
    row = sample_row()
    row['recovery_summary'] = dict(halted=True, pending_mutations=False)
    result = operator_view(row, {'trading_environment': 'demo'})
    assert result['operator']['next_action'] == 'contact_support'
    assert not result['markets'][0]['spread_policy']['defense']['automatic_recovery_expected']
