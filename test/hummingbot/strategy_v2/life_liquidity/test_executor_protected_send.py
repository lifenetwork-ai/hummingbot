"""P4.13/P4.14 contract from OrderExecutor through the protected OKX gateway."""

import asyncio
from dataclasses import replace
from decimal import Decimal
from test.hummingbot.strategy_v2.life_liquidity.test_protected_okx_send import PausedThrottler
from test.hummingbot.strategy_v2.life_liquidity.test_session import FakeClock, begin, manager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from controllers.generic.life_liquidity import LifeLiquidityConfig, LifeLiquidityController
from hummingbot.connector.exchange.okx.okx_exchange import OkxExchange
from hummingbot.connector.trading_rule import TradingRule
from hummingbot.core.data_type.common import TradeType
from hummingbot.core.web_assistant.connections.rest_connection import RESTConnection
from hummingbot.core.web_assistant.rest_assistant import RESTAssistant
from hummingbot.strategy.strategy_v2_base import StrategyV2Base
from hummingbot.strategy_v2.executors.order_executor.data_types import ExecutionStrategy, OrderExecutorConfig
from hummingbot.strategy_v2.executors.order_executor.order_executor import OrderExecutor
from hummingbot.strategy_v2.life_liquidity import risk
from hummingbot.strategy_v2.life_liquidity.config import QuotesConfig
from hummingbot.strategy_v2.life_liquidity.executor_send import ProtectedSpotExecutorSender
from hummingbot.strategy_v2.life_liquidity.order_gateway import OkxSpotOrderGateway
from hummingbot.strategy_v2.life_liquidity.protected_send import ProtectedSpotGateway
from hummingbot.strategy_v2.life_liquidity.risk import ReservationLedger, RiskLimits, SpotIntent
from hummingbot.strategy_v2.life_liquidity.state import IntentWAL


class Connector:
    client_order_id_prefix = "HBOT"
    client_order_id_max_length = 32

    def __init__(self):
        self.sent = []
        self.armed = []
        self.before_send = None

    def enable_protected_trading_pair(self, pair):
        self.armed.append(pair)

    def submit_protected_order(self, **kwargs):
        self.sent.append(kwargs)
        if self.before_send is not None:
            self.before_send(kwargs)
        return kwargs["order_id"]


def _setup(tmp_path, *, reserve=True, pre_reserve=False, connector=None,
           level_id="0", quote_levels=1, request_budget=None,
           recovery_account_uid=None, clock=None):
    clock = clock or FakeClock()
    active = manager(tmp_path, clock)
    begin(active)
    session = active.current_session
    wal = IntentWAL(tmp_path / "intents.json")
    limits = RiskLimits(Decimal("0"), Decimal("20"), Decimal("20"), Decimal("20"))
    ledger = ReservationLedger(life_balance=Decimal("10"),
                               usdt_balance=Decimal("10") if reserve else Decimal("0"),
                               limits=limits, path=tmp_path / "reservations.json")
    if pre_reserve:
        assert ledger.reserve(SpotIntent("executor-1", "BUY", Decimal("1"), Decimal("1"),
                                         session.session_id, session.epoch),
                              reference_price=Decimal("1")).allowed
    connector = connector or Connector()
    controller_config = LifeLiquidityConfig.model_construct(
        id="life", recovery_account_uid=recovery_account_uid)
    if quote_levels != 1:
        quotes = QuotesConfig(spreads_bps=tuple(Decimal(30 + level) for level in range(quote_levels)),
                              sizes_base=tuple(Decimal("1") for _ in range(quote_levels)))
        strategy_config = controller_config.strategy.model_copy(update={"quotes": quotes})
        controller_config = controller_config.model_copy(update={"strategy": strategy_config})
    controller = LifeLiquidityController(controller_config,
                                         MagicMock(), MagicMock())
    safety = OkxSpotOrderGateway(
        connector, wal, trading_pair="LIFE-USDT", clock=lambda: clock.wall,
        apply_fills=lambda *_: True, confirm_terminal=lambda *_: True,
        on_cancel_requested=lambda *_: None, on_unknown=lambda *_: None,
        account_check=lambda: True, request_budget=request_budget)
    controller.install_order_safety(active, safety, wal, reservations=ledger)
    allowed = {"value": True, "risk_epoch": 1}
    controller.allow_create_executor_actions = lambda: allowed["value"]
    gateway = ProtectedSpotGateway(
        connector, wal, authorize=lambda _: True, request_budget=request_budget)
    sender = ProtectedSpotExecutorSender(
        controller, gateway, ledger, risk_epoch=lambda: allowed["risk_epoch"],
        authorize=lambda permit: allowed["value"],
        reference_price=lambda: Decimal("1"))
    controller.install_protected_spot_sender(sender)
    strategy = MagicMock(spec=StrategyV2Base)
    strategy.controllers = {"life": controller}
    strategy.connectors = {"okx": connector}
    config = OrderExecutorConfig(
        id="executor-1", controller_id="life", side=TradeType.BUY,
        connector_name="okx", trading_pair="LIFE-USDT", amount=Decimal("1"),
        price=Decimal("1"), execution_strategy=ExecutionStrategy.LIMIT_MAKER,
        level_id=level_id)
    executor = OrderExecutor(strategy, config)
    executor.get_order_price = lambda: Decimal("1")
    return controller, executor, connector, wal, ledger, allowed


