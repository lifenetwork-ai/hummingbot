from test.hummingbot.strategy_v2.controllers.test_life_mm import configuration, make_controller
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from hummingbot.core.mm_liquidity import capture_liquidity


@pytest.mark.parametrize('phase,local_reason,stale,expected', [
    ('waiting_for_mm_readiness', None, False, 'exposure_limit'),
    ('waiting_for_mm_readiness', 'market_price_unavailable', False, 'market_price_unavailable'),
    ('running', None, False, None),
    ('stopped', None, False, 'session_not_admitted'),
    ('waiting_for_mm_readiness', None, True, 'liquidity_observation_unavailable'),
])
def test_liquidity_explains_shared_admission_without_hiding_local_or_stale_data(phase, local_reason, stale, expected):
    controller, _ = make_controller(configuration())
    controller.processed_data = {'ready': local_reason is None, 'reason': local_reason}
    source = controller.market_data_provider.get_connector_with_fallback.return_value
    source._user_stream_tracker.last_recv_time = 0 if stale else 1000
    source.TICK_INTERVAL_LIMIT = 120
    owner = SimpleNamespace(generation=1, store=MagicMock())
    owner.store.read.return_value = {}
    session = SimpleNamespace(id='session', generation=1, profile_id='sideways',
                              started_at=1000, status='running', expires_at=1600)
    host = SimpleNamespace(mm_session=session, owner=owner, phase=phase, mm_readiness_reason='exposure_limit',
                           _mm_controllers=lambda: [controller])
    result = capture_liquidity(host)['markets']['spot']
    assert result['reason_code'] == expected
    assert result['valid'] is not stale
    assert result['eligible'] is (phase == 'running' and local_reason is None and not stale)
    source._api_request.assert_not_called()
