import asyncio
import json
import time
from contextlib import asynccontextmanager
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import text

from hummingbot.client.config.client_config_map import MarketDataCollectionConfigMap
from hummingbot.connector.markets_recorder import MarketsRecorder
from hummingbot.core.bot_ownership import ACCOUNT_PATHS, ORDER_PATH, BotOwnership, OwnershipLost
from hummingbot.core.data_type.common import OrderType, TradeType
from hummingbot.core.data_type.in_flight_order import InFlightOrder, OrderState
from hummingbot.core.order_submission_guard import SubmissionBlocked, install_guard, session_order_scope
from hummingbot.core.web_assistant.connections.data_types import RESTMethod
from hummingbot.core.web_assistant.rest_assistant import RESTAssistant
from hummingbot.model.bot_state_store import BotStateStore
from hummingbot.model.db_migration.postgres.runner import bootstrap_postgres
from hummingbot.model.market_state import MarketState
from hummingbot.model.order import Order
from hummingbot.model.sql_connection_manager import SQLConnectionManager, SQLConnectionType


@pytest.fixture
def owned(pg_engine, monkeypatch):
    bootstrap_postgres(pg_engine, auto_migrate=True)
    store = BotStateStore(pg_engine, "hummingbot")
    store.ensure()
    store.control(expected_version=0, desired_state="running", config_file_path="test.yml", config_type="v1-strategy")
    owner = BotOwnership(pg_engine, "hummingbot")
    assert owner.try_acquire()
    owner.prepare(1, "test.yml")
    owner.allow_new_orders = True
    owner.reconciling = False
    monkeypatch.setenv("DATABASE_URL", "postgresql://test-unused/db")
    monkeypatch.setenv("K_SERVICE", "offline-test")
    with patch.object(SQLConnectionManager, "_postgres_engine", pg_engine):
        manager = SQLConnectionManager(None, SQLConnectionType.TRADE_FILLS)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    previous = MarketsRecorder._shared_instance
    market = MagicMock()
    market.name = market.display_name = "okx"
    market.in_flight_orders = {}
    market.tracking_states = {}
    recorder = MarketsRecorder(manager, [market], "test.yml", "pure_market_making",
                               MarketDataCollectionConfigMap(market_data_collection_enabled=False))
    owner.connectors = {"okx": market}
    install_guard(owner)
    try:
        yield SimpleNamespace(owner=owner, store=store, engine=pg_engine, market=market,
                              manager=manager, recorder=recorder, loop=loop)
    finally:
        pending = asyncio.all_tasks(loop)
        for task in pending:
            task.cancel()
        if pending:
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        owner.release()
        install_guard(None)
        MarketsRecorder._shared_instance = previous
        loop.close()
        asyncio.set_event_loop(None)


def intent(context, order_id="order-1"):
    order = InFlightOrder(client_order_id=order_id, trading_pair="BTC-USDT", order_type=OrderType.LIMIT,
                          trade_type=TradeType.BUY, amount=Decimal("0.1"), price=Decimal("100"),
                          creation_timestamp=1700000000)
    context.market.in_flight_orders[order_id] = order
    context.market.tracking_states[order_id] = order.to_json()
    context.owner.before_order(context.market, order)
    return order


class Throttler:
    def __init__(self, enter=None, release=None):
        self.enter = enter
        self.release = release

    @asynccontextmanager
    async def execute_task(self, limit_id):
        if self.enter:
            self.enter.set()
        if self.release:
            await self.release.wait()
        yield


def assistant(throttler=None):
    response = MagicMock()
    response.status = 200
    response.json = AsyncMock(return_value={"code": "0", "data": [{"ordId": "exchange-1", "sCode": "0"}]})
    connection = MagicMock()
    connection.call = AsyncMock(return_value=response)
    return RESTAssistant(connection, throttler or Throttler()), connection


