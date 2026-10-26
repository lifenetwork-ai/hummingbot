"""Listing admission uses the real owner/session transaction and recorder gates."""
import asyncio
import json
import random
import time
from decimal import Decimal
from test.hummingbot.client.config.test_bootstrap_profiles import catalogue as bootstrap_catalogue
from test.hummingbot.strategy_v2.controllers.test_life_mm import make_controller
from test.integration.postgres.test_ownership import owned as _owned
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import text

from controllers.market_making.life_mm import LifeMMConfig
from hummingbot.client.config.mm_profiles import MMProfilesConfig
from hummingbot.core.data_type.common import OrderType, TradeType
from hummingbot.core.data_type.in_flight_order import InFlightOrder
from hummingbot.core.data_type.order_book import OrderBook
from hummingbot.core.listing_bootstrap import ListingBootstrapGuard
from hummingbot.core.mm_session import MMSession, MMSessionRequest
from hummingbot.core.order_submission_guard import SubmissionBlocked, session_order_scope
from hummingbot.core.spot_outflow_guard import SpotOutflowGuard
from hummingbot.core.web_assistant.connections.data_types import RESTMethod, RESTRequest

owned = _owned


def setup(context):
    catalogue = MMProfilesConfig.model_validate(bootstrap_catalogue())
    session = MMSession.create(catalogue, MMSessionRequest(market_mode='spot', profile_id='sideways', duration_seconds=600), rng=random.Random(1))
    with context.engine.begin() as connection:
        connection.execute(text("UPDATE bot_state SET config_type='mm-session', config_file_path='life_mm_profiles.yaml', mm_session=CAST(:value AS jsonb)"),
                           {'value': json.dumps(session.model_dump(mode='json'))})
    context.owner.config_file_path = context.recorder._config_file_path = 'life_mm_profiles.yaml'
    context.market.account_uid, context.market.trading_environment = 'account-A', 'demo'
    from hummingbot.core.data_type.trade_fee import AddedToCostTradeFee
    context.market.get_fee.return_value = AddedToCostTradeFee(percent=Decimal('.001'))
    context.owner.spot_outflow = SpotOutflowGuard(context.owner, catalogue.outflow_policy())
    asyncio.run(context.owner.spot_outflow.initialize())
    config = catalogue.controller_configs('sideways', 'spot', None)[0]
    config = LifeMMConfig.model_validate({**config.model_dump(), 'mm_session_id': session.id, 'mm_generation': 0})
    controller, _ = make_controller(config)
    provider = controller.market_data_provider
    provider.time.side_effect = time.time
    book = OrderBook()
    stamp = time.time()
    book.apply_snapshot([], [], stamp)
    provider.get_order_book.side_effect = None
    provider.get_order_book.return_value = book
    provider.get_connector_with_fallback.return_value = context.market
    context.market._order_tracker = SimpleNamespace(cached_orders={}, lost_orders={})
    context.market.trading_rules = {'LIFE-USDT': SimpleNamespace(min_order_size=Decimal('.001'), min_base_amount_increment=Decimal('.001'))}
    context.market.quantize_order_price.side_effect = lambda pair, price: price
    guard = ListingBootstrapGuard(context.owner, catalogue.bootstrap_policies(), {config.id: controller})
    context.owner.listing_bootstrap = guard
    assert guard.bind_if_ready()
    controller.update_reference()
    assert controller.processed_data['quote_guard']['can_admit']
    context.owner.mm_ready_check = lambda: controller.processed_data.get('ready')
    return catalogue, session, controller, guard, book


def admit(context, session, controller, guard, risk_change=None):
    result = context.store.update_mm_session(
        owner_uuid=context.owner.owner_uuid, owner_generation=context.owner.generation, version=1,
        session_id=session.id, generation=session.generation,
        change=lambda current, now: current.ready(now=now, anchors={controller.config.id: controller.reference_state()}),
        risk_change=risk_change or guard.start_admission)
    guard.refresh()
    controller.update_reference()
    return result


def proposal(context, session, identity='a', amount='100', price='.099'):
    order = InFlightOrder(identity, 'LIFE-USDT', OrderType.LIMIT_MAKER, TradeType.BUY,
                          Decimal(amount), time.time(), Decimal(price))
    context.market.in_flight_orders[identity] = order
    with session_order_scope(session.id, session.generation):
        context.owner.before_order(context.market, order)
    return order


def request(identity='a', price='.099', size='100'):
    return RESTRequest(method=RESTMethod.POST, url='https://www.okx.com/api/v5/trade/order', is_auth_required=True,
                       data=json.dumps(dict(clOrdId=identity, instId='LIFE-USDT', side='buy', sz=size, px=price,
                                            ordType='post_only', tdMode='cash')))


