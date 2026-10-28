import asyncio
import json
import random
import time
from decimal import Decimal
from test.hummingbot.client.config.test_mm_profiles import CATALOGUE
from test.integration.postgres.test_ownership import owned as _owned
from types import MethodType
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import event, text

from hummingbot.client.config.mm_profiles import MMProfilesConfig
from hummingbot.connector.exchange.okx.okx_exchange import OkxExchange
from hummingbot.core.data_type.common import OrderType, TradeType
from hummingbot.core.data_type.in_flight_order import InFlightOrder
from hummingbot.core.data_type.trade_fee import AddedToCostTradeFee, TokenAmount, TradeFeeSchema
from hummingbot.core.event.events import MarketEvent, OrderFilledEvent
from hummingbot.core.mm_session import MMSession, MMSessionRequest
from hummingbot.core.order_submission_guard import SubmissionBlocked, session_order_scope
from hummingbot.core.spot_outflow import SpotOutflowPolicy
from hummingbot.core.spot_outflow_guard import SpotOutflowGuard
from hummingbot.core.web_assistant.connections.data_types import RESTMethod, RESTRequest
from hummingbot.model.db_migration.postgres.io_deadline import bootstrap_io_budget
from hummingbot.model.order import Order
from hummingbot.model.trade_fill import TradeFill

owned = _owned


def setup(context, **changes):
    data = asyncio.run(MMProfilesConfig.load(CATALOGUE)).model_dump(mode="json")
    data["spot_outflow"] = dict(window_seconds=10, max_net_outflow_quote="100", fee_allowance_rate=".01")
    data["spot_outflow"].update(changes)
    catalogue = MMProfilesConfig.model_validate(data)
    session = MMSession.create(catalogue, MMSessionRequest(market_mode="spot", profile_id="sideways", duration_seconds=60), rng=random.Random(1))
    session.ready(now=time.time(), anchors={"life_spot": dict(life=".1", sources={})})
    with context.engine.begin() as connection:
        connection.execute(text("UPDATE bot_state SET config_type='mm-session', config_file_path='life_mm_profiles.yaml', mm_session=CAST(:value AS jsonb)"),
                           {"value": json.dumps(session.model_dump(mode="json"))})
    context.owner.config_file_path = context.recorder._config_file_path = "life_mm_profiles.yaml"
    context.owner.mm_ready_check = lambda: True
    context.market.account_uid, context.market.trading_environment = "account-A", "demo"
    context.market.get_fee.return_value = AddedToCostTradeFee(percent=Decimal(".001"))
    context.owner.spot_outflow = SpotOutflowGuard(context.owner, catalogue.outflow_policy())
    asyncio.run(context.owner.spot_outflow.initialize())
    return session, catalogue


def buy(context, session, identity="a", side=TradeType.BUY, amount="5"):
    item = InFlightOrder(client_order_id=identity, trading_pair="LIFE-USDT", order_type=OrderType.LIMIT_MAKER,
                         trade_type=side, amount=Decimal(amount), price=Decimal("10"), creation_timestamp=time.time())
    context.market.in_flight_orders[identity] = item
    context.market.tracking_states[identity] = item.to_json()
    with session_order_scope(session.id, session.generation):
        context.owner.before_order(context.market, item)
    return item


def test_binding_and_parallel_pre_intent_final_gate_count_one_reservation(owned):
    session, _ = setup(owned)
    first = buy(owned, session)
    with pytest.raises(SubmissionBlocked, match="outflow"):
        buy(owned, session, "b")
    assert owned.owner.spot_outflow.status()["reserved_quote"] == Decimal("50.5")
    request = RESTRequest(method=RESTMethod.POST, url="https://www.okx.com/api/v5/trade/order", is_auth_required=True,
                          data=json.dumps(dict(clOrdId="a", instId="LIFE-USDT", side="buy", sz="5", px="10", tdMode="cash")))
    asyncio.run(owned.owner.authorize_rest(request))
    assert owned.owner.spot_outflow.status()["reserved_quote"] == Decimal("50.5")
    assert first.client_order_id in owned.owner.sent_intents
    assert owned.store.read()["recovery_state"]["spot_outflow"]["binding"] == dict(account_id="account-A", environment="demo", trading_pair="LIFE-USDT")


