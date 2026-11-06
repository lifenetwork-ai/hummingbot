"""Operator controls must explain a fresh invalid durable risk checkpoint."""
import pytest

from hummingbot.cli.admin_operations import operator_view


@pytest.mark.parametrize('kind', ['spot_outflow', 'drawdown'])
def test_fresh_checkpoint_incident_requires_review_without_claiming_a_durable_halt(kind):
    row = dict(database_now=1000, heartbeat_age_s=0, owner_uuid='owner', desired_state='stopped',
               mm_session={'id': 'session', 'generation': 1, 'status': 'stopped'}, recovery_summary={},
               status={'running': False, 'phase': 'stopped', 'mm_session': {'id': 'session', 'generation': 1},
                       'availability': {'observed_at': 1000, 'incidents': [dict(
                           scope=kind + '_checkpoint', reason_code=kind + '_checkpoint_invalid',
                           operator_action_required=True)]}})
    result = operator_view(row, {'trading_environment': 'demo'})
    assert result['operator']['state'] == 'needs_attention'
    assert result['operator']['next_action'] == 'contact_support'
    assert not result['actions']['start']['allowed']
    assert result['actions']['start']['reason_code'] == kind + '_checkpoint_invalid'
    assert result['actions']['stop']['allowed']
    assert not row['recovery_summary']


@pytest.mark.parametrize('age,scope', [(10, 'spot_outflow_checkpoint'), (0, 'spot_outflow_reporting')])
def test_stale_or_render_only_incident_does_not_become_a_new_risk_authority(age, scope):
    row = dict(database_now=1000, heartbeat_age_s=age, owner_uuid='owner', desired_state='stopped',
               status={'running': False, 'phase': 'stopped', 'availability': {'observed_at': 1000, 'incidents': [dict(
                   scope=scope, reason_code=scope + '_invalid', operator_action_required=True)]}}, recovery_summary={})
    result = operator_view(row, {'trading_environment': 'demo'})
    assert result['operator']['state'] == ('unavailable' if age else 'stopped')
    assert result['actions']['start']['reason_code'] == ('owner_unavailable' if age else None)