def test_atomic_start_rollback_and_restart_cannot_renew_original_episode(owned):
    _, session, controller, guard, _ = setup(owned)
    before = owned.store.read()['recovery_state']['listing_bootstrap']

    def rollback(state, current, now):
        guard.start_admission(state, current, now)
        raise ValueError('rollback')
    with pytest.raises(ValueError, match='rollback'):
        admit(owned, session, controller, guard, rollback)
    assert owned.store.read()['recovery_state']['listing_bootstrap'] == before
    assert owned.store.read()['mm_session']['status'] == 'preparing'
    session = admit(owned, session, controller, guard)
    frame = owned.store.read()['recovery_state']['listing_bootstrap']
    episode = frame['episodes'][controller.config.id]
    assert episode['expires_at'] <= session.step_expires_at
    assert episode['expires_at'] - episode['started_at'] == 30
    replacement = ListingBootstrapGuard(owned.owner, guard.policies, guard.controllers)
    assert replacement.bind_if_ready()
    # A new startup admission never starts an already armed instrument again.
    owned.owner._mutate_recovery(lambda state: replacement.start_admission(state, session, time.time() + 20))
    assert owned.store.read()['recovery_state']['listing_bootstrap'] == frame


def test_pre_intent_partial_allocation_final_payload_and_expiration_deadline(owned):
    _, session, controller, guard, _ = setup(owned)
    session = admit(owned, session, controller, guard)
    order = proposal(owned, session, amount='400')
    order.executed_amount_base = Decimal('390')
    with pytest.raises(SubmissionBlocked, match='bootstrap_quote_budget'):
        proposal(owned, session, 'b', amount='200')
    owned.market.in_flight_orders.pop('b')
    with pytest.raises(SubmissionBlocked, match='payload'):
        asyncio.run(owned.owner.authorize_rest(request(size='399')))
    assert not owned.owner.sent_intents
    req = request(size='400')
    asyncio.run(owned.owner.authorize_rest(req))
    expiry = guard.frame['episodes'][controller.config.id]['expires_at']
    assert int(req.headers['expTime']) <= int(expiry * 1000)
    assert not owned.store.read()['recovery_state'].get('halt')


def test_qualification_requires_commit_and_cannot_reenable_bootstrap(owned):
    _, session, controller, guard, book = setup(owned)
    session = admit(owned, session, controller, guard)
    from hummingbot.core.data_type.order_book_row import OrderBookRow
    stamp = time.time()
    with patch('controllers.market_making.life_mm.time.monotonic', return_value=0):
        book.apply_snapshot([OrderBookRow(.099, 2000, stamp)], [OrderBookRow(.101, 2000, stamp)], stamp)
        controller.update_reference(sample_guard=True)
    with patch('controllers.market_making.life_mm.time.monotonic', return_value=3):
        book.apply_snapshot([OrderBookRow(.099, 2001, stamp + .001)], [OrderBookRow(.101, 2001, stamp + .001)], stamp + .001)
        controller.update_reference(sample_guard=True)
        assert controller.processed_data['quote_guard']['qualification_pending']
        assert not controller.processed_data['ready']
        assert guard.sync_qualification()
        controller.update_reference()
        assert controller.processed_data['ready']
        assert guard.frame['episodes'][controller.config.id]['market_qualified']
        book.apply_snapshot([], [], stamp + .002)
        controller.update_reference()
        assert not controller.processed_data['ready']
        assert controller.processed_data['reason'] == 'market_liquidity_unqualified'
        assert controller.processed_data['quote_guard']['bootstrap']['state'] == 'waiting'


def test_immutable_policy_and_control_version_halt_priority(owned):
    catalogue, session, controller, guard, _ = setup(owned)
    session = admit(owned, session, controller, guard)
    data = catalogue.model_dump(mode='json')
    data['markets']['spot']['initial_anchor'] = '.101'
    with pytest.raises(Exception, match='bootstrap'):
        owned.store.request_mm_session(action='switch', expected_version=1, current_session_id=session.id,
                                       catalogue=MMProfilesConfig.model_validate(data),
                                       request=MMSessionRequest(market_mode='spot', profile_id='sideways', duration_seconds=60))
    owned.owner.require_recovery('test halt')
    with pytest.raises(SubmissionBlocked):
        proposal(owned, session)
    assert owned.store.read()['recovery_state']['halt']