def test_delayed_commit_partial_fill_and_duplicates_do_not_return_capacity_early(owned):
    session, _ = setup(owned)
    item = buy(owned, session)
    item.executed_amount_base = Decimal("2")
    assert owned.owner.spot_outflow.status()["reserved_quote"] == Decimal("50.5")
    evt = OrderFilledEvent(timestamp=time.time() - 1, order_id="a", trading_pair="LIFE-USDT", trade_type=TradeType.BUY,
                           order_type=OrderType.LIMIT_MAKER, price=Decimal("10"), amount=Decimal("2"),
                           trade_fee=AddedToCostTradeFee(flat_fees=[TokenAmount("USDT", Decimal(".2"))]), exchange_trade_id="1")
    for _ in range(2):
        owned.recorder._did_fill_order(MarketEvent.OrderFilled.value, owned.market, evt)
    status = owned.owner.spot_outflow.status()
    assert status["recorded_net_outflow_quote"] == Decimal("20.2")
    assert status["reserved_quote"] == Decimal("30.3")
    buy(owned, session, "sell", TradeType.SELL)


def test_final_send_rechecks_cashflow_after_a_late_commit_and_unsent_denial_preserves_sells(owned):
    session, _ = setup(owned)
    buy(owned, session, "other", amount="4")
    queued = buy(owned, session)
    # Actual charges can exceed an earlier fee allowance. A queued proposal is
    # not authorized by its pre-intent decision after the committed cashflow changes.
    evt = OrderFilledEvent(timestamp=time.time() - .001, order_id="other", trading_pair="LIFE-USDT", trade_type=TradeType.BUY,
                           order_type=OrderType.LIMIT_MAKER, price=Decimal("10"), amount=Decimal("4"),
                           trade_fee=AddedToCostTradeFee(flat_fees=[TokenAmount("USDT", Decimal("21"))]), exchange_trade_id="1")
    owned.recorder._did_fill_order(MarketEvent.OrderFilled.value, owned.market, evt)
    request = RESTRequest(method=RESTMethod.POST, url="https://www.okx.com/api/v5/trade/order", is_auth_required=True,
                          data=json.dumps(dict(clOrdId="a", instId="LIFE-USDT", side="buy", sz="5", px="10", tdMode="cash")))
    with pytest.raises(SubmissionBlocked, match="spot_outflow_limit"):
        asyncio.run(owned.owner.authorize_rest(request))
    owned.owner.order_failed(owned.market, queued)
    assert "a" not in owned.owner.sent_intents
    buy(owned, session, "sell", TradeType.SELL)
    assert not owned.store.read()["recovery_state"].get("halt")


def test_rebuild_retains_setup_authority_and_binding_halt_cannot_reopen_new_orders(owned):
    from types import SimpleNamespace
    _, catalogue = setup(owned)
    owned.market.ready = True
    owned.market._update_positions = AsyncMock()
    owned.market.account_positions = {}
    owned.owner._open_orders = AsyncMock(return_value=[])
    core = SimpleNamespace(connector_manager=SimpleNamespace(connectors={"okx": owned.market}),
                           trade_fill_db=owned.manager, markets_recorder=owned.recorder,
                           strategy=SimpleNamespace(config=SimpleNamespace(
                               mm_drawdown_scope=None, mm_spot_outflow_policy=SpotOutflowPolicy.model_validate(catalogue.outflow_policy()))))

    async def during_rebuild(guard):
        assert guard.owner.reconciling  # Account setup can finish during async reconstruction.
        guard.owner.require_recovery("spot_outflow_binding_mismatch")
        return False

    with patch.object(SpotOutflowGuard, "initialize", during_rebuild):
        with pytest.raises(SubmissionBlocked, match="outflow"):
            asyncio.run(owned.owner.reconcile(core))
    assert not owned.owner.allow_new_orders and not owned.owner.reconciling
    assert owned.store.read()["recovery_state"]["halt"]


@pytest.mark.parametrize("attribute,value", [("account_uid", "account-B"), ("trading_environment", "live")])
def test_verified_binding_cannot_mix_another_account_or_environment(owned, attribute, value):
    _, catalogue = setup(owned)
    binding = owned.store.read()["recovery_state"]["spot_outflow"]
    setattr(owned.market, attribute, value)
    replacement = SpotOutflowGuard(owned.owner, catalogue.outflow_policy())
    asyncio.run(replacement.initialize())
    assert not replacement.status()["ready"]
    assert replacement.status()["reason_code"] == "spot_outflow_binding_mismatch"
    assert owned.store.read()["recovery_state"]["spot_outflow"] == binding
    assert owned.store.read()["recovery_state"]["halt"]


