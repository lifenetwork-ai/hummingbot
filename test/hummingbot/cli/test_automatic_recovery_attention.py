"""A prolonged uncertain outcome needs attention without claiming a HALT."""
from test.hummingbot.cli.test_volatility_operations import sample_row

import pytest

from hummingbot.cli.admin_operations import operator_view


@pytest.mark.parametrize('phase', ['bootstrapping_recovery', 'reconciling'])
def test_prolonged_automatic_recovery_requests_review_without_reset_or_start(phase):
    row = sample_row()
    row['status']['mm_markets'] = []
    row['status']['phase'] = 'starting'
    row['recovery_summary'] = dict(automatic_recovery=dict(
        phase=phase, operator_required=True, reason_code='order_not_found_inconclusive'))
    result = operator_view(row, {'trading_environment': 'demo'})
    assert result['operator']['state'] == 'needs_attention'
    assert result['operator']['next_action'] == 'contact_support'
    assert not result['recovery']['halted']
    assert result['recovery']['next_action'] == 'contact_support'
    assert result['actions']['stop']['allowed']
    assert not result['actions']['start']['allowed']
    assert not result['actions']['switch']['allowed']


def test_ordinary_reconciliation_still_requests_wait():
    row = sample_row()
    row['status']['mm_markets'] = []
    row['status']['phase'] = 'starting'
    row['recovery_summary'] = dict(automatic_recovery=dict(phase='bootstrapping_recovery', operator_required=False))
    result = operator_view(row, {'trading_environment': 'demo'})
    assert result['operator']['next_action'] == 'wait'
    assert result['recovery']['next_action'] == 'wait'


def test_attention_does_not_deadlock_explicit_cold_start_from_stopped():
    row = sample_row()
    row['desired_state'] = 'stopped'
    row['mm_session']['status'] = 'stopped'
    row['status']['mm_session']['status'] = 'stopped'
    row['status']['phase'] = 'stopped'
    row['status']['mm_markets'] = []
    row['recovery_summary'] = dict(automatic_recovery=dict(
        phase='bootstrapping_recovery', operator_required=True, reason_code='order_not_found_inconclusive'))
    result = operator_view(row, {'trading_environment': 'demo'})
    assert result['operator']['next_action'] == 'contact_support'
    assert result['actions']['start']['allowed']  # Initializes proof, never bypasses it.
    assert not result['recovery']['halted']