async def send(rest, path=ORDER_PATH):
    return await rest.execute_request(url="https://offline.invalid" + path, method=RESTMethod.POST,
                                      throttler_limit_id="order", is_auth_required=True,
                                      data={"clOrdId": "order-1", "instId": "BTC-USDT", "side": "buy", "sz": "0.1"})


def test_mm_final_gate_rejects_expired_or_superseded_step_while_cancel_remains_allowed(owned):
    snapshot = {"id": "session-1", "generation": 0, "status": "running", "started_at": time.time() - 1,
                "expires_at": time.time() + 60, "step_expires_at": time.time() + 60}
    with owned.engine.begin() as connection:
        connection.execute(text("UPDATE bot_state SET config_type='mm-session', mm_session=CAST(:state AS jsonb)"),
                           {"state": json.dumps(snapshot)})
    owned.owner.mm_ready_check = lambda: True
    with session_order_scope("session-1", 0):
        intent(owned)
    # A new draw in the same control version must revoke a queued REST attempt.
    with owned.engine.begin() as connection:
        connection.execute(text("UPDATE bot_state SET mm_session=jsonb_set(mm_session, '{generation}', '1')"))
    rest, transport = assistant()
    with session_order_scope("session-1", 0):
        with pytest.raises(SubmissionBlocked):
            owned.loop.run_until_complete(send(rest))
    transport.call.assert_not_awaited()
    owned.loop.run_until_complete(send(rest, path="/api/v5/trade/cancel-order"))
    assert transport.call.await_count == 1


def test_strict_quote_guard_is_rechecked_after_throttle_and_does_not_block_cancel(owned):
    from test.hummingbot.strategy_v2.controllers.test_quote_guard import observation, ready_guard
    guard = ready_guard()
    current = [observation(timestamp=1002, now=1002)]
    snapshot = {"id": "session-guard", "generation": 0, "status": "running", "started_at": time.time() - 1,
                "expires_at": time.time() + 60, "step_expires_at": time.time() + 60}
    with owned.engine.begin() as connection:
        connection.execute(text("UPDATE bot_state SET config_type='mm-session', mm_session=CAST(:state AS jsonb)"),
                           {"state": json.dumps(snapshot)})
    owned.owner.mm_ready_check = lambda: guard.evaluate(current[0], Decimal("100"), time.monotonic())["ready"]
    with session_order_scope("session-guard", 0):
        tracked = intent(owned)

    class ChangedBookThrottle:
        @asynccontextmanager
        async def execute_task(self, limit_id):
            current[0] = observation(bids=[(109, 20)], asks=[(111, 20)], timestamp=1003, now=1003)
            yield

    rest, transport = assistant(ChangedBookThrottle())
    with pytest.raises(SubmissionBlocked):
        owned.loop.run_until_complete(send(rest))
    transport.call.assert_not_awaited()
    owned.owner.order_failed(owned.market, tracked)
    owned.loop.run_until_complete(send(rest, path="/api/v5/trade/cancel-order"))
    assert transport.call.await_count == 1
    assert not owned.store.read()["recovery_state"].get("halt")


def test_expiry_during_quote_guard_recovery_still_revokes_send(owned):
    from test.hummingbot.strategy_v2.controllers.test_quote_guard import observation, ready_guard
    guard = ready_guard()
    snapshot = {"id": "session-guard", "generation": 0, "status": "running", "started_at": time.time() - 1,
                "expires_at": time.time() + 60, "step_expires_at": time.time() + 60}
    with owned.engine.begin() as connection:
        connection.execute(text("UPDATE bot_state SET config_type='mm-session', mm_session=CAST(:state AS jsonb)"),
                           {"state": json.dumps(snapshot)})
    owned.owner.mm_ready_check = lambda: guard.evaluate(observation(), Decimal("100"), 100)["ready"]
    with session_order_scope("session-guard", 0):
        tracked = intent(owned)
    guard.evaluate(observation(), Decimal("120"), 3)
    with owned.engine.begin() as connection:
        connection.execute(text("UPDATE bot_state SET mm_session=jsonb_set(mm_session, '{expires_at}', to_jsonb(0))"))
    # Even a recovered market cannot renew the persisted session deadline.
    guard.evaluate(observation(timestamp=1004, now=1004), Decimal("100"), 4, sample=True)
    guard.evaluate(observation(timestamp=1006, now=1006), Decimal("100"), 6, sample=True)
    rest, transport = assistant()
    with pytest.raises(SubmissionBlocked):
        owned.loop.run_until_complete(send(rest))
    transport.call.assert_not_awaited()
    owned.owner.order_failed(owned.market, tracked)


