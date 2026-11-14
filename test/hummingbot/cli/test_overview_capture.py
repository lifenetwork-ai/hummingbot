"""Owner-loop integration stays optional and never creates trading authority."""
from copy import deepcopy
from test.hummingbot.core.test_overview_market_observations import setup_host
from types import SimpleNamespace

from hummingbot.cli.overview_capture import capture_overview
from hummingbot.core.mm_liquidity import LiquidityObserver, liquidity_view


def host():
    runtime, provider, controllers = setup_host(include_perp=False)
    runtime.mm_session.status = 'running'
    runtime.owner = SimpleNamespace(generation=1)
    runtime.liquidity_observer = LiquidityObserver()
    quotes = dict(valid=True, eligible=True, connector='okx', pair='LIFE-USDT',
                  bid_ids=['bid'], ask_ids=['ask'], two_sided=True, working_orders=[])
    for at in (98, 100):
        runtime.mm_liquidity_state = runtime.liquidity_observer.observe(
            dict(session_id='session-a', generation=4, profile_id='sideways', started_at=90, status='running'),
            {'spot': quotes}, now=at, monotonic_now=at, owner_generation=1)
    provider.connectors['okx'].event_logs = []
    controllers[0].executors_info = []
    return runtime, provider, controllers


def test_capture_combines_market_coverage_and_recovery_without_control_calls():
    runtime, provider, _ = host()
    before = deepcopy(provider.connectors['okx'].in_flight_orders)
    result = capture_overview(runtime, now=100, monotonic_now=100)
    market = result['markets'][0]
    assert market['coverage']['value']['observed_seconds'] == 2
    assert market['coverage']['value']['unknown_seconds'] == 8
    assert market['coverage']['value']['target_fraction'] is None
    assert market['post_fill_recovery']['value']['history_complete'] is False
    assert market['post_fill_recovery']['value']['p50_seconds'] is None
    assert result['valuation_prices']['value'][0]['price_usdt'] == '100.00'
    assert provider.created == 0
    assert provider.connectors['okx'].in_flight_orders == before
    assert '_overview_recovery' in runtime.mm_liquidity_state
    public = liquidity_view(dict(database_now=100, owner_uuid='owner', heartbeat_age_s=0,
                                 mm_session=dict(id='session-a', generation=4),
                                 status={'mm_liquidity': runtime.mm_liquidity_state}))
    assert '_overview_recovery' not in public
    assert '_coverage_history' not in public


def test_missing_private_observation_masks_depth_and_recovery_but_keeps_policy():
    runtime, provider, _ = host()
    runtime.mm_liquidity_state['markets']['spot']['valid'] = False
    result = capture_overview(runtime, now=100, monotonic_now=100)
    assert result['markets'][0]['depth']['value'] is None
    assert result['markets'][0]['post_fill_recovery']['value'] is None
    assert result['inventory_limits']['value'][0]['inventory_limit'] == '600'
    assert provider.created == 0


def test_capture_no_session_does_not_read_or_create_connectors():
    runtime = SimpleNamespace(mm_session=None)
    result = capture_overview(runtime, now=100, monotonic_now=100)
    assert result['session_id'] is None and result['markets'] == []


def test_owner_projection_failure_is_isolated_and_recovers(monkeypatch):
    from unittest.mock import MagicMock

    from hummingbot.cli.service import ServiceHost
    runtime = SimpleNamespace(mm_overview_state={'old': True}, overview_recovery_observer=object(), availability=MagicMock())

    def fail(_):
        raise ValueError('unavailable')

    monkeypatch.setattr('hummingbot.cli.overview_capture.capture_overview', fail)
    ServiceHost.capture_overview_observation(runtime)
    assert runtime.mm_overview_state is None and runtime.overview_recovery_observer is None
    runtime.availability.fail.assert_called_once()
    monkeypatch.setattr('hummingbot.cli.overview_capture.capture_overview', lambda _: {'session_id': 'new'})
    ServiceHost.capture_overview_observation(runtime)
    assert runtime.mm_overview_state == {'session_id': 'new'}
    runtime.availability.clear.assert_called_once_with('overview')


def test_account_bindings_are_current_and_unknown_identity_never_reuses_prior_scope():
    from hummingbot.cli.admin_observations import capture_reporting
    core = SimpleNamespace(strategy=None, connector_manager=SimpleNamespace(connectors={
        'okx': SimpleNamespace(account_uid='new', trading_environment='demo', account_balance_observations={})}))
    old = {'active_account_bindings': {'okx': {'account_id': 'old', 'environment': 'live'}}}
    captured = capture_reporting(core, old)
    assert captured['active_account_bindings'] == {'okx': {'account_id': 'new', 'environment': 'demo'}}
    core.connector_manager.connectors['okx'].account_uid = None
    assert capture_reporting(core, captured)['active_account_bindings']['okx']['account_id'] is None
    assert old['active_account_bindings']['okx']['account_id'] == 'old'
