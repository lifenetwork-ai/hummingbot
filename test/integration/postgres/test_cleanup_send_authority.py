import json
from decimal import Decimal
from test.integration.postgres.test_ownership import owned as owned_fixture
from types import SimpleNamespace

import pytest
from sqlalchemy import update

from hummingbot.core.admin_identity import Principal
from hummingbot.core.bot_ownership import OwnershipLost
from hummingbot.core.cleanup_authority import CleanupOrderScope, cleanup_order_scope
from hummingbot.core.data_type.common import OrderType, PositionAction, PositionMode, TradeType
from hummingbot.core.data_type.in_flight_order import InFlightOrder
from hummingbot.core.order_submission_guard import SubmissionBlocked
from hummingbot.model.admin_access import AdminAccessStore
from hummingbot.model.bot_state import BotState
from hummingbot.model.exchange_credential import ExchangeCredential
from hummingbot.model.order import Order
from hummingbot.model.position import Position

owned = owned_fixture


def prepare(owned):
    owned.market.name = owned.market.display_name = "okx_perpetual"
    owned.market.position_mode = PositionMode.HEDGE
    owned.market._format_size_to_amount.side_effect = lambda pair, amount: amount
    owned.owner.connectors = {"okx_perpetual": owned.market}
    owned.owner.cleanup_operation_id = "disconnect"
    owned.owner.cleanup_versions = {"okx_perpetual": 1}
    owned.owner.allow_new_orders = False
    now = int(owned.store.read()["database_now"] * 1000)
    with owned.engine.begin() as connection:
        AdminAccessStore(owned.engine, owned.store.bot_id).audit(connection,
                                                                 actor=Principal("basic:operator", "basic", "admin"), action="credentials_disconnect", outcome="accepted",
                                                                 request_id="request-disconnect", operation_id="disconnect", exchange="okx", connector="okx_perpetual", environment="demo")
        connection.execute(update(BotState).values(desired_state="stopped", control_version=2,
                                                   config_type="mm-session", mm_session={"status": "expired"}, recovery_state={"credential_operation": dict(
                                                       id="disconnect", action="credentials_disconnect", version=1, connector="okx_perpetual", exchange="okx",
                                                       environment="demo", affected_versions={"okx_perpetual": 1}, stage="closing_positions", approved_generation=owned.owner.generation)}))
        connection.execute(Position.__table__.insert().values(id="held-long", controller_id="controller", connector_name="okx_perpetual",
                                                              side="BUY", trading_pair="BTC-USDT", timestamp=1, volume_traded_quote=10, amount=Decimal("0.1"), breakeven_price=100,
                                                              realized_pnl_quote=0, unrealized_pnl_quote=0, cum_fees_quote=0))
        connection.execute(ExchangeCredential.__table__.insert().values(bot_id=owned.store.bot_id, exchange="okx", connector="okx_perpetual",
                                                                        environment="demo", version=1, account_id="www:account", state="revoking", envelope={"opaque": "encrypted"},
                                                                        changed_by="operator", changed_at=owned.store.read()["heartbeat"]))
    scope = CleanupOrderScope("disconnect", 2, owned.owner.generation, "held-long", "BTC-USDT", "LONG", Decimal("0.1"), 1, now + 5000)
    order = InFlightOrder(client_order_id="close-1", trading_pair="BTC-USDT", order_type=OrderType.MARKET, trade_type=TradeType.SELL,
                          amount=Decimal("0.1"), price=Decimal("100"), creation_timestamp=now / 1000, position=PositionAction.CLOSE)
    owned.market.in_flight_orders[order.client_order_id] = order
    owned.market.tracking_states[order.client_order_id] = order.to_json()
    return scope, order


def request(**changes):
    payload = dict(clOrdId="close-1", instId="BTC-USDT-SWAP", tdMode="cross", ordType="market", side="sell", posSide="long", sz="0.1")
    return SimpleNamespace(url="https://offline.invalid/api/v5/trade/order", data=json.dumps({**payload, **changes}), headers={})


def test_only_scoped_reduce_close_can_send_after_stop_and_expired_session(owned):
    scope, order = prepare(owned)
    with pytest.raises(SubmissionBlocked):
        owned.owner.before_order(owned.market, order)
    with cleanup_order_scope(scope):
        owned.owner.before_order(owned.market, order)
    with owned.manager.get_new_session() as session:
        saved = session.get(Order, "close-1")
        assert saved.cleanup_operation_id == "disconnect" and saved.cleanup_scope["position_id"] == "held-long"
    rest = request()
    owned.loop.run_until_complete(owned.owner.authorize_rest(rest))
    assert int(rest.headers["expTime"]) <= scope.deadline_ms
    with pytest.raises(SubmissionBlocked):
        owned.loop.run_until_complete(owned.owner.authorize_rest(request()))


@pytest.mark.parametrize("changes", [dict(side="buy"), dict(posSide="short"), dict(tdMode="isolated"), dict(ordType="limit"),
                                     dict(sz="0.2"), dict(instId="ETH-USDT-SWAP")])
def test_cleanup_final_gate_rejects_payload_tampering(owned, changes):
    scope, order = prepare(owned)
    with cleanup_order_scope(scope):
        owned.owner.before_order(owned.market, order)
    with pytest.raises(SubmissionBlocked):
        owned.loop.run_until_complete(owned.owner.authorize_rest(request(**changes)))


def test_cleanup_authority_does_not_bypass_shutdown_or_stale_credential_version(owned):
    scope, order = prepare(owned)
    with cleanup_order_scope(scope):
        owned.owner.before_order(owned.market, order)
    owned.owner.begin_shutdown()
    with pytest.raises(SubmissionBlocked):
        owned.loop.run_until_complete(owned.owner.authorize_rest(request()))


@pytest.mark.parametrize('change', ['owner', 'credential', 'stage', 'deadline', 'second_intent'])
def test_changed_cleanup_authority_cannot_send_or_create_a_second_close(owned, change):
    from dataclasses import replace
    scope, order = prepare(owned)
    with cleanup_order_scope(scope):
        owned.owner.before_order(owned.market, order)
    if change == 'deadline':
        owned.owner.cleanup_intents[order.client_order_id] = replace(scope, deadline_ms=1)
    elif change == 'second_intent':
        another = InFlightOrder(client_order_id='close-2', trading_pair=order.trading_pair, order_type=order.order_type,
                                trade_type=order.trade_type, amount=order.amount, price=order.price, creation_timestamp=order.creation_timestamp, position=order.position)
        with pytest.raises(SubmissionBlocked), cleanup_order_scope(scope):
            owned.owner.before_order(owned.market, another)
        return
    else:
        with owned.engine.begin() as connection:
            if change == 'owner':
                connection.execute(update(BotState).values(owner_uuid='new-owner', owner_generation=owned.owner.generation + 1))
            elif change == 'credential':
                connection.execute(update(ExchangeCredential).values(version=2))
            else:
                recovery = owned.store.read()['recovery_state']
                recovery['credential_operation']['stage'] = 'action_required'
                connection.execute(update(BotState).values(recovery_state=recovery))
    with pytest.raises((SubmissionBlocked, OwnershipLost)):
        owned.loop.run_until_complete(owned.owner.authorize_rest(request()))