def test_mm_intent_requires_scope_and_deadline(owned):
    snapshot = {"id": "session-1", "generation": 0, "status": "running", "started_at": time.time() - 120,
                "expires_at": time.time() - 1, "step_expires_at": time.time() - 1}
    with owned.engine.begin() as connection:
        connection.execute(text("UPDATE bot_state SET config_type='mm-session', mm_session=CAST(:state AS jsonb)"),
                           {"state": json.dumps(snapshot)})
    owned.owner.mm_ready_check = lambda: True
    with session_order_scope("session-1", 0):
        with pytest.raises(SubmissionBlocked):
            intent(owned)
    with pytest.raises(SubmissionBlocked):
        intent(owned)


def test_two_process_sessions_have_single_owner_and_increment_generation(owned):
    successor = BotOwnership(owned.engine, "hummingbot")
    assert successor.try_acquire() is False
    first_generation = owned.owner.generation
    owned.owner.release()
    assert successor.try_acquire()
    try:
        assert successor.generation == first_generation + 1
        assert owned.owner.store.read()["owner_uuid"] == successor.owner_uuid
    finally:
        successor.release()


def test_intent_precedes_wire_and_duplicate_physical_send_is_blocked(owned):
    intent(owned)
    with owned.manager.get_new_session() as session:
        assert session.get(Order, "order-1").creation_pending
        assert "order-1" in session.query(MarketState).one().saved_state
    rest, transport = assistant()
    owned.loop.run_until_complete(send(rest))
    assert transport.call.await_count == 1
    assert int(transport.call.call_args.args[0].headers["expTime"]) > 0
    with pytest.raises(SubmissionBlocked, match="already sent"):
        owned.loop.run_until_complete(send(rest))
    assert transport.call.await_count == 1


def test_lock_loss_during_throttle_wait_never_reaches_transport(owned):
    async def scenario():
        intent(owned)
        entered, release = asyncio.Event(), asyncio.Event()
        rest, transport = assistant(Throttler(entered, release))
        task = asyncio.create_task(send(rest))
        await asyncio.wait_for(entered.wait(), 1)
        with owned.engine.begin() as connection:
            connection.execute(text("SELECT pg_terminate_backend(:pid)"), {"pid": owned.owner.backend_pid})
        successor = BotOwnership(owned.engine, "hummingbot")
        assert successor.try_acquire()
        try:
            release.set()
            with pytest.raises(OwnershipLost):
                await task
            transport.call.assert_not_awaited()
            assert successor.store.read()["recovery_state"]["halt"]
        finally:
            successor.release()
    owned.loop.run_until_complete(scenario())


def test_inflight_uncertainty_survives_handover_and_further_restart(owned):
    async def scenario():
        intent(owned)
        accepted, finish = asyncio.Event(), asyncio.Event()
        rest, transport = assistant()
        response = transport.call.return_value

        async def delayed(request):
            accepted.set()
            await finish.wait()
            return response

        transport.call.side_effect = delayed
        task = asyncio.create_task(send(rest))
        await asyncio.wait_for(accepted.wait(), 1)
        with owned.engine.begin() as connection:
            connection.execute(text("SELECT pg_terminate_backend(:pid)"), {"pid": owned.owner.backend_pid})
        successor = BotOwnership(owned.engine, "hummingbot")
        assert successor.try_acquire()
        try:
            successor.prepare(1, "test.yml")
            successor.allow_new_orders = True
            with pytest.raises(SubmissionBlocked):
                successor.check_owner(new_risk=True)
            # Even if readonly exchange reconciliation later resolves the order,
            # automatic restart must not erase the durable unresolved-effect gate.
            with owned.engine.begin() as connection:
                connection.execute(text("UPDATE public.orders SET creation_pending=false"))
            finish.set()
            await task
        finally:
            successor.release()
        third = BotOwnership(owned.engine, "hummingbot")
        assert third.try_acquire()
        try:
            assert third.store.read()["recovery_state"]["halt"]
        finally:
            third.release()
    owned.loop.run_until_complete(scenario())