def test_original_bootstrap_deadline_survives_qualification_while_throttled(owned):
    _, session, controller, guard, _ = setup(owned)
    session = admit(owned, session, controller, guard)
    proposal(owned, session)
    deadline = guard.deadlines['a'][1]
    row = owned.owner.check_owner()
    # Qualification may change authorization, but cannot rewrite a queued order's clock.
    row['database_now'] = deadline
    with patch.object(guard, '_market', return_value=(controller.config.id,
                      dict(market_qualified=True, expires_at=deadline), controller)):
        with pytest.raises(SubmissionBlocked, match='bootstrap_expired'):
            guard.authorize(json.loads(request().data), row)
    assert 'a' not in owned.owner.sent_intents


def test_stop_control_version_cannot_race_a_first_bootstrap_admission(owned):
    _, session, controller, guard, _ = setup(owned)
    before = owned.store.read()['recovery_state']['listing_bootstrap']
    owned.store.request_mm_session(action='stop', expected_version=1, current_session_id=session.id,
                                   catalogue=session.catalogue, request=None)
    with pytest.raises(Exception, match='version'):
        admit(owned, session, controller, guard)
    assert owned.store.read()['recovery_state']['listing_bootstrap'] == before


def test_bootstrap_initialization_account_mismatch_retains_halt(owned):
    _, _, _, guard, _ = setup(owned)
    owned.market.account_uid = 'account-B'
    replacement = ListingBootstrapGuard(owned.owner, guard.policies, guard.controllers)
    with pytest.raises(SubmissionBlocked, match='binding'):
        replacement.bind_if_ready()
    assert owned.store.read()['recovery_state']['halt']


def test_final_send_bootstrap_deadline_is_tighter_than_session_expiry(owned):
    _, session, controller, guard, _ = setup(owned)
    session = admit(owned, session, controller, guard)
    proposal(owned, session)
    deadline = guard.deadlines['a'][1]
    req = request()
    from datetime import datetime, timezone
    req.headers = {'OK-ACCESS-TIMESTAMP': datetime.fromtimestamp(deadline - .25, timezone.utc).isoformat()}
    asyncio.run(owned.owner.authorize_rest(req))
    assert int(req.headers['expTime']) == int(deadline * 1000)


def test_random_rotation_and_inactive_market_cannot_rearm_episode(owned):
    catalogue, session, controller, guard, _ = setup(owned)
    session = admit(owned, session, controller, guard)
    before = owned.store.read()['recovery_state']['listing_bootstrap']
    random_session = MMSession.create(catalogue, MMSessionRequest(
        market_mode='spot', selection='random', profile_ids=['sideways'], duration_seconds=600, step_duration_seconds=60),
        rng=random.Random(2))
    random_session.ready(now=session.started_at, anchors=session.anchors)
    assert random_session.advance(now=session.started_at + 61, rng=random.Random(3))
    random_session.ready(now=session.started_at + 61, anchors=session.anchors)
    owned.owner._mutate_recovery(lambda state: guard.start_admission(state, random_session, session.started_at + 61))
    assert owned.store.read()['recovery_state']['listing_bootstrap'] == before
    inactive = ListingBootstrapGuard(owned.owner, guard.policies, {})
    assert inactive.bind_if_ready() and not inactive.needs_start()
    assert inactive.frame == before


def test_expiry_inside_real_rest_throttle_rejects_unsent_and_allows_cancel(owned):
    from contextlib import asynccontextmanager
    from test.integration.postgres.test_ownership import assistant
    _, session, controller, guard, _ = setup(owned)
    session = admit(owned, session, controller, guard)
    order = proposal(owned, session)
    deadline = guard.deadlines['a'][1]
    check_owner = owned.owner.check_owner
    late = [False]

    def database_clock(**kwargs):
        row = check_owner(**kwargs)
        if late[0]:
            row['database_now'] = deadline
        return row

    class ExpiredThrottle:
        @asynccontextmanager
        async def execute_task(self, limit_id):
            late[0] = True
            yield

    rest, transport = assistant(ExpiredThrottle())
    with patch.object(owned.owner, 'check_owner', database_clock):
        with pytest.raises(SubmissionBlocked, match='bootstrap_expired'):
            owned.loop.run_until_complete(rest.execute_request(
                url=request().url, method=RESTMethod.POST, throttler_limit_id='order',
                is_auth_required=True, data=json.loads(request().data)))
        transport.call.assert_not_awaited()
        owned.owner.order_failed(owned.market, order)
        owned.loop.run_until_complete(rest.execute_request(
            url='https://www.okx.com/api/v5/trade/cancel-order', method=RESTMethod.POST,
            throttler_limit_id='cancel', is_auth_required=True, data={'clOrdId': 'a', 'instId': 'LIFE-USDT'}))
        assert transport.call.await_count == 1
    assert 'a' not in owned.owner.sent_intents and not owned.store.read()['recovery_state'].get('halt')