def test_armed_policy_cannot_be_disabled_and_proven_unsent_denial_does_not_halt(owned):
    session, catalogue = setup(owned)
    item = buy(owned, session)
    owned.owner.order_failed(owned.market, item)
    assert owned.owner.spot_outflow.status()["reserved_quote"] == 0
    assert not owned.store.read()["recovery_state"].get("halt")
    data = catalogue.model_dump(mode="json")
    data["spot_outflow"] = None
    with pytest.raises(Exception, match="outflow"):
        owned.store.request_mm_session(action="switch", expected_version=1, current_session_id=session.id,
                                       catalogue=MMProfilesConfig.model_validate(data),
                                       request=MMSessionRequest(market_mode="spot", profile_id="sideways", duration_seconds=60))


def test_terminal_read_timeout_keeps_reservation_and_only_blocks_buys(owned):
    session, _ = setup(owned)
    item = buy(owned, session)
    item.update_exchange_order_id("remote-a")
    owned.market._request_order_update = AsyncMock(side_effect=asyncio.TimeoutError())
    owned.owner.spot_outflow.mark_terminal_pending("a")
    asyncio.run(owned.owner.spot_outflow.reconcile_terminal())
    assert owned.owner.spot_outflow.status()["reserved_quote"] == Decimal("50.5")
    with pytest.raises(SubmissionBlocked, match="outflow"):
        buy(owned, session, "b")
    buy(owned, session, "sell", TradeType.SELL)
    assert not owned.store.read()["recovery_state"].get("halt")


def historical_fill(context, identity="old", timestamp=None, fee=".1"):
    stamp = int(time.time() * 1000) - 1 if timestamp is None else timestamp
    with context.manager.get_new_session() as session, session.begin():
        session.add(Order(id=identity, config_file_path="life_mm_profiles.yaml", strategy="v2", market="okx", symbol="LIFE-USDT",
                          base_asset="LIFE", quote_asset="USDT", creation_timestamp=stamp, order_type="LIMIT_MAKER",
                          amount=Decimal("1"), price=Decimal("10"), last_status="BuyOrderCompleted", last_update_timestamp=stamp,
                          creation_pending=False))
        session.flush()
        session.add(TradeFill(config_file_path="life_mm_profiles.yaml", strategy="v2", market="okx", symbol="LIFE-USDT",
                              base_asset="LIFE", quote_asset="USDT", timestamp=stamp, order_id=identity,
                              exchange_trade_id="1", trade_type="BUY", order_type="LIMIT_MAKER", price=Decimal("10"), amount=Decimal("1"),
                              trade_fee=dict(percent="0", percent_token=None, fee_type="AddedToCost", flat_fees=[dict(token="USDT", amount=fee)])))


def test_unbound_history_waits_a_full_observed_window_without_relabelling_cashflow(owned):
    historical_fill(owned)
    session, _ = setup(owned)
    status = owned.owner.spot_outflow.status()
    assert not status["ready"] and status["reason_code"] == "spot_outflow_legacy_window_uncovered"
    assert status["recorded_net_outflow_quote"] is None
    with pytest.raises(SubmissionBlocked, match="outflow"):
        buy(owned, session, "new")
    buy(owned, session, "sell", TradeType.SELL)
    owned.owner.spot_outflow.projection.advance(status["coverage_start_ms"] + 10000)
    assert owned.owner.spot_outflow.status()["ready"]


def test_reconstruction_after_switch_preserves_committed_spending_and_policy(owned):
    session, catalogue = setup(owned)
    historical_fill(owned)
    changed = owned.store.request_mm_session(action="switch", expected_version=1, current_session_id=session.id,
                                             catalogue=catalogue, request=MMSessionRequest(market_mode="perpetual", profile_id="sideways", duration_seconds=60))
    owned.owner.prepare(changed["control_version"], "life_mm_profiles.yaml")
    replacement = SpotOutflowGuard(owned.owner, catalogue.outflow_policy())
    asyncio.run(replacement.initialize())
    assert replacement.status()["recorded_net_outflow_quote"] == Decimal("10.1")
    assert replacement.status()["remaining_quote"] == Decimal("89.9")