def test_stale_process_cannot_write_tracking_state(owned):
    intent(owned)
    with owned.engine.begin() as connection:
        connection.execute(text("SELECT pg_terminate_backend(:pid)"), {"pid": owned.owner.backend_pid})
    successor = BotOwnership(owned.engine, "hummingbot")
    assert successor.try_acquire()
    try:
        with pytest.raises(OwnershipLost):
            with owned.manager.get_new_session() as session, session.begin():
                session.query(MarketState).one().saved_state = {"stale": "must not overwrite"}
        with owned.engine.connect() as connection:
            state = connection.execute(text("SELECT saved_state FROM public.market_states")).scalar()
        assert "order-1" in state and "stale" not in state
    finally:
        successor.release()


def test_control_change_after_intent_blocks_wire_and_unsent_identity_can_be_rejected(owned):
    order = intent(owned)
    owned.store.control(expected_version=1, desired_state="stopped")
    rest, transport = assistant()
    with pytest.raises(SubmissionBlocked):
        owned.loop.run_until_complete(send(rest))
    transport.call.assert_not_awaited()
    owned.owner.order_failed(owned.market, order)
    with owned.manager.get_new_session() as session:
        assert not session.get(Order, "order-1").creation_pending
        assert "order-1" not in session.query(MarketState).one().saved_state


def test_tracking_snapshot_excludes_queued_creates_without_durable_intent(owned):
    queued = InFlightOrder(client_order_id="queued-without-intent", trading_pair="BTC-USDT", order_type=OrderType.LIMIT,
                           trade_type=TradeType.BUY, amount=Decimal("0.1"), price=Decimal("100"), creation_timestamp=1700000000)
    owned.market.tracking_states[queued.client_order_id] = queued.to_json()
    intent(owned)
    with owned.manager.get_new_session() as session:
        assert session.get(Order, queued.client_order_id) is None
        assert set(session.query(MarketState).one().saved_state) == {"order-1"}
    # A superseding switch rejects both tasks. Only the persisted intent needs
    # durable rejection; a queued sibling must never be restored as a remote order.
    owned.owner.order_failed(owned.market, queued)
    owned.owner.order_failed(owned.market, owned.market.in_flight_orders["order-1"])
    with owned.manager.get_new_session() as session:
        assert session.query(MarketState).one().saved_state == {}


def test_account_mutation_pending_before_wire_and_cleared_only_after_positive_ack(owned):
    rest, transport = assistant()
    response = transport.call.return_value

    async def verify(request):
        assert owned.store.read()["recovery_state"]["pending_mutations"]
        return response

    transport.call.side_effect = verify
    owned.loop.run_until_complete(send(rest, sorted(ACCOUNT_PATHS)[0]))
    assert not owned.store.read()["recovery_state"].get("pending_mutations")
    assert not owned.owner.account_lock.locked()


def test_account_mutation_transport_failure_is_durable_and_closes_gate(owned):
    rest, transport = assistant()
    transport.call.side_effect = asyncio.TimeoutError
    with pytest.raises(asyncio.TimeoutError):
        owned.loop.run_until_complete(send(rest, sorted(ACCOUNT_PATHS)[0]))
    row = owned.store.read()
    assert row["recovery_state"]["halt"] and row["recovery_state"]["pending_mutations"]
    assert not owned.owner.allow_new_orders and not owned.owner.account_lock.locked()