def _next_executor(previous, intent_id, *, level_id="0", side=TradeType.BUY):
    config = previous.config.model_copy(update={"id": intent_id, "level_id": level_id, "side": side})
    executor = OrderExecutor(previous._strategy, config)
    executor.get_order_price = lambda: Decimal("1")
    return executor


@pytest.mark.parametrize("level_id", [None, "", "00", "-1", "one"])
def test_executor_requires_canonical_quote_level_before_journal_or_send(tmp_path, level_id):
    _, executor, connector, wal, ledger, _ = _setup(tmp_path, level_id=level_id)
    with pytest.raises(ValueError, match="SLOT_LEVEL_INVALID"):
        executor.place_open_order()
    assert wal.all_records() == ()
    assert ledger.reservation_ids == frozenset()
    assert connector.sent == []


def test_executor_claims_durable_slot_before_reservation_and_blocks_duplicate(tmp_path):
    _, executor, connector, wal, ledger, _ = _setup(tmp_path)
    original_reserve = ledger.reserve

    def reserve(intent, *, reference_price):
        record = IntentWAL(wal.path).get(intent.intent_id)
        assert (record.slot_market, record.slot_side, record.slot_level) == (
            "LIFE-USDT", "BUY", 0)
        assert record.state == "PREPARED"
        return original_reserve(intent, reference_price=reference_price)

    ledger.reserve = reserve
    executor.place_open_order()
    duplicate = _next_executor(executor, "executor-2")
    with pytest.raises(ValueError, match="SLOT_OCCUPIED"):
        duplicate.place_open_order()
    assert len(connector.sent) == 1
    assert "executor-2" not in {record.intent_id for record in wal.all_records()}
    assert ledger.reservation_ids == frozenset({"executor-1"})


def test_uncertain_cashflow_checkpoint_blocks_before_wal_claim_or_network(tmp_path, monkeypatch):
    _, executor, connector, wal, ledger, _ = _setup(tmp_path)
    original_fsync = risk.os.fsync
    calls = 0

    def fail_directory_fsync(descriptor):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("crash after cashflow replacement")
        return original_fsync(descriptor)

    with monkeypatch.context() as patcher:
        patcher.setattr(risk.os, "fsync", fail_directory_fsync)
        with pytest.raises(OSError, match="crash after cashflow replacement"):
            ledger.record_cashflow("101", "USDT", Decimal("1"))

    with pytest.raises(ValueError, match="RISK_JOURNAL_UNCERTAIN"):
        executor.place_open_order()
    assert wal.all_records() == ()
    assert connector.sent == []
    assert ReservationLedger.restore(ledger.path, limits=ledger.limits).usdt_balance == Decimal("11")


