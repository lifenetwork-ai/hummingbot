from random import Random
from test.hummingbot.cli.test_admin_configuration import api, state
from test.hummingbot.client.config.test_mm_profiles import CATALOGUE
from test.hummingbot.core.test_mm_quote_activity import observation

import pytest

from hummingbot.cli.admin_api import ADMIN_PREFIX
from hummingbot.client.config.mm_profiles import MMProfilesConfig
from hummingbot.core.mm_liquidity import LiquidityObserver
from hummingbot.core.mm_session import MMSession, MMSessionRequest


async def quote_state():
    catalogue = await MMProfilesConfig.load(CATALOGUE)
    session = MMSession.create(catalogue, MMSessionRequest(market_mode='spot', profile_id='sideways', duration_seconds=600), rng=Random(1))
    session.status, session.started_at, session.expires_at = 'running', 1000, 1600
    raw = session.model_dump(mode='json', exclude={'catalogue'})
    row = state()
    row.update(database_now=1007, desired_state='running', mm_session=raw)
    row['status'].update(phase='running', running=True, identity=None, mm_session=raw)
    observer = LiquidityObserver()
    for at in (1000, 1003, 1006):
        row['status']['mm_liquidity'] = observer.observe(
            dict(session_id=session.id, generation=session.generation, profile_id='sideways', started_at=1000, status='running'),
            {'spot': observation(empty=True, at=at, reasons={'BUY': ['insufficient_quote_balance'], 'SELL': ['insufficient_base_balance']})},
            now=at, monotonic_now=at, owner_generation=1, warning_seconds=5)
    return catalogue, row


@pytest.mark.asyncio
async def test_status_and_liquidity_publish_same_quote_warning_and_safe_english_reasons():
    catalogue, row = await quote_state()
    async with api(catalogue, row) as http:
        status = await (await http.get(ADMIN_PREFIX + '/status')).json()
        response = await http.get(ADMIN_PREFIX + '/statistics/liquidity')
        assert response.status == 200
        liquidity = (await response.json())['markets']['spot']['quote_activity']
        assert status['quote_activity']['spot'] == liquidity
        assert liquidity['warning_active'] and liquidity['no_quote_seconds'] == 6
        assert liquidity['sides']['BUY']['reason_codes'] == ['insufficient_quote_balance']
        assert liquidity['proposal_observed_at'] == '1970-01-01T00:16:46Z'
        assert status['operator']['label'] == 'No working orders observed'
        assert status['phase'] == 'running' and status['running']
        assert status['actions']['stop']['allowed']


@pytest.mark.asyncio
async def test_stale_owner_and_stop_never_keep_a_current_no_quote_alarm():
    catalogue, row = await quote_state()
    row['heartbeat_age_s'] = 20
    async with api(catalogue, row) as http:
        status = await (await http.get(ADMIN_PREFIX + '/status')).json()
        assert status['quote_activity']['spot']['state'] == 'unavailable'
        assert not status['quote_activity']['spot']['warning_active']
        assert status['operator']['state'] == 'unavailable'
        row['heartbeat_age_s'] = 1
        row['desired_state'] = 'stopped'
        row['mm_session']['status'] = 'stopping'
        status = await (await http.get(ADMIN_PREFIX + '/status')).json()
        assert not status['quote_activity']['spot']['warning_active']
        assert status['operator']['state'] == 'stopping'