def test_postgres_mutations_without_service_owner_are_rejected(owned):
    install_guard(None)
    rest, transport = assistant()
    with pytest.raises(SubmissionBlocked, match="service ownership"):
        owned.loop.run_until_complete(send(rest))
    transport.call.assert_not_awaited()


def reconciliation_core(owned):
    owned.market.ready = True
    del owned.market._update_positions
    return SimpleNamespace(connector_manager=SimpleNamespace(connectors={"okx": owned.market}),
                           strategy=SimpleNamespace(_is_stop_triggered=False, max_global_pnl=Decimal("0"),
                                                    max_pnl_by_controller={}, drawdown_exited_controllers=[]),
                           trade_fill_db=owned.manager, markets_recorder=owned.recorder, KILL_TIMEOUT=20)


def test_reconciliation_blocks_unknown_exchange_orders_before_strategy_can_run(owned):
    core = reconciliation_core(owned)
    owned.owner._open_orders = AsyncMock(return_value=[{"clOrdId": "other-writer"}])
    with pytest.raises(SubmissionBlocked, match="untracked"):
        owned.loop.run_until_complete(owned.owner.reconcile(core))
    assert not owned.owner.allow_new_orders
    assert owned.store.read()["recovery_state"]["halt"]


def test_reconciliation_reads_back_cancellation_before_enabling_strategy(owned):
    core = reconciliation_core(owned)
    order = intent(owned)
    order.current_state = OrderState.OPEN
    with owned.manager.get_new_session() as session, session.begin():
        session.get(Order, order.client_order_id).creation_pending = False
    owned.owner._open_orders = AsyncMock(return_value=[{"clOrdId": order.client_order_id}])
    observed = []

    async def refresh(connector, orders):
        observed.append(owned.owner.allow_new_orders)
        if connector.cancel_all.await_count:
            order.current_state = OrderState.CANCELED

    owned.market.cancel_all = AsyncMock()
    owned.owner._refresh_orders = AsyncMock(side_effect=refresh)
    owned.loop.run_until_complete(owned.owner.reconcile(core))
    assert owned.market.cancel_all.await_count == 1
    assert len(observed) == 2 and not any(observed)
    assert owned.owner.allow_new_orders


def test_checkpoint_restore_preserves_loss_limits_and_halt(owned):
    core = reconciliation_core(owned)
    owned.owner._mutate_recovery(lambda state: state.update(halt=True, strategy_checkpoints={"test.yml": {
        "_is_stop_triggered": True, "max_global_pnl": "12.345678", "max_pnl_by_controller": {"stable-id": "5.2"},
        "drawdown_exited_controllers": ["stable-id"]}}))
    owned.owner._open_orders = AsyncMock(return_value=[])
    with pytest.raises(SubmissionBlocked, match="prior unresolved"):
        owned.loop.run_until_complete(owned.owner.reconcile(core))
    assert core.strategy._is_stop_triggered
    assert core.strategy.max_global_pnl == Decimal("12.345678")
    assert core.strategy.max_pnl_by_controller == {"stable-id": Decimal("5.2")}
    assert core.strategy.drawdown_exited_controllers == ["stable-id"]
    assert not owned.owner.allow_new_orders


def test_position_mismatch_blocks_reconciliation(owned):
    core = reconciliation_core(owned)
    connector = SimpleNamespace(name="okx_perpetual", account_positions={"BTC": SimpleNamespace(
        amount=Decimal("0.1"), trading_pair="BTC-USDT", position_side=SimpleNamespace(name="LONG"))})
    with pytest.raises(SubmissionBlocked, match="positions"):
        BotOwnership._verify_positions(core, connector)