def test_executor_rejects_level_outside_configured_quote_count(tmp_path):
    _, executor, connector, wal, ledger, _ = _setup(tmp_path, level_id="1")
    with pytest.raises(ValueError, match="SLOT_LEVEL_UNCONFIGURED"):
        executor.place_open_order()
    assert wal.all_records() == ()
    assert ledger.reservation_ids == frozenset()
    assert connector.sent == []


def test_executor_waits_for_terminal_reconciliation_before_replacement(tmp_path):
    _, executor, connector, wal, ledger, _ = _setup(tmp_path)
    executor.place_open_order()
    replacement = _next_executor(executor, "executor-2")
    wal.acknowledge("executor-1", "exchange-1")
    ledger.record_fill("executor-1", "trade-1", Decimal("0.4"), Decimal("1"))
    wal.mark_cancel_requested("executor-1")
    wal.mark_exchange_terminal_observed("executor-1", "exchange-1")
    with pytest.raises(ValueError, match="SLOT_OCCUPIED"):
        replacement.place_open_order()
    assert len(connector.sent) == 1
    ledger.confirm_terminal("executor-1", cumulative_filled=Decimal("0.4"),
                            fills_reconciled=True, exchange_state="CANCELED")
    wal.mark_terminal("executor-1", "exchange-1")
    _next_executor(executor, "executor-3").place_open_order()
    assert wal.get("executor-3").slot_level == 0
    assert len(connector.sent) == 2


def test_executor_blocks_replacement_when_wal_terminal_precedes_reservation(tmp_path):
    _, executor, connector, wal, ledger, _ = _setup(tmp_path)
    executor.place_open_order()
    wal.mark_terminal("executor-1", "exchange-1")
    replacement = _next_executor(executor, "executor-2")
    with pytest.raises(PermissionError, match="RECOVERY_JOURNALS_DISAGREE"):
        replacement.place_open_order()
    assert ledger.has_open_intent("executor-1")
    assert len(connector.sent) == 1


def test_executor_blocks_other_slots_when_reservation_terminal_precedes_wal(tmp_path):
    _, executor, connector, wal, ledger, _ = _setup(tmp_path, quote_levels=2)
    executor.place_open_order()
    ledger.confirm_terminal("executor-1", cumulative_filled=Decimal("0"),
                            fills_reconciled=True, exchange_state="CANCELED")
    other_level = _next_executor(executor, "executor-2", level_id="1")
    with pytest.raises(PermissionError, match="RECOVERY_JOURNALS_DISAGREE"):
        other_level.place_open_order()
    assert wal.get("executor-1").state == "SEND_UNKNOWN"
    assert len(connector.sent) == 1


def test_executor_can_claim_different_side_or_level(tmp_path):
    _, executor, connector, wal, ledger, _ = _setup(tmp_path, quote_levels=2)
    executor.place_open_order()
    _next_executor(executor, "executor-2", level_id="1").place_open_order()
    _next_executor(executor, "executor-3", side=TradeType.SELL).place_open_order()
    assert len(connector.sent) == 3
    assert {(item.slot_side, item.slot_level) for item in wal.all_records()} == {
        ("BUY", 0), ("BUY", 1), ("SELL", 0)}


def test_final_send_gate_rechecks_persisted_slot_identity(tmp_path):
    _, executor, connector, wal, _, _ = _setup(tmp_path)
    executor.place_open_order()
    wal._commit(replace(wal.get("executor-1"), slot_level=1))
    with pytest.raises(PermissionError, match="SEND_PERMISSION_REVOKED"):
        connector.sent[0]["pre_send_check"]({
            "clOrdId": executor._order.order_id, "instId": "LIFE-USDT",
            "side": "buy", "ordType": "post_only", "tdMode": "cash",
            "px": "1", "sz": "1"})


