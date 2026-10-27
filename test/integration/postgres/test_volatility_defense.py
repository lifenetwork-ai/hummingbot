"""Real owner-fenced pause persistence, independent of profile/session identity."""
from test.hummingbot.strategy_v2.controllers.test_life_mm import configuration, make_controller
from test.hummingbot.strategy_v2.controllers.test_volatility_defense import defense_policy
from test.integration.postgres.test_ownership import owned as _owned

import pytest

owned = _owned


def controller():
    return make_controller(configuration(
        runtime_config_managed=True, mm_session_id='session', mm_generation=0,
        quote_guards=dict(enabled=True, max_deviation_bps='200', resume_deviation_bps='100'),
        spread_policy=defense_policy()))[0]


def checkpoints(context, item):
    from hummingbot.core.volatility_defense import VolatilityCheckpoints
    context.market.account_uid, context.market.trading_environment = 'account-A', 'demo'
    result = VolatilityCheckpoints(context.owner, {item.config.id: item})
    assert result.attach()
    return result


def test_pause_restores_across_profile_identity_and_clears_only_after_checkpoint(owned):
    first = controller()
    checkpoints(owned, first)
    first._spread_policy.defense.state = 'paused'
    assert first.sync_volatility_checkpoint()
    saved = owned.store.read()['recovery_state']['volatility_defense']
    assert len(saved['pauses']) == 1
    other = controller()
    other.config = other.config.model_copy(update={'id': 'different-profile-controller', 'mm_session_id': 'other-session'})
    checkpoints(owned, other)
    assert other._spread_policy.defense.state == 'paused'
    assert other._spread_policy.size_factor(1000) == 0
    other._spread_policy.defense.state = 'reduced'
    assert other.sync_volatility_checkpoint()
    assert owned.store.read()['recovery_state']['volatility_defense']['pauses'] == {}


def test_stale_control_version_cannot_clear_pause(owned):
    item = controller()
    checkpoints(owned, item)
    item._spread_policy.defense.state = 'paused'
    assert item.sync_volatility_checkpoint()
    owned.store.control(expected_version=1, desired_state='stopped')
    item._spread_policy.defense.state = 'reduced'
    assert not item.sync_volatility_checkpoint()
    assert item._volatility_saved_pause
    assert owned.store.read()['recovery_state']['volatility_defense']['pauses']


def test_failed_checkpoint_cannot_reopen_admission(owned, monkeypatch):
    item = controller()
    checkpoints(owned, item)
    item._spread_policy.defense.state = 'paused'
    assert item.sync_volatility_checkpoint()
    original = owned.owner._mutate_recovery

    def fail(change, **kwargs):
        def rollback(state):
            change(state)
            raise RuntimeError('simulated transaction rollback')
        return original(rollback, **kwargs)
    monkeypatch.setattr(owned.owner, '_mutate_recovery', fail)
    item._spread_policy.defense.state = 'reduced'
    with pytest.raises(RuntimeError):
        item.sync_volatility_checkpoint()
    assert item._volatility_saved_pause
    assert owned.store.read()['recovery_state']['volatility_defense']['pauses']


def test_clean_owner_restart_restores_pause_without_offline_recovery_credit(owned):
    from hummingbot.core.bot_ownership import BotOwnership
    from hummingbot.core.volatility_defense import VolatilityCheckpoints
    item = controller()
    checkpoints(owned, item)
    item._spread_policy.defense.state = 'paused'
    assert item.sync_volatility_checkpoint()
    owned.owner._mutate_recovery(lambda state: state.update(runtime_active=False))
    owned.owner.release()
    replacement = BotOwnership(owned.engine, owned.store.bot_id)
    try:
        assert replacement.try_acquire()
        replacement.prepare(1, 'test.yml')
        replacement.connectors = {'okx': owned.market}
        new = controller()
        assert VolatilityCheckpoints(replacement, {new.config.id: new}).attach()
        assert new._spread_policy.defense.state == 'paused'
        assert new._spread_policy.defense.good_since is None
        assert new._spread_policy.defense.good_samples == 0
    finally:
        replacement.release()


def test_control_api_cannot_remove_paused_policy(owned):
    import asyncio
    from test.hummingbot.client.config.test_mm_profiles import CATALOGUE

    from hummingbot.client.config.mm_profiles import MMProfilesConfig
    from hummingbot.core.control_errors import ControlConflict
    item = controller()
    checkpoints(owned, item)
    item._spread_policy.defense.state = 'paused'
    assert item.sync_volatility_checkpoint()
    owned.store.control(expected_version=1, desired_state='stopped')
    catalogue = asyncio.run(MMProfilesConfig.load(CATALOGUE))
    with pytest.raises(ControlConflict) as caught:
        owned.store.request_mm_session(action='start', expected_version=2, catalogue=catalogue,
                                       request=dict(market_mode='spot', profile_id='sideways', duration_seconds=60))
    assert caught.value.code == 'volatility_pause_policy_bound'
    assert owned.store.read()['control_version'] == 2