def test_clean_shutdown_checkpoints_before_cleanup_and_closes_runtime_marker(owned):
    core = reconciliation_core(owned)
    core.strategy.max_global_pnl = Decimal("42.5")

    async def shutdown(**kwargs):
        assert not owned.owner.allow_new_orders
        assert owned.store.read()["recovery_state"]["shutdown_incomplete"]
        core.strategy._is_stop_triggered = True
        await core.after_order_cancellation()
        return True

    core.shutdown = AsyncMock(side_effect=shutdown)
    assert owned.loop.run_until_complete(owned.owner.shutdown_core(core, timeout=1))
    recovery = owned.store.read()["recovery_state"]
    assert not recovery.get("halt") and not recovery["runtime_active"]
    assert not recovery.get("shutdown_incomplete")
    checkpoint = recovery["strategy_checkpoints"]["test.yml"]
    assert not checkpoint["_is_stop_triggered"] and checkpoint["max_global_pnl"] == "42.5"


def test_incomplete_shutdown_preserves_owner_and_durable_halt(owned):
    core = reconciliation_core(owned)

    async def stuck_shutdown(**kwargs):
        await asyncio.Event().wait()

    core.shutdown = AsyncMock(side_effect=stuck_shutdown)
    assert not owned.loop.run_until_complete(owned.owner.shutdown_core(core, timeout=0.02))
    recovery = owned.store.read()["recovery_state"]
    assert recovery["halt"] and recovery["shutdown_incomplete"] and recovery["runtime_active"]
    successor = BotOwnership(owned.engine, "hummingbot")
    assert not successor.try_acquire()


@pytest.mark.parametrize("transport_attempted", [False, True])
@pytest.mark.parametrize("terminal_state", [OrderState.FAILED, OrderState.CANCELED])
def test_shutdown_skips_only_proven_unsent_failed_creates(owned, transport_attempted, terminal_state):
    core = reconciliation_core(owned)
    order = intent(owned)
    order.current_state = OrderState.FAILED
    if transport_attempted:
        owned.owner.sent_intents.add(order.client_order_id)
        # Model a failed response with uncertain exchange execution. A final
        # read remains mandatory even if the connector marks the order failed.
        owned.owner.order_failed(owned.market, order)
    else:
        owned.owner.order_failed(owned.market, order)
    # A cancel-not-found acknowledgement can follow the send-gate rejection.
    order.current_state = terminal_state
    owned.owner._refresh_orders = AsyncMock()

    async def shutdown(**kwargs):
        await core.after_order_cancellation()
        return True
    core.shutdown = AsyncMock(side_effect=shutdown)
    owned.loop.run_until_complete(owned.owner.shutdown_core(core, timeout=1))
    assert owned.owner._refresh_orders.await_count == int(transport_attempted)


def test_fill_during_v2_shutdown_requires_position_reconciliation(owned):
    core = reconciliation_core(owned)
    core.strategy.executor_orchestrator = object()
    order = intent(owned)
    order.current_state = OrderState.OPEN

    async def refresh(connector, orders):
        order.executed_amount_base = Decimal("0.1")
        order.current_state = OrderState.FILLED

    owned.owner._refresh_orders = AsyncMock(side_effect=refresh)

    async def shutdown(**kwargs):
        try:
            await core.after_order_cancellation()
        except SubmissionBlocked:
            return False
        return True

    core.shutdown = AsyncMock(side_effect=shutdown)
    assert not owned.loop.run_until_complete(owned.owner.shutdown_core(core, timeout=1))
    recovery = owned.store.read()["recovery_state"]
    assert recovery["halt"] and recovery["runtime_active"] and recovery["shutdown_incomplete"]
    assert "fill arrived" in recovery["reason"]
    successor = BotOwnership(owned.engine, "hummingbot")
    assert not successor.try_acquire()