def test_executor_send_uses_preallocated_wal_id_before_connector_enqueue(tmp_path):
    controller, executor, connector, wal, ledger, _ = _setup(tmp_path)

    def before_send(kwargs):
        record = IntentWAL(wal.path).get("executor-1")
        assert record.client_order_id == kwargs["order_id"]
        assert record.reservation_id == "executor-1"
        assert record.state == "SEND_UNKNOWN"
        assert ledger.has_open_intent("executor-1")

    connector.before_send = before_send
    executor.place_open_order()

    assert executor._order.order_id == connector.sent[0]["order_id"]
    assert connector.armed == ["LIFE-USDT"]
    executor._strategy.buy.assert_not_called()
    executor._strategy.sell.assert_not_called()
    assert not controller.trading_permissions_ready()


def test_wal_identity_is_durable_before_risk_reservation(tmp_path):
    _, executor, connector, wal, ledger, _ = _setup(tmp_path)
    original = ledger.reserve

    def reserve(intent, *, reference_price):
        record = IntentWAL(wal.path).get(intent.intent_id)
        assert record.state == "PREPARED"
        assert record.client_order_id
        assert record.session_id == intent.session_id
        return original(intent, reference_price=reference_price)

    ledger.reserve = reserve
    executor.place_open_order()
    assert len(connector.sent) == 1


def test_crash_before_reservation_keeps_prepared_identity_and_no_send(tmp_path):
    _, executor, connector, wal, ledger, _ = _setup(tmp_path)
    ledger.reserve = lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("crash"))

    with pytest.raises(OSError, match="crash"):
        executor.place_open_order()
    assert connector.sent == []
    assert IntentWAL(wal.path).get("executor-1").state == "PREPARED"
    assert ledger.reservation_ids == frozenset()


def test_executor_retry_after_lost_ack_cannot_duplicate_order(tmp_path):
    _, executor, connector, wal, ledger, _ = _setup(tmp_path)
    executor.place_open_order()
    executor._order = None  # failure event/ACK loss may clear the in-memory tracker

    with pytest.raises(ValueError, match="RECONCILE_BEFORE_RETRY"):
        executor.place_open_order()

    assert len(connector.sent) == 1
    assert wal.get("executor-1").state == "SEND_UNKNOWN"
    assert ledger.has_open_intent("executor-1")


def test_refused_reservation_leaves_aborted_wal_identity_and_no_connector_send(tmp_path):
    _, executor, connector, wal, _, _ = _setup(tmp_path, reserve=False)

    with pytest.raises(PermissionError, match="RESERVATION_UNAVAILABLE"):
        executor.place_open_order()

    assert wal.get("executor-1").state == "ABORTED_BEFORE_SEND"
    assert connector.sent == []
    executor._strategy.buy.assert_not_called()


def test_aborted_pre_send_does_not_block_distinct_intent_after_capacity_returns(tmp_path):
    _, executor, connector, wal, ledger, _ = _setup(tmp_path, reserve=False)
    with pytest.raises(PermissionError, match="RESERVATION_UNAVAILABLE"):
        executor.place_open_order()
    ledger.record_cashflow("123", "USDT", Decimal("2"))
    next_config = executor.config.model_copy(update={"id": "executor-2"})
    next_executor = OrderExecutor(executor._strategy, next_config)
    next_executor.get_order_price = lambda: Decimal("1")
    next_executor.place_open_order()
    assert wal.get("executor-1").state == "ABORTED_BEFORE_SEND"
    assert wal.get("executor-2").state == "SEND_UNKNOWN"
    assert len(connector.sent) == 1


def test_wal_arm_failure_before_commit_releases_proven_unsent_reservation(tmp_path):
    _, executor, connector, wal, ledger, _ = _setup(tmp_path)
    wal.arm_send = lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("crash"))
    with pytest.raises(OSError, match="crash"):
        executor.place_open_order()
    assert IntentWAL(wal.path).get("executor-1").state == "ABORTED_BEFORE_SEND"
    assert ledger.is_terminal_intent("executor-1")
    assert connector.sent == []
    with pytest.raises(ValueError, match="RECONCILE_BEFORE_RETRY"):
        executor.place_open_order()


