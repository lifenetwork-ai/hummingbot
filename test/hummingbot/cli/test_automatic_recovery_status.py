import time
from test.hummingbot.cli.test_admin_api import client

import pytest

from hummingbot.cli.admin_api import ADMIN_PREFIX
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