@pytest.mark.parametrize("race", ["covered", "uncovered", "after_checkpoint"])
def test_terminal_checkpoint_only_accepts_fills_covered_by_the_new_snapshot(owned, race):
    core = reconciliation_core(owned)
    core.strategy.executor_orchestrator = object()
    order = intent(owned)
    order.current_state = OrderState.OPEN
    with owned.manager.get_new_session() as session, session.begin():
        session.get(Order, order.client_order_id).creation_pending = False
    core.cancel_outstanding_orders = AsyncMock(return_value=True)

    async def refresh(connector, orders):
        order.executed_amount_base = Decimal("0.1")
        order.current_state = OrderState.CANCELED
        if race == "after_checkpoint" and owned.owner._refresh_orders.await_count > 1:
            order.executed_amount_base += Decimal("0.001")

    owned.owner._refresh_orders = AsyncMock(side_effect=refresh)

    async def shutdown(**kwargs):
        try:
            proof = await core.before_position_checkpoint(
                {order.client_order_id: ("okx", order)} if race != "uncovered" else {})
            assert proof[order.client_order_id]["executed_amount_base"] == "0.1"
            await core.after_order_cancellation()
        except SubmissionBlocked:
            return False
        return True

    core.shutdown = AsyncMock(side_effect=shutdown)
    assert owned.loop.run_until_complete(owned.owner.shutdown_core(core, timeout=1)) is (race == "covered")
    recovery = owned.store.read()["recovery_state"]
    assert bool(recovery.get("halt")) is (race != "covered")
    assert bool(recovery.get("shutdown_incomplete")) is (race != "covered")
    assert not owned.owner.allow_new_orders


@pytest.mark.parametrize("pruned", [False, True])
@pytest.mark.parametrize("initial", ["0", "0.011"])
def test_late_cancel_fill_round_trips_through_real_position_checkpoint(owned, monkeypatch, initial, pruned):
    from hummingbot.core.data_type.in_flight_order import TradeUpdate
    from hummingbot.core.data_type.trade_fee import AddedToCostTradeFee, TokenAmount
    from hummingbot.core.rate_oracle.rate_oracle import RateOracle
    from hummingbot.strategy_v2.executors.executor_orchestrator import ExecutorOrchestrator
    from hummingbot.strategy_v2.executors.position_executor.data_types import (
        PositionExecutorConfig,
        TripleBarrierConfig,
    )
    from hummingbot.strategy_v2.executors.position_executor.position_executor import PositionExecutor
    from hummingbot.strategy_v2.models.base import RunnableStatus
    from hummingbot.strategy_v2.models.executors import TrackedOrder

    monkeypatch.setattr(RateOracle, "get_instance", lambda: SimpleNamespace(
        get_pair_rate=lambda pair: Decimal("0.01") if pair == "USDT-BTC" else Decimal("100")))
    core = reconciliation_core(owned)
    order = intent(owned)
    order.current_state = OrderState.OPEN
    with owned.manager.get_new_session() as session, session.begin():
        session.get(Order, order.client_order_id).creation_pending = False
    owned.market.quantize_order_amount.side_effect = lambda trading_pair, amount, **kwargs: amount
    owned.market.quantize_order_price.side_effect = lambda trading_pair, price, **kwargs: price
    strategy = MagicMock()
    strategy.connectors = {"okx": owned.market}
    strategy.controllers = {"maker": MagicMock()}
    strategy.markets = {"okx": {"BTC-USDT"}}
    strategy.config = SimpleNamespace(mm_drawdown_scope=None)
    strategy.current_timestamp = 1700000000
    strategy.market_data_provider.get_price_by_type.return_value = Decimal("100")
    strategy.get_price.return_value = Decimal("100")
    config = PositionExecutorConfig(timestamp=1, controller_id="maker", connector_name="okx",
                                    trading_pair="BTC-USDT", side=TradeType.BUY, entry_price=Decimal("100"),
                                    amount=Decimal("0.1"), use_configured_entry_price=True,
                                    triple_barrier_config=TripleBarrierConfig(open_order_type=OrderType.LIMIT_MAKER))
    executor = PositionExecutor(strategy, config)
    executor._open_order = TrackedOrder(order_id=order.client_order_id)
    executor._open_order.order = order
    executor._status = RunnableStatus.RUNNING
    orchestrator = ExecutorOrchestrator(strategy)
    orchestrator.active_executors = {"maker": [executor]}
    orchestrator.positions_held = {"maker": []}
    core.strategy.executor_orchestrator = orchestrator

    def fill(identity, amount):
        order.update_with_trade_update(TradeUpdate(
            trade_id=identity, client_order_id=order.client_order_id, exchange_order_id="1",
            trading_pair="BTC-USDT", fill_timestamp=1700000001, fill_price=Decimal("100"),
            fill_base_amount=Decimal(amount), fill_quote_amount=Decimal(amount) * 100,
            fee=AddedToCostTradeFee(flat_fees=[TokenAmount("USDT", Decimal("0.000001"))])))

    if Decimal(initial):
        fill("first", initial)
    if pruned:
        # The executor still owns the reference when the connector has already
        # retired it from active orders during controller cleanup.
        owned.market.in_flight_orders.pop(order.client_order_id)

    async def refresh(connector, orders):
        assert executor.terminated.is_set()
        fill("late", "0.001")  # Repeated readback of the same fill remains idempotent.
        order.current_state = OrderState.CANCELED

    core.cancel_outstanding_orders = AsyncMock(return_value=True)
    owned.owner._refresh_orders = AsyncMock(side_effect=refresh)

    async def shutdown(**kwargs):
        await orchestrator.stop(keep_position=True, before_position_checkpoint=core.before_position_checkpoint)
        await core.after_order_cancellation()
        return True

    core.shutdown = AsyncMock(side_effect=shutdown)
    assert owned.loop.run_until_complete(owned.owner.shutdown_core(core, timeout=2))
    saved = owned.recorder.get_positions_by_controller("maker")
    assert len(saved) == 1
    assert saved[0].amount == Decimal(initial) + Decimal("0.001")
    assert saved[0].cum_fees_quote == Decimal("0.000001") * (2 if Decimal(initial) else 1)
    restored = ExecutorOrchestrator(strategy)
    assert restored.positions_held["maker"][0].net_amount_base == saved[0].amount
    assert restored.positions_held["maker"][0].cum_fees_quote == saved[0].cum_fees_quote
    recovery = owned.store.read()["recovery_state"]
    assert not recovery.get("halt") and not recovery.get("shutdown_incomplete")