def test_revoked_before_wal_arm_releases_proven_unsent_reservation(tmp_path):
    controller, executor, connector, wal, ledger, _ = _setup(tmp_path)
    controller._protected_spot_sender.policy_authorize = lambda _permit: False

    with pytest.raises(PermissionError, match="SEND_PERMISSION_REVOKED"):
        executor.place_open_order()

    assert wal.get("executor-1").state == "ABORTED_BEFORE_SEND"
    assert ledger.is_terminal_intent("executor-1")
    assert ledger.reserved_usdt == Decimal("0")
    assert connector.sent == []


def test_wal_arm_disk_commit_with_lost_fsync_keeps_reservation(tmp_path):
    _, executor, connector, wal, ledger, _ = _setup(tmp_path)
    original_save = wal._save

    def save_then_fail(records):
        original_save(records)
        if records["executor-1"].state == "SEND_UNKNOWN":
            raise OSError("directory fsync lost")

    wal._save = save_then_fail
    with pytest.raises(OSError, match="directory fsync lost"):
        executor.place_open_order()

    assert IntentWAL(wal.path).get("executor-1").state == "SEND_UNKNOWN"
    assert ledger.has_open_intent("executor-1")
    assert connector.sent == []


def test_wire_id_allocation_failure_leaves_no_journal_or_reservation(tmp_path):
    controller, executor, connector, wal, ledger, _ = _setup(tmp_path)
    controller._protected_spot_sender.gateway.allocate_client_order_id = (
        lambda **_kwargs: (_ for _ in ()).throw(ValueError("allocation failed")))
    with pytest.raises(ValueError, match="allocation failed"):
        executor.place_open_order()
    assert wal.all_records() == ()
    assert ledger.reservation_ids == frozenset()
    assert connector.sent == []


def test_in_memory_reservation_ledger_cannot_reach_executor_send(tmp_path):
    _, executor, connector, wal, ledger, _ = _setup(tmp_path)
    ledger.path = None
    with pytest.raises(PermissionError, match="RESERVATION_JOURNAL_NOT_DURABLE"):
        executor.place_open_order()
    assert wal.all_records() == ()
    assert connector.sent == []


def test_ambiguous_connector_enqueue_keeps_reservation_for_reconciliation(tmp_path):
    _, executor, connector, wal, ledger, _ = _setup(tmp_path)
    connector.submit_protected_order = (
        lambda **_kwargs: (_ for _ in ()).throw(TimeoutError("unknown enqueue")))
    with pytest.raises(TimeoutError, match="unknown enqueue"):
        executor.place_open_order()
    assert wal.get("executor-1").state == "SEND_UNKNOWN"
    assert ledger.has_open_intent("executor-1")
    with pytest.raises(ValueError, match="RECONCILE_BEFORE_RETRY"):
        executor.place_open_order()


def test_wal_write_failure_never_enqueues_and_retry_stays_blocked(tmp_path):
    _, executor, connector, wal, ledger, _ = _setup(tmp_path)
    wal._save = lambda _: (_ for _ in ()).throw(OSError("disk failure"))

    with pytest.raises(OSError, match="disk failure"):
        executor.place_open_order()
    assert connector.sent == []
    assert not ledger.has_open_intent("executor-1")
    with pytest.raises(ValueError, match="RECONCILE_BEFORE_RETRY"):
        executor.place_open_order()


def test_unmatched_reservation_journal_blocks_send(tmp_path):
    controller, executor, connector, wal, ledger, _ = _setup(tmp_path)
    current = controller._order_safety_manager.current_session
    ledger.reserve(SpotIntent("orphan", "BUY", Decimal("1"), Decimal("1"),
                              current.session_id, current.epoch),
                   reference_price=Decimal("1"))

    with pytest.raises(PermissionError, match="RECOVERY_JOURNALS_DISAGREE"):
        executor.place_open_order()
    assert connector.sent == []
    assert wal.all_records() == ()


def test_reservation_with_existing_fill_cannot_authorize_new_full_size_send(tmp_path):
    _, executor, connector, wal, ledger, _ = _setup(tmp_path, pre_reserve=True)
    ledger.record_fill("executor-1", "trade-1", Decimal("0.25"), Decimal("1"))

    with pytest.raises(PermissionError, match="RESERVATION_UNAVAILABLE"):
        executor.place_open_order()

    assert connector.sent == []
    assert wal.all_records() == ()