def test_original_committed_fill_timestamp_wins_over_a_duplicate_event_timestamp(owned):
    session, _ = setup(owned)
    buy(owned, session)
    evt = OrderFilledEvent(timestamp=time.time() - 1, order_id="a", trading_pair="LIFE-USDT", trade_type=TradeType.BUY,
                           order_type=OrderType.LIMIT_MAKER, price=Decimal("10"), amount=Decimal("1"),
                           trade_fee=AddedToCostTradeFee(), exchange_trade_id="1")
    owned.recorder._did_fill_order(MarketEvent.OrderFilled.value, owned.market, evt)
    owned.recorder._did_fill_order(MarketEvent.OrderFilled.value, owned.market, evt._replace(timestamp=evt.timestamp + .5))
    assert owned.owner.spot_outflow.status()["ready"]
    assert owned.owner.spot_outflow.status()["recorded_net_outflow_quote"] == 10


def test_terminal_proof_backfills_actual_fills_then_releases_only_unfilled_remainder(owned):
    session, _ = setup(owned)
    item = buy(owned, session)
    item.update_exchange_order_id("1")
    stamp = str(int(time.time() * 1000))
    detail = dict(ordId="1", clOrdId="a", instId="LIFE-USDT", side="buy", sz="5", px="10", state="canceled", accFillSz="2", uTime=stamp)
    owned.market._request_order_update = AsyncMock(return_value=dict(code="0", data=[detail]))
    owned.market._request_order_fills = AsyncMock(return_value=dict(code="0", data=[dict(
        tradeId="1", billId="2", ordId="1", fillSz="2", fillPx="10", fee="-.2", feeCcy="USDT", ts=str(int(stamp) - 1000))]))
    owned.market._trade_update_from_fill = MethodType(OkxExchange._trade_update_from_fill, owned.market)
    owned.market.trade_fee_schema.return_value = TradeFeeSchema()
    asyncio.run(owned.owner.spot_outflow.reconcile_terminal(all_orders=True))
    status = owned.owner.spot_outflow.status()
    assert status["reserved_quote"] == 0 and status["recorded_net_outflow_quote"] == Decimal("20.2")
    assert not owned.owner.spot_outflow.terminal_pending
    with owned.manager.get_new_session() as session:
        assert session.query(TradeFill).count() == 1
        assert not session.get(Order, "a").creation_pending


def test_large_window_rebuild_has_bounded_queries_and_no_sql_on_projection_or_reservation(owned):
    _, catalogue = setup(owned, window_seconds=60)
    historical_fill(owned)
    stamp = int(time.time() * 1000) - 1
    with owned.engine.begin() as connection, bootstrap_io_budget(connection, time.monotonic() + 60):
        connection.execute(text("SET LOCAL statement_timeout = '60s'"))
        connection.execute(text("""INSERT INTO trade_fills
            (market, order_id, exchange_trade_id, config_file_path, strategy, symbol, base_asset, quote_asset,
             timestamp, trade_type, order_type, price, amount, trade_fee, leverage, position)
            SELECT 'okx', 'old', 'load-' || value, 'life_mm_profiles.yaml', 'v2', 'LIFE-USDT', 'LIFE', 'USDT',
                   :stamp, 'BUY', 'LIMIT_MAKER', 0.1, 0.000001, CAST(:fee AS jsonb), 1, 'NIL'
            FROM generate_series(1,100000) AS value"""), dict(stamp=stamp, fee=json.dumps(dict(percent="0", flat_fees=[]))))
    statements = []

    def observe(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)
    event.listen(owned.engine, "before_cursor_execute", observe)
    try:
        replacement = SpotOutflowGuard(owned.owner, catalogue.outflow_policy())
        started = time.monotonic()
        asyncio.run(replacement.initialize())
        assert len(statements) <= 4
        assert replacement.status()["fill_count"] == 100001
        assert replacement.status()["recorded_net_outflow_quote"] == Decimal("10.11")
        row = owned.owner.check_owner()
        statements.clear()
        candidate = InFlightOrder(
            client_order_id="load-buy", trading_pair="LIFE-USDT", order_type=OrderType.LIMIT_MAKER,
            trade_type=TradeType.BUY, amount=Decimal("1"), price=Decimal("10"), creation_timestamp=time.time())
        hot_start = time.monotonic()
        for _ in range(1000):
            replacement.before_order(owned.market, candidate, row)
            replacement.status()
        assert not statements
        print(dict(rows=100001, rebuild_seconds=time.monotonic() - started, hot_1000_seconds=time.monotonic() - hot_start,
                   additional_hot_path_queries=len(statements)))
    finally:
        event.remove(owned.engine, "before_cursor_execute", observe)