def test_release_refuses_live_transport_task(owned):
    async def scenario():
        task = asyncio.create_task(asyncio.Event().wait())
        owned.owner.transport_tasks[task] = 1
        try:
            with pytest.raises(SubmissionBlocked, match="tasks"):
                owned.owner.release()
            assert owned.owner.connection is not None
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            owned.owner.transport_tasks.pop(task)
    owned.loop.run_until_complete(scenario())


def test_account_setup_can_run_before_strategy_gate_opens(owned):
    owned.owner.allow_new_orders = False
    owned.owner.reconciling = True
    rest, transport = assistant()
    owned.loop.run_until_complete(send(rest, sorted(ACCOUNT_PATHS)[0]))
    assert transport.call.await_count == 1
    assert not owned.owner.allow_new_orders


def test_shutdown_does_not_clear_runtime_marker_on_cancel_ack_without_terminal_status(owned):
    core = reconciliation_core(owned)
    order = intent(owned)
    order.current_state = OrderState.OPEN
    with owned.manager.get_new_session() as session, session.begin():
        session.get(Order, order.client_order_id).creation_pending = False
    owned.owner._refresh_orders = AsyncMock()

    async def shutdown(**kwargs):
        await core.after_order_cancellation()
        return True

    core.shutdown = AsyncMock(side_effect=shutdown)
    assert not owned.loop.run_until_complete(owned.owner.shutdown_core(core, timeout=0.02))
    owned.owner._refresh_orders.assert_awaited()
    recovery = owned.store.read()["recovery_state"]
    assert recovery["runtime_active"] and recovery["halt"]