def test_revocation_before_executor_send_and_after_queue_blocks(tmp_path):
    controller, executor, connector, wal, _, allowed = _setup(tmp_path)
    allowed["value"] = False
    with pytest.raises(PermissionError, match="LIFE_TRADING_DISABLED"):
        executor.place_open_order()
    assert wal.all_records() == ()

    allowed["value"] = True
    executor.place_open_order()
    check = connector.sent[0]["pre_send_check"]
    allowed["value"] = False
    with pytest.raises(PermissionError, match="SEND_PERMISSION_REVOKED"):
        check({"clOrdId": executor._order.order_id, "instId": "LIFE-USDT",
               "side": "buy", "ordType": "post_only", "tdMode": "cash",
               "px": "1", "sz": "1"})
    assert wal.get("executor-1").state == "SEND_UNKNOWN"
    assert not controller.trading_permissions_ready()


def test_risk_epoch_change_blocks_final_send(tmp_path):
    _, executor, connector, wal, _, allowed = _setup(tmp_path)
    executor.place_open_order()
    allowed["risk_epoch"] = 2
    with pytest.raises(PermissionError, match="SEND_PERMISSION_REVOKED"):
        connector.sent[0]["pre_send_check"]({
            "clOrdId": executor._order.order_id, "instId": "LIFE-USDT",
            "side": "buy", "ordType": "post_only", "tdMode": "cash",
            "px": "1", "sz": "1"})
    assert wal.get("executor-1").state == "SEND_UNKNOWN"


def test_cancel_intent_before_final_send_blocks_network_request(tmp_path):
    _, executor, connector, wal, _, _ = _setup(tmp_path)
    executor.place_open_order()
    wal.mark_cancel_requested("executor-1")
    with pytest.raises(PermissionError, match="INTENT_WAL_UNAVAILABLE"):
        connector.sent[0]["pre_send_check"]({
            "clOrdId": executor._order.order_id, "instId": "LIFE-USDT",
            "side": "buy", "ordType": "post_only", "tdMode": "cash",
            "px": "1", "sz": "1"})


def test_session_pause_after_executor_enqueue_revokes_final_send(tmp_path):
    controller, executor, connector, wal, _, allowed = _setup(tmp_path)
    executor.place_open_order()
    assert allowed["value"]
    controller._order_safety_manager.tick(reference_ready=False, all_gates_ready=False)
    with pytest.raises(PermissionError, match="SEND_PERMISSION_REVOKED"):
        connector.sent[0]["pre_send_check"]({
            "clOrdId": executor._order.order_id, "instId": "LIFE-USDT",
            "side": "buy", "ordType": "post_only", "tdMode": "cash",
            "px": "1", "sz": "1"})
    assert wal.get("executor-1").state == "SEND_UNKNOWN"


def test_life_executor_without_installed_sender_never_uses_generic_buy():
    controller = LifeLiquidityController(LifeLiquidityConfig.model_construct(id="life"),
                                         MagicMock(), MagicMock())
    strategy = MagicMock(spec=StrategyV2Base)
    strategy.controllers = {"life": controller}
    strategy.connectors = {"okx": Connector()}
    config = OrderExecutorConfig(
        id="executor-1", controller_id="life", side=TradeType.BUY,
        connector_name="okx", trading_pair="LIFE-USDT", amount=Decimal("1"),
        price=Decimal("1"), execution_strategy=ExecutionStrategy.LIMIT_MAKER)
    executor = OrderExecutor(strategy, config)
    executor.get_order_price = lambda: Decimal("1")
    with pytest.raises(PermissionError, match="LIFE_TRADING_DISABLED"):
        executor.place_open_order()
    strategy.buy.assert_not_called()


