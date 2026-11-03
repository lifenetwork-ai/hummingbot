"""Recheck both markets after throttling using real ownership, recorder and PG."""
import json
import time
from contextlib import asynccontextmanager
from decimal import Decimal
from test.hummingbot.strategy_v2.controllers.test_inventory_cap_lifecycle import observe_account
from test.hummingbot.strategy_v2.controllers.test_life_mm import configuration, make_controller
from test.integration.postgres.test_ownership import assistant, intent, owned as _owned, send as send_unscoped
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text

from hummingbot.cli.service import ServiceHost
from hummingbot.core.bot_ownership import ORDER_PATH
from hummingbot.core.data_type.common import OrderType, TradeType
from hummingbot.core.data_type.in_flight_order import InFlightOrder, OrderState
from hummingbot.core.order_submission_guard import (
    MarketReadinessBlocked,
    SessionSubmissionBlocked,
    SubmissionBlocked,
    session_order_scope,
)
from hummingbot.core.web_assistant.connections.data_types import RESTMethod
from hummingbot.model.order import Order

owned = _owned


def jointly_ready(context, gross_case):
    snapshot = dict(id='joint-session', generation=0, status='running', started_at=time.time() - 1,
                    expires_at=time.time() + 600, step_expires_at=time.time() + 600)
    with context.engine.begin() as connection:
        connection.execute(text("UPDATE bot_state SET config_type='mm-session', mm_session=CAST(:state AS jsonb)"),
                           dict(state=json.dumps(snapshot)))
    controllers = []
    for name in ('okx', 'okx_perpetual'):
        controller, _ = make_controller(configuration(
            id=name, connector_name=name, runtime_config_managed=True,
            mm_session_id=snapshot['id'], mm_generation=0, max_base_inventory='1700',
            max_long_base='10000' if gross_case else '1700',
            max_short_base='10000' if gross_case else '1700',
            max_gross_base='1700' if gross_case else '10000'))
        controller._account_ready = True
        controller.admit_session(snapshot['id'], 0)
        provider = controller.market_data_provider
        connector = provider.get_connector_with_fallback.return_value
        if name == 'okx':
            context.market.trading_rules = connector.trading_rules
            provider.get_connector_with_fallback.return_value = context.market
            connector = context.market
        connector.name = name
        provider.time.side_effect = time.time
        provider.get_order_book.side_effect = None
        stamp = time.time() * (1000 if name == 'okx_perpetual' else 1)
        provider.get_order_book.return_value = SimpleNamespace(snapshot_uid=stamp, last_diff_uid=stamp)
        observe_account(connector, name, (300, 200) if name == 'okx_perpetual' else (500, 0))
        controllers.append(controller)
    host = ServiceHost.__new__(ServiceHost)
    host.owner = context.owner
    host.mm_session = SimpleNamespace(id=snapshot['id'], generation=0,
                                      catalogue=SimpleNamespace(drawdown_policy=lambda: None))
    host._mm_controllers = lambda: controllers
    context.owner.mm_ready_check = host.mm_runtime_ready
    context.owner.mm_quote_check = host.mm_quote_valid
    assert host.mm_runtime_ready(), host.mm_readiness_reason
    return host, controllers


def record_intent(context, identity):
    order = InFlightOrder(identity, 'LIFE-USDT', OrderType.LIMIT_MAKER, TradeType.BUY,
                          Decimal('1'), time.time(), Decimal('.099'))
    context.market.in_flight_orders[identity] = order
    context.owner.before_order(context.market, order)
    return order


async def send(rest, identity, *, cancel=False):
    data = dict(clOrdId=identity, instId='LIFE-USDT')
    if not cancel:
        data.update(side='buy', sz='1', px='.099', ordType='post_only', tdMode='cash')
    return await rest.execute_request(
        url='https://offline.invalid' + ('/api/v5/trade/cancel-order' if cancel else ORDER_PATH),
        method=RESTMethod.POST, throttler_limit_id='order', is_auth_required=True, data=data)


