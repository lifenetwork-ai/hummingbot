import time
from test.hummingbot.cli.test_admin_api import client
from unittest.mock import MagicMock, patch

import pytest

from hummingbot.cli.admin_api import ADMIN_PREFIX, AdminAPI
from hummingbot.cli.admin_operations import operator_view


def state(available=True):
    now = time.time()
    automatic = dict(phase='reconciling', reason_code='order_not_found_inconclusive',
                     operator_required=False, started_at=time.time(), next_retry_at=time.time() + 2, order_id='uncertain')
    return dict(bot_id='hummingbot', desired_state='running', control_version=1, acknowledged_version=1,
                heartbeat_age_s=0 if available else 10, owner_uuid='owner', database_now=now, mm_session=None,
                recovery_summary={'halted': False, 'pending_mutations': False, 'automatic_recovery': automatic},
                status={'running': True, 'phase': 'reconciling_orders', 'automatic_recovery': {'phase': 'idle'},
                        'availability': {'observed_at': now - .1, 'state_source': 'postgres',
                                         'persisted_state_available': True, 'phase': 'reconciling_orders'}})


@pytest.mark.asyncio
@pytest.mark.parametrize('available', [True, False])
async def test_api_distinguishes_technical_pause_from_manual_halt_and_stale_observation(available):
    row = state(available)
    async with client(state=row) as (http, _):
        response = await http.get(ADMIN_PREFIX + '/status')
        body = await response.json()
    assert response.status == 200
    assert body['automatic_recovery']['phase'] == 'reconciling'  # Fresh DB state beats old published idle.
    assert body['automatic_recovery']['stale'] is not available
    assert body['automatic_recovery']['reason_code'] == 'order_not_found_inconclusive'
    assert not body['recovery']['halted'] and body['recovery']['next_action'] == 'wait'
    assert not body['actions']['switch']['allowed']


def test_stop_remains_available_during_automatic_reconciliation():
    row = state()
    row['mm_session'] = row['status']['mm_session'] = {'id': 'session', 'generation': 0, 'status': 'running'}
    result = operator_view(row, {'trading_environment': 'demo'})
    assert result['operator']['label'] == 'Reconciling exchange orders'
    assert result['actions']['stop']['allowed']
    assert not result['actions']['start']['allowed'] and not result['actions']['switch']['allowed']


@pytest.mark.asyncio
@pytest.mark.parametrize('desired', ['running', 'stopped'])
@pytest.mark.parametrize('available', [True, False])
async def test_verified_shutdown_fence_is_wait_not_manual_recovery(desired, available):
    row = state(available)
    row['desired_state'] = desired
    row['status']['phase'] = 'running'  # Last publication can precede the durable shutdown fence.
    row['mm_session'] = row['status']['mm_session'] = dict(
        id='session', generation=0, status='running', config_hash='a' * 64, profile_id='follow_market',
        request=dict(market_mode='both', selection='fixed', profile_id='follow_market', duration_mode='until_stopped'),
        step_index=0, started_at=row['database_now'], expires_at=None, step_expires_at=None, anchors={})
    row['recovery_summary'].update(halted=True, shutdown_pending=True,
                                   automatic_recovery={'phase': 'idle', 'cold_recovery_supported': True})
    async with client(state=row) as (http, _):
        response = await http.get(ADMIN_PREFIX + '/status')
        body = await response.json()
    assert response.status == 200
    assert body['recovery']['halted']  # Preserve the real admission fence.
    assert body['recovery']['next_action'] == 'wait'
    assert not body['recovery']['check_available'] and not body['recovery']['reset_available']
    assert body['operator']['state'] == ('stopping' if available else 'unavailable')
    assert body['operator']['next_action'] == 'wait'
    assert not body['actions']['start']['allowed'] and not body['actions']['switch']['allowed']
    assert body['actions']['stop']['allowed']


@pytest.mark.parametrize('change', ['manual', 'pending_mutation', 'unsupported_baseline', 'operator_required', 'risk_checkpoint'])
def test_shutdown_presentation_does_not_hide_a_manual_block(change):
    row = state()
    row['recovery_summary'].update(halted=True, shutdown_pending=True,
                                   automatic_recovery={'phase': 'idle', 'cold_recovery_supported': True})
    if change == 'manual':
        row['recovery_summary']['shutdown_pending'] = False
    elif change == 'pending_mutation':
        row['recovery_summary']['pending_mutations'] = True
    elif change == 'unsupported_baseline':
        row['recovery_summary']['automatic_recovery']['cold_recovery_supported'] = False
    elif change == 'operator_required':
        row['recovery_summary']['automatic_recovery']['operator_required'] = True
    else:
        row['status']['availability']['incidents'] = [dict(scope='drawdown_checkpoint', operator_action_required=True)]
    result = operator_view(row, {'trading_environment': 'demo'})
    assert result['operator']['state'] == 'needs_attention'
    assert result['recovery']['next_action'] == 'contact_support'


@pytest.mark.parametrize('phase', ['reconciling', 'bootstrapping_recovery', 'waiting_for_readiness'])
def test_manual_halt_takes_precedence_over_retained_automatic_progress(phase):
    row = state()
    row['desired_state'] = 'stopped'
    row['recovery_summary'].update(halted=True, shutdown_pending=False,
                                   automatic_recovery={'phase': phase, 'cold_recovery_supported': True})
    result = operator_view(row, {'trading_environment': 'demo'})
    assert result['operator']['state'] == 'needs_attention'
    assert result['recovery']['next_action'] == 'contact_support'


@pytest.mark.asyncio
async def test_stopped_admin_can_check_manual_halt_despite_retained_automatic_progress():
    row = state()
    row['desired_state'] = 'stopped'
    row['status']['phase'] = 'halted'
    row['status']['running'] = False
    row['recovery_summary'].update(halted=True, shutdown_pending=False)
    store = MagicMock()
    store.latest.return_value = None
    with patch.object(AdminAPI, 'recovery_store', return_value=store):
        async with client(state=row) as (http, _):
            response = await http.get(ADMIN_PREFIX + '/status')
            body = await response.json()
    assert response.status == 200
    assert body['recovery']['halted'] and body['recovery']['check_available']
    assert body['recovery']['next_action'] == 'check_recovery'
    assert not body['recovery']['reset_available'] and not body['actions']['start']['allowed']