@pytest.mark.asyncio
async def test_actual_strategy_instance_dispatches_executor_through_protected_sender(tmp_path):
    controller, prepared, connector, wal, _, _ = _setup(tmp_path)
    with patch("hummingbot.strategy.strategy_v2_base._get_executor_orchestrator_class",
               return_value=lambda **kwargs: MagicMock()):
        runner = StrategyV2Base({}, config=None)
    runner.controllers = {"life": controller}
    runner.connectors = {"okx": connector}
    runner.buy = MagicMock()
    runner.sell = MagicMock()
    executor = OrderExecutor(runner, prepared.config)
    executor.get_order_price = lambda: Decimal("1")
    try:
        executor.place_open_order()
        assert executor._order.order_id == wal.get("executor-1").client_order_id
        assert len(connector.sent) == 1
        runner.buy.assert_not_called()
        runner.sell.assert_not_called()
    finally:
        runner.listen_to_executor_actions_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await runner.listen_to_executor_actions_task


@pytest.mark.asyncio
async def test_executor_recheck_after_okx_throttler_blocks_revoked_request(tmp_path):
    sent = []
    throttler = PausedThrottler()

    class Session:
        async def request(self, **kwargs):
            sent.append(kwargs)
            raise AssertionError("revoked order reached network")

    connector = OkxExchange("key", "secret", "passphrase", trading_pairs=[], trading_required=False)
    connector._trading_rules["LIFE-USDT"] = TradingRule(
        trading_pair="LIFE-USDT", min_order_size=Decimal("1"),
        min_price_increment=Decimal("0.01"), min_base_amount_increment=Decimal("1"))
    connector._on_order_failure = Mock()
    connector._web_assistants_factory = SimpleNamespace(get_rest_assistant=AsyncMock(
        return_value=RESTAssistant(connection=RESTConnection(Session()), throttler=throttler)))
    connector._api_request_url = AsyncMock(return_value="https://www.okx.com/api/v5/trade/order")
    connector.exchange_symbol_associated_to_pair = AsyncMock(return_value="LIFE-USDT")
    _, executor, _, wal, _, allowed = _setup(tmp_path, connector=connector)

    executor.place_open_order()
    await asyncio.wait_for(throttler.entered.wait(), timeout=2)
    allowed["value"] = False
    throttler.release.set()
    for _ in range(50):
        if connector._on_order_failure.called:
            break
        await asyncio.sleep(0.01)

    assert sent == []
    assert wal.get("executor-1").state == "SEND_UNKNOWN"
    executor._strategy.buy.assert_not_called()


@pytest.mark.asyncio
async def test_executor_send_reaches_okx_order_tracker_and_wal_ack(tmp_path):
    sent = []
    throttler = PausedThrottler()
    throttler.release.set()

    class Response:
        status = 200
        content_type = "application/json"

        async def json(self):
            return {"data": [{"sCode": "0", "ordId": "exchange-1"}]}

    class Session:
        async def request(self, **kwargs):
            sent.append(kwargs)
            return Response()

    connector = OkxExchange("key", "secret", "passphrase", trading_pairs=[], trading_required=False)
    connector._trading_rules["LIFE-USDT"] = TradingRule(
        trading_pair="LIFE-USDT", min_order_size=Decimal("1"),
        min_price_increment=Decimal("0.01"), min_base_amount_increment=Decimal("1"))
    connector._on_order_failure = Mock()
    connector._web_assistants_factory = SimpleNamespace(get_rest_assistant=AsyncMock(
        return_value=RESTAssistant(connection=RESTConnection(Session()), throttler=throttler)))
    connector._api_request_url = AsyncMock(return_value="https://www.okx.com/api/v5/trade/order")
    connector.exchange_symbol_associated_to_pair = AsyncMock(return_value="LIFE-USDT")
    _, executor, _, wal, _, _ = _setup(tmp_path, connector=connector)

    executor.place_open_order()
    for _ in range(50):
        if wal.get("executor-1").state == "ACKED":
            break
        await asyncio.sleep(0.01)

    assert wal.get("executor-1").state == "ACKED"
    assert executor._order.order_id == wal.get("executor-1").client_order_id
    assert connector._order_tracker.fetch_order(executor._order.order_id).exchange_order_id == "exchange-1"
    assert len(sent) == 1
    assert '"ordType": "post_only"' in sent[0]["data"]
    connector._on_order_failure.assert_not_called()