@pytest.mark.parametrize('change', [
    'spot_inventory', 'negative_spot_inventory', 'long_inventory', 'short_inventory', 'gross_inventory',
    'peer_provider_unready', 'peer_account_unconfirmed', 'peer_stale', 'peer_generation',
])
def test_queued_spot_order_cannot_cross_wire_after_either_market_loses_readiness(owned, change):
    host, controllers = jointly_ready(owned, change == 'gross_inventory')
    perpetual = controllers[1]
    peer = perpetual.market_data_provider.get_connector_with_fallback.return_value
    initial_book = perpetual.market_data_provider.get_order_book.return_value

    class ChangeDuringThrottle:
        @asynccontextmanager
        async def execute_task(self, limit_id):
            if change == 'spot_inventory':
                observe_account(owned.market, 'okx', (1701, 0))
            elif change == 'negative_spot_inventory':
                observe_account(owned.market, 'okx', (-1, 0))
                controllers[0].executors_info = [SimpleNamespace(
                    is_active=True, is_trading=False,
                    config=SimpleNamespace(side=TradeType.BUY, amount=Decimal('30'),
                                           entry_price=Decimal('.1'), leverage=1))]
            elif change == 'long_inventory':
                observe_account(peer, 'okx_perpetual', (1701, 200))
            elif change == 'short_inventory':
                observe_account(peer, 'okx_perpetual', (300, 1701))
            elif change == 'gross_inventory':
                observe_account(peer, 'okx_perpetual', (900, 900))  # Zero net, excessive gross.
            elif change == 'peer_provider_unready':
                perpetual.market_data_provider.ready = False
            elif change == 'peer_account_unconfirmed':
                perpetual._account_ready = False
            elif change == 'peer_stale':
                perpetual.market_data_provider.get_order_book.return_value = SimpleNamespace(
                    snapshot_uid=0, last_diff_uid=0)
            else:
                perpetual.config.mm_generation = 1
            yield

    rest, transport = assistant(ChangeDuringThrottle())
    with session_order_scope('joint-session', 0):
        tracked = record_intent(owned, 'blocked')
        assert 'blocked' in owned.owner.active_intents
        with pytest.raises(MarketReadinessBlocked, match='jointly ready'):
            owned.loop.run_until_complete(send(rest, 'blocked'))
    transport.call.assert_not_awaited()
    assert 'blocked' not in owned.owner.sent_intents
    if change.endswith('inventory'):
        assert host.mm_readiness_reason == 'exposure_limit'
    else:
        assert host.mm_readiness_reason is not None
    # A known local no-send can be rejected durably without turning a transient
    # peer-market restriction into HALT. No uncertainty is cleared by this test.
    tracked.current_state = OrderState.FAILED
    owned.owner.order_failed(owned.market, tracked)
    with owned.engine.connect() as connection:
        assert connection.execute(select(Order.creation_pending).where(Order.id == 'blocked')).scalar_one() is False
    assert not owned.store.read()['recovery_state'].get('halt')

    # Cancellation remains available while the peer restriction still applies.
    cancel_rest, cancel_transport = assistant()
    owned.loop.run_until_complete(send(cancel_rest, 'blocked', cancel=True))
    cancel_transport.call.assert_awaited_once()
    assert not host.mm_runtime_ready()
    assert 'blocked' not in owned.owner.sent_intents

    observe_account(owned.market, 'okx', (500, 0))
    controllers[0].executors_info = []
    observe_account(peer, 'okx_perpetual', (300, 200))
    perpetual.market_data_provider.ready = True
    perpetual.market_data_provider.get_order_book.return_value = initial_book
    perpetual._account_ready = True
    perpetual.config.mm_generation = 0
    assert host.mm_runtime_ready(), host.mm_readiness_reason
    fresh_rest, fresh_transport = assistant()
    with session_order_scope('joint-session', 0):
        record_intent(owned, 'fresh')
        owned.loop.run_until_complete(send(fresh_rest, 'fresh'))
    fresh_transport.call.assert_awaited_once()
    assert 'fresh' in owned.owner.sent_intents
    assert not owned.store.read()['recovery_state'].get('halt')


def test_recorder_failure_during_throttle_retains_unresolved_intent_and_requires_recovery(owned):
    tracked = intent(owned)

    class RecorderFailureDuringThrottle:
        @asynccontextmanager
        async def execute_task(self, limit_id):
            owned.manager.persistence_healthy = False
            yield

    rest, transport = assistant(RecorderFailureDuringThrottle())
    with pytest.raises(RuntimeError, match='Persistence failed'):
        owned.loop.run_until_complete(send_unscoped(rest))
    transport.call.assert_not_awaited()
    assert tracked.client_order_id not in owned.owner.sent_intents
    assert tracked.client_order_id in owned.owner.active_intents
    tracked.current_state = OrderState.FAILED
    owned.owner.order_failed(owned.market, tracked)
    assert owned.store.read()['recovery_state']['halt']
    assert not owned.owner.allow_new_orders
    with owned.engine.connect() as connection:
        assert connection.execute(select(Order.creation_pending).where(Order.id == tracked.client_order_id)).scalar_one()

    # Persistence failure cannot silently erase an intent. Owned cancellation
    # can still reach transport; no recovery latch is cleared to make it pass.
    cancel_rest, cancel_transport = assistant()
    owned.loop.run_until_complete(send_unscoped(cancel_rest, path='/api/v5/trade/cancel-order'))
    cancel_transport.call.assert_awaited_once()
    with pytest.raises(SubmissionBlocked):
        intent(owned, 'must-not-start')
    assert owned.store.read()['recovery_state']['halt']


def test_shutdown_during_throttle_is_latched_but_durable_local_rejection_and_cancel_remain_allowed(owned):
    tracked = intent(owned)

    class ShutdownDuringThrottle:
        @asynccontextmanager
        async def execute_task(self, limit_id):
            owned.owner.begin_shutdown()
            yield

    rest, transport = assistant(ShutdownDuringThrottle())
    with pytest.raises(SessionSubmissionBlocked, match='shutdown'):
        owned.loop.run_until_complete(send_unscoped(rest))
    transport.call.assert_not_awaited()
    assert tracked.client_order_id not in owned.owner.sent_intents
    tracked.current_state = OrderState.FAILED
    owned.owner.order_failed(owned.market, tracked)
    with owned.engine.connect() as connection:
        assert connection.execute(select(Order.creation_pending).where(Order.id == tracked.client_order_id)).scalar_one() is False
    assert not owned.store.read()['recovery_state'].get('halt')

    cancel_rest, cancel_transport = assistant()
    owned.loop.run_until_complete(send_unscoped(cancel_rest, path='/api/v5/trade/cancel-order'))
    cancel_transport.call.assert_awaited_once()
    owned.owner.allow_new_orders = True  # A stale readiness toggle cannot undo process shutdown.
    with pytest.raises(SessionSubmissionBlocked, match='shutdown'):
        intent(owned, 'must-not-reopen')
    assert owned.owner.shutdown_requested