@pytest.mark.parametrize('market_mode', ['spot', 'perpetual', 'both'])
@pytest.mark.parametrize('duration_mode,selection', [('timed', 'fixed'), ('until_stopped', 'random')])
def test_stop_start_and_market_selection_keep_pause(owned, market_mode, duration_mode, selection):
    import asyncio
    from test.hummingbot.client.config.test_mm_profiles import CATALOGUE

    from hummingbot.client.config.mm_profiles import MMProfilesConfig
    item = controller()
    checkpoints(owned, item)
    item._spread_policy.defense.state = 'paused'
    assert item.sync_volatility_checkpoint()
    paused = owned.store.read()['recovery_state']['volatility_defense']
    owned.store.control(expected_version=1, desired_state='stopped')
    values = asyncio.run(MMProfilesConfig.load(CATALOGUE)).model_dump(mode='json')
    values['allow_until_stopped'] = True
    values['markets']['spot'].update(quote_guards=item.config.quote_guards.model_dump(mode='json'),
                                     spread_policy=item.config.spread_policy.model_dump(mode='json'),
                                     buy_spreads=[.01], sell_spreads=[.01], buy_amounts_pct=[1], sell_amounts_pct=[1])
    catalogue = MMProfilesConfig.model_validate(values)
    request = dict(market_mode=market_mode, duration_mode=duration_mode, selection=selection,
                   **({'profile_id': 'sideways'} if selection == 'fixed' else {}),
                   **({'duration_seconds': 600} if duration_mode == 'timed' else {}))
    started = owned.store.request_mm_session(action='start', expected_version=2, catalogue=catalogue, request=request)
    switched = owned.store.request_mm_session(action='switch', expected_version=3, current_session_id=started['session_id'],
                                              catalogue=catalogue, request=request)
    owned.store.request_mm_session(action='stop', expected_version=4, current_session_id=switched['session_id'])
    assert owned.store.read()['recovery_state']['volatility_defense'] == paused


def test_corrupt_checkpoint_is_not_treated_as_no_pause(owned):
    from hummingbot.core.order_submission_guard import SubmissionBlocked
    from hummingbot.core.volatility_defense import VolatilityCheckpoints
    item = controller()
    checkpoints(owned, item)
    owned.owner._mutate_recovery(lambda state: state.update(volatility_defense={'invalid': True}))
    with pytest.raises(SubmissionBlocked):
        VolatilityCheckpoints(owned.owner, {item.config.id: item}).attach()
    assert owned.store.read()['recovery_state']['halt']


def test_checkpoint_adapter_does_not_retain_retired_controller(owned):
    import gc
    import weakref
    item = controller()
    adapter = checkpoints(owned, item)
    observed = weakref.ref(item)
    del item
    gc.collect()
    assert observed() is None
    assert not adapter.controllers


def test_pause_rejects_changed_policy_and_account(owned):
    from hummingbot.core.order_submission_guard import SubmissionBlocked
    from hummingbot.core.volatility_defense import VolatilityCheckpoints
    item = controller()
    checkpoints(owned, item)
    item._spread_policy.defense.state = 'paused'
    assert item.sync_volatility_checkpoint()
    changed = controller()
    changed.config = changed.config.model_copy(update={'spread_policy': changed.config.spread_policy.model_copy(
        update={'volatility': changed.config.spread_policy.volatility.model_copy(update={'defense': None})})})
    with pytest.raises((SubmissionBlocked, ValueError)):
        VolatilityCheckpoints(owned.owner, {changed.config.id: changed}).attach()
    owned.market.account_uid = 'other-account'
    with pytest.raises(SubmissionBlocked):
        VolatilityCheckpoints(owned.owner, {item.config.id: item}).attach()


def test_final_transport_rejects_quote_queued_before_spread_restriction(owned, monkeypatch):
    import asyncio
    import json
    import time
    from decimal import Decimal
    from test.hummingbot.strategy_v2.controllers.test_life_mm_spread import feed
    from test.integration.postgres.test_listing_bootstrap import admit, setup

    from hummingbot.cli.service import ServiceHost
    from hummingbot.core.data_type.common import OrderType, TradeType
    from hummingbot.core.data_type.in_flight_order import InFlightOrder
    from hummingbot.core.order_submission_guard import SubmissionBlocked, session_order_scope
    from hummingbot.core.web_assistant.connections.data_types import RESTMethod, RESTRequest
    from hummingbot.strategy_v2.utils.mm_spread import SpreadPolicy, SpreadPolicyConfig
    _, session, item, bootstrap, _ = setup(owned)
    admit(owned, session, item, bootstrap)
    policy = SpreadPolicyConfig(**defense_policy(fallback_buy_spreads=[str(x) for x in item.config.buy_spreads],
                                                 fallback_sell_spreads=[str(x) for x in item.config.sell_spreads]))
    item.config = item.config.model_copy(update={'spread_policy': policy})
    item._spread_policy = SpreadPolicy(policy)
    checkpoints(owned, item)
    item.update_reference()
    assert item.processed_data['ready'], item.processed_data
    host = ServiceHost.__new__(ServiceHost)
    host._mm_controllers = lambda: [item]
    owned.owner.mm_quote_check = host.mm_quote_valid
    price, amount = item.get_price_and_amount('buy_0')
    order = InFlightOrder('queued', 'LIFE-USDT', OrderType.LIMIT_MAKER, TradeType.BUY, amount, time.time(), price)
    owned.market.in_flight_orders['queued'] = order
    with session_order_scope(session.id, session.generation, level_id='buy_0'):
        owned.owner.before_order(owned.market, order)
        item.market_data_provider._generate_candle_feed_key.return_value = 'feed'
        item.market_data_provider.candles_feeds = {'feed': feed(now=int(time.time()))}
        monkeypatch.setattr('hummingbot.strategy_v2.utils.mm_spread.natr_value', lambda *args: Decimal('.02'))
        item.update_reference(sample_guard=True)
        assert item.processed_data['ready'], item.processed_data
        request = RESTRequest(method=RESTMethod.POST, url='https://www.okx.com/api/v5/trade/order', is_auth_required=True,
                              data=json.dumps(dict(clOrdId='queued', instId='LIFE-USDT', side='buy', ordType='post_only',
                                                   sz=str(amount), px=str(price), tdMode='cash')))
        with pytest.raises(SubmissionBlocked, match='spread_policy_quote_rejected'):
            asyncio.run(owned.owner.authorize_rest(request))
    assert 'queued' not in owned.owner.sent_intents
