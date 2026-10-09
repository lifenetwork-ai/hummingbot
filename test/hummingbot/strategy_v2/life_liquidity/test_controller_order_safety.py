"""LIFE cancellation must run through the real runner safety callback."""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from test.hummingbot.strategy_v2.life_liquidity.test_order_gateway import FakeOkx
from test.hummingbot.strategy_v2.life_liquidity.test_reference_transition import manager, start
from test.hummingbot.strategy_v2.life_liquidity.test_session import FakeClock
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from controllers.generic.life_liquidity import LifeLiquidityConfig, LifeLiquidityController
from hummingbot.strategy.strategy_v2_base import StrategyV2Base
from hummingbot.strategy_v2.life_liquidity import account_lock
from hummingbot.strategy_v2.life_liquidity.config import RiskConfig
from hummingbot.strategy_v2.life_liquidity.order_gateway import (
    OkxSpotOrderGateway,
    SpotAccountReconciler,
    SpotReservationReconciler,
)
from hummingbot.strategy_v2.life_liquidity.risk import ReservationLedger, RiskLimits, SpotIntent
from hummingbot.strategy_v2.life_liquidity.state import IntentWAL


def _controller():
    return LifeLiquidityController(LifeLiquidityConfig.model_construct(id="life"),
                                   MagicMock(), MagicMock())


@pytest.fixture(autouse=True)
def isolated_account_lock_root(tmp_path, monkeypatch):
    monkeypatch.setattr(account_lock, "ACCOUNT_LOCK_ROOT", tmp_path / "account_locks")


def _limits():
    return RiskLimits(min_inventory_base=Decimal("0"),
                      max_inventory_base=Decimal("20"),
                      max_gross_quote=Decimal("20"),
                      max_net_base=Decimal("20"))


def _recovery_config(recovery_dir):
    template = LifeLiquidityConfig(id="life").strategy
    risk = RiskConfig(min_inventory_base=Decimal("0"),
                      max_inventory_base=Decimal("20"),
                      max_gross_quote=Decimal("20"), max_net_base=Decimal("20"))
    return LifeLiquidityConfig.model_construct(
        id="life", recovery_state_dir=str(recovery_dir), recovery_account_uid="12345",
        recovery_reconciliation_max_age_ms=1000,
        strategy=template.model_copy(update={"risk": risk}))


def _reservation(path, session_id, epoch):
    ledger = ReservationLedger(life_balance=Decimal("10"), usdt_balance=Decimal("10"),
                               limits=_limits(), path=path)
    assert ledger.reserve(SpotIntent("i1", "BUY", Decimal("1"), Decimal("1"),
                                     session_id, epoch),
                          reference_price=Decimal("1")).allowed
    return ledger


def _cashflows(directory, *, approved=()):
    (directory / "cashflows.json").write_text(json.dumps({
        "schema_version": 1, "anchor_bill_id": "100", "approved": list(approved)}))


def _install(controller, tmp_path, clock, connector, *, restored=False):
    active = manager(tmp_path, clock)
    primary = active.current_session if restored else start(active)
    wal = IntentWAL(tmp_path / "intents.json")
    if not restored:
        wal.prepare("i1", client_order_id="wire-1", session_id=primary.session_id,
                    epoch=primary.epoch, reservation_id="i1")
        reservations = _reservation(tmp_path / "reservations.json", primary.session_id,
                                    primary.epoch)
    else:
        reservations = ReservationLedger.restore(tmp_path / "reservations.json", limits=_limits())
    reconciler = SpotReservationReconciler(wal, reservations)
    gateway = OkxSpotOrderGateway(
        connector, wal, trading_pair="LIFE-USDT", clock=lambda: clock.wall,
        apply_fills=reconciler.apply_fills,
        confirm_terminal=reconciler.confirm_terminal,
        on_cancel_requested=reconciler.request_cancel,
        on_unknown=reconciler.mark_unknown,
        account_check=SpotAccountReconciler(connector, reservations).check)
    controller.install_order_safety(active, gateway, wal)
    return active, wal, reservations


@pytest.mark.asyncio
async def test_expiry_cancels_and_reconciles_partial_fill_without_market_readiness(tmp_path):
    clock = FakeClock()
    connector = FakeOkx()
    connector.status["wire-1"] = {"clOrdId": "wire-1", "ordId": "exchange-1",
                                  "state": "partially_filled", "accFillSz": "0.4"}
    connector.fills["exchange-1"] = [{"tradeId": "trade-1", "ordId": "exchange-1",
                                      "fillSz": "0.4", "fillPx": "1"}]
    connector.cash_balances = {"LIFE": "10.4", "USDT": "9.6"}
    connector.open_pages[None] = [{"clOrdId": "wire-1", "ordId": "exchange-1",
                                   "instId": "LIFE-USDT", "state": "partially_filled"}]
    controller = _controller()
    active, wal, reservations = _install(controller, tmp_path, clock, connector)
    clock.advance(10)
    with patch("hummingbot.strategy.strategy_v2_base._get_executor_orchestrator_class",
               return_value=lambda **kwargs: MagicMock()):
        runner = StrategyV2Base({}, config=None)
    runner.executor_orchestrator.active_executors = {"life": []}
    runner.executor_orchestrator.get_stored_executors_by_controller.return_value = ()
    runner.controllers = {"life": controller}
    runner.connectors = {"okx": SimpleNamespace(ready=False, name="okx")}
    runner.market_data_provider = SimpleNamespace(ready=False)
    try:
        runner.tick(10)
        await controller.order_safety_task
        assert active.state == "TRANSITIONING"
        assert connector.cancels == [("LIFE-USDT", "wire-1")]
        assert controller.order_safety_reason_code == "OLD_ORDERS_UNRESOLVED"
        assert reservations.requires_reconciliation("i1")
        assert wal.get("i1").state != "TERMINAL"
        assert not controller.allow_create_executor_actions()

        connector.status["wire-1"]["state"] = "canceled"
        connector.open_pages[None] = []
        runner.tick(11)
        await controller.order_safety_task
        assert wal.get("i1").state == "TERMINAL"
        assert not reservations.requires_reconciliation("i1")
        assert reservations.life_balance == Decimal("10.4")
        assert active.state == "TRANSITIONING"  # no qualified successor reference
        runner.executor_orchestrator.execute_action.assert_not_called()
    finally:
        runner.listen_to_executor_actions_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await runner.listen_to_executor_actions_task


@pytest.mark.asyncio
async def test_failed_cancel_and_restart_keep_reservation_until_terminal_proof(tmp_path):
    clock = FakeClock()
    connector = FakeOkx()
    connector.fail_status = True
    controller = _controller()
    active, wal, reservations = _install(controller, tmp_path, clock, connector)
    clock.advance(10)
    controller.on_safety_tick(10)
    await controller.order_safety_task
    assert controller.order_safety_reason_code == "RECONCILIATION_INCOMPLETE"
    assert reservations.requires_reconciliation("i1")

    restarted = _controller()
    active, wal, reservations = _install(restarted, tmp_path, clock, connector, restored=True)
    assert active.state == "TRANSITIONING"
    connector.fail_status = False
    connector.status["wire-1"] = {"clOrdId": "wire-1", "ordId": "exchange-1",
                                  "state": "canceled", "accFillSz": "0"}
    restarted.on_safety_tick(11)
    await restarted.order_safety_task
    assert wal.get("i1").state == "TERMINAL"
    assert not reservations.requires_reconciliation("i1")
    assert active.state == "TRANSITIONING"


@pytest.mark.asyncio
async def test_cancel_ack_failure_keeps_order_pending_and_retries_without_successor(tmp_path):
    clock = FakeClock()
    connector = FakeOkx()
    connector.status["wire-1"] = {"clOrdId": "wire-1", "ordId": "exchange-1",
                                  "state": "live", "accFillSz": "0"}
    connector.open_pages[None] = [{"clOrdId": "wire-1", "ordId": "exchange-1",
                                   "instId": "LIFE-USDT", "state": "live"}]

    async def no_ack(pair, wire_id):
        connector.cancels.append((pair, wire_id))
        return False

    connector.cancel_by_client_id = no_ack
    controller = _controller()
    active, wal, reservations = _install(controller, tmp_path, clock, connector)
    clock.advance(10)
    controller.on_safety_tick(10)
    await controller.order_safety_task
    assert controller.order_safety_reason_code == "CANCEL_REQUEST_FAILED"
    assert wal.get("i1").cancel_requested
    assert reservations.requires_reconciliation("i1")
    assert active.state == "TRANSITIONING"
    controller.on_safety_tick(11)
    await controller.order_safety_task
    assert connector.cancels == [("LIFE-USDT", "wire-1"), ("LIFE-USDT", "exchange-1")]
    assert wal.get("i1").state != "TERMINAL"


@pytest.mark.asyncio
async def test_safety_tick_does_not_start_overlapping_cancel_cycles(tmp_path):
    clock = FakeClock()
    connector = FakeOkx()
    waiting = asyncio.Event()
    release = asyncio.Event()

    async def delayed_cancel(pair, wire_id):
        connector.cancels.append((pair, wire_id))
        waiting.set()
        await release.wait()
        return True

    connector.cancel_by_client_id = delayed_cancel
    connector.status["wire-1"] = {"clOrdId": "wire-1", "ordId": "exchange-1",
                                  "state": "canceled", "accFillSz": "0"}
    controller = _controller()
    _install(controller, tmp_path, clock, connector)
    clock.advance(10)
    controller.on_safety_tick(10)
    first = controller.order_safety_task
    await waiting.wait()
    controller.on_safety_tick(11)
    assert controller.order_safety_task is first
    assert connector.cancels == [("LIFE-USDT", "wire-1")]
    release.set()
    await first


@pytest.mark.asyncio
@pytest.mark.parametrize("filled, historical, fee", [
    ("0", False, "0"), ("0.4", False, "0"), ("0.4", True, "0"),
    ("0.4", False, "-0.01"),
])
async def test_controller_restores_journals_automatically_and_checks_account(
        tmp_path, filled, historical, fee):
    clock = FakeClock()
    clock.wall = datetime.now(timezone.utc) - timedelta(seconds=20)
    recovery_dir = tmp_path / "recovery"
    recovery_dir.mkdir()
    active = manager(recovery_dir, clock)
    primary = start(active)
    (recovery_dir / "transition.json").replace(recovery_dir / "session.json")
    wal = IntentWAL(recovery_dir / "intents.json")
    wal.prepare("i1", client_order_id="wire-1", session_id=primary.session_id,
                epoch=primary.epoch, reservation_id="i1")
    _reservation(recovery_dir / "reservations.json", primary.session_id, primary.epoch)
    _cashflows(recovery_dir)
    connector = FakeOkx()
    connector.status["wire-1"] = {"clOrdId": "wire-1", "ordId": "exchange-1",
                                  "state": "canceled", "accFillSz": filled}
    if filled != "0":
        fill = {"tradeId": "trade-1", "ordId": "exchange-1",
                "fillSz": filled, "fillPx": "1", "fee": fee,
                "feeCcy": "LIFE" if fee != "0" else "USDT"}
        if historical:
            connector.fill_history_pages[None] = [{**fill, "billId": "bill-1",
                                                   "instId": "LIFE-USDT"}]
        else:
            connector.fills["exchange-1"] = [fill]
        connector.bill_pages[None] = [
            {"billId": "101", "type": "2", "subType": "1", "ccy": "LIFE",
             "tradeId": "trade-1", "ordId": "exchange-1", "instId": "LIFE-USDT",
             "balChg": str(Decimal(filled) + Decimal(fee)), "fee": fee},
            {"billId": "100"}]
        connector.all_fill_history_pages[None] = [
            {**fill, "billId": "101", "instType": "SPOT", "instId": "LIFE-USDT",
             "clOrdId": "wire-1"}]
    else:
        connector.bill_pages[None] = [{"billId": "100"}]
    connector.cash_balances = {
        "LIFE": str(Decimal("10") + Decimal(filled) + Decimal(fee)),
        "USDT": str(Decimal("10") - Decimal(filled))}
    provider = MagicMock()
    provider.get_connector_with_fallback.return_value = connector
    config = _recovery_config(recovery_dir)
    controller = LifeLiquidityController(config, provider, MagicMock())
    controller.on_safety_tick(10)
    await controller.order_safety_task
    assert controller.order_safety_reason_code == "MARKET_REFERENCE_UNAVAILABLE"
    assert IntentWAL(recovery_dir / "intents.json").get("i1").state == "TERMINAL"
    assert ReservationLedger.restore(recovery_dir / "reservations.json", limits=_limits()).life_balance == (
        Decimal("10") + Decimal(filled) + Decimal(fee))
    assert not controller.allow_create_executor_actions()


@pytest.mark.asyncio
async def test_recovery_applies_approved_cashflow_before_terminal_release(tmp_path):
    recovery_dir = tmp_path / "recovery"
    _seed_recovery(recovery_dir)
    _cashflows(recovery_dir, approved=[
        {"bill_id": "102", "currency": "USDT", "amount": "2"}])
    connector = FakeOkx()
    connector.status["wire-1"] = {"clOrdId": "wire-1", "ordId": "exchange-1",
                                  "state": "canceled", "accFillSz": "0"}
    connector.bill_pages[None] = [
        {"billId": "102", "type": "1", "subType": "11", "ccy": "USDT",
         "balChg": "2", "tradeId": "", "ordId": "", "from": "6", "to": "18"},
        {"billId": "100"}]
    connector.cash_balances = {"LIFE": "10", "USDT": "12"}
    provider = MagicMock()
    provider.get_connector_with_fallback.return_value = connector
    controller = LifeLiquidityController(_recovery_config(recovery_dir), provider, MagicMock())
    try:
        controller.on_safety_tick(10)
        await controller.order_safety_task
        assert controller.order_safety_reason_code == "MARKET_REFERENCE_UNAVAILABLE"
        assert IntentWAL(recovery_dir / "intents.json").get("i1").state == "TERMINAL"
        restored = ReservationLedger.restore(recovery_dir / "reservations.json", limits=_limits())
        assert restored.usdt_balance == Decimal("12")
        controller.on_safety_tick(11)
        await controller.order_safety_task
        assert ReservationLedger.restore(recovery_dir / "reservations.json",
                                         limits=_limits()).usdt_balance == Decimal("12")
    finally:
        controller.stop()


@pytest.mark.asyncio
async def test_missing_bill_anchor_keeps_terminal_order_unresolved(tmp_path):
    recovery_dir = tmp_path / "recovery"
    _seed_recovery(recovery_dir)
    connector = FakeOkx()
    connector.status["wire-1"] = {"clOrdId": "wire-1", "ordId": "exchange-1",
                                  "state": "canceled", "accFillSz": "0"}
    provider = MagicMock()
    provider.get_connector_with_fallback.return_value = connector
    controller = LifeLiquidityController(_recovery_config(recovery_dir), provider, MagicMock())
    try:
        controller.on_safety_tick(10)
        await controller.order_safety_task
        assert controller.order_safety_reason_code == "RECONCILIATION_INCOMPLETE"
        assert IntentWAL(recovery_dir / "intents.json").get("i1").state != "TERMINAL"
    finally:
        controller.stop()


@pytest.mark.asyncio
async def test_other_pair_manual_order_blocks_recovery_without_canceling_it(tmp_path):
    recovery_dir = tmp_path / "recovery"
    _seed_recovery(recovery_dir)
    connector = FakeOkx()
    connector.status["wire-1"] = {"clOrdId": "wire-1", "ordId": "exchange-1",
                                  "state": "canceled", "accFillSz": "0"}
    connector.bill_pages[None] = [{"billId": "100"}]
    connector.open_pages[None] = [{"clOrdId": "manual-btc", "ordId": "exchange-btc",
                                   "instId": "BTC-USDT", "state": "live"}]
    provider = MagicMock()
    provider.get_connector_with_fallback.return_value = connector
    controller = LifeLiquidityController(_recovery_config(recovery_dir), provider, MagicMock())
    try:
        controller.on_safety_tick(10)
        await controller.order_safety_task
        assert controller.order_safety_reason_code == "RECONCILIATION_INCOMPLETE"
        assert IntentWAL(recovery_dir / "intents.json").get("i1").state != "TERMINAL"
        assert ReservationLedger.restore(recovery_dir / "reservations.json",
                                         limits=_limits()).reserved_usdt == Decimal("1")
        assert connector.cancels == [("LIFE-USDT", "wire-1")]
        connector.open_pages[None] = []
        controller.on_safety_tick(11)
        await controller.order_safety_task
        assert IntentWAL(recovery_dir / "intents.json").get("i1").state == "TERMINAL"
    finally:
        controller.stop()


def test_missing_cashflow_approval_file_blocks_auto_recovery(tmp_path):
    recovery_dir = tmp_path / "recovery"
    _seed_recovery(recovery_dir)
    (recovery_dir / "cashflows.json").unlink()
    connector = FakeOkx()
    provider = MagicMock()
    provider.get_connector_with_fallback.return_value = connector
    controller = LifeLiquidityController(_recovery_config(recovery_dir), provider, MagicMock())
    controller.on_safety_tick(10)
    assert controller.order_safety_reason_code == "ORDER_SAFETY_RECOVERY_FAILED"
    assert controller.order_safety_task is None
    assert connector.cancels == []


def test_missing_or_corrupt_recovery_journals_fail_closed(tmp_path):
    recovery_dir = tmp_path / "recovery"
    recovery_dir.mkdir()
    provider = MagicMock()
    provider.get_connector_with_fallback.return_value = FakeOkx()
    config = _recovery_config(recovery_dir)
    controller = LifeLiquidityController(config, provider, MagicMock())
    controller.on_safety_tick(0)
    assert controller.order_safety_reason_code == "ORDER_SAFETY_RECOVERY_FAILED"
    assert controller.order_safety_task is None
    assert not controller.allow_create_executor_actions()
    (recovery_dir / "session.json").write_text("{broken")
    controller.on_safety_tick(1)
    assert controller.order_safety_reason_code == "ORDER_SAFETY_RECOVERY_FAILED"


def test_wal_and_reservation_journal_mismatch_blocks_auto_recovery(tmp_path):
    clock = FakeClock()
    clock.wall = datetime.now(timezone.utc) - timedelta(seconds=20)
    recovery_dir = tmp_path / "recovery"
    recovery_dir.mkdir()
    primary = start(manager(recovery_dir, clock))
    (recovery_dir / "transition.json").replace(recovery_dir / "session.json")
    wal = IntentWAL(recovery_dir / "intents.json")
    wal.prepare("i1", client_order_id="wire-1", session_id=primary.session_id,
                epoch=primary.epoch, reservation_id="i1")
    ReservationLedger(life_balance=Decimal("10"), usdt_balance=Decimal("10"),
                      limits=_limits(), path=recovery_dir / "reservations.json")
    _cashflows(recovery_dir)
    connector = FakeOkx()
    provider = MagicMock()
    provider.get_connector_with_fallback.return_value = connector
    controller = LifeLiquidityController(_recovery_config(recovery_dir), provider, MagicMock())
    controller.on_safety_tick(10)
    assert controller.order_safety_reason_code == "ORDER_SAFETY_RECOVERY_FAILED"
    assert controller.order_safety_task is None
    assert connector.cancels == []


@pytest.mark.parametrize("with_reservation,already_aborted", [
    (False, False), (True, False), (True, True),
])
def test_pre_send_crash_recovers_without_exchange_request(
        tmp_path, with_reservation, already_aborted):
    recovery_dir = tmp_path / "recovery"
    _seed_recovery(recovery_dir)
    wal = IntentWAL(recovery_dir / "intents.json")
    old = wal.get("i1")
    wal.begin("i2", client_order_id="wire-2", session_id=old.session_id,
              epoch=old.epoch, reservation_id="i2")
    if with_reservation:
        ledger = ReservationLedger.restore(recovery_dir / "reservations.json", limits=_limits())
        assert ledger.reserve(SpotIntent("i2", "BUY", Decimal("1"), Decimal("1"),
                                         old.session_id, old.epoch),
                              reference_price=Decimal("1")).allowed
        if not already_aborted:
            with pytest.raises(ValueError, match="UNSENT_WAL_PROOF_INVALID"):
                ledger.abort_unsent("i2", session_id=old.session_id,
                                    epoch=old.epoch, wal=wal)
    if already_aborted:
        wal.abort_before_send("i2")  # crash before the reservation release
    connector = FakeOkx()
    provider = MagicMock()
    provider.get_connector_with_fallback.return_value = connector
    controller = LifeLiquidityController(_recovery_config(recovery_dir), provider, MagicMock())
    try:
        controller._restore_order_safety()
        assert IntentWAL(recovery_dir / "intents.json").get("i2").state == "ABORTED_BEFORE_SEND"
        ledger = ReservationLedger.restore(recovery_dir / "reservations.json", limits=_limits())
        assert ledger.reserved_usdt == Decimal("1")  # i1 remains unresolved
        if with_reservation:
            assert ledger.is_terminal_intent("i2")
        assert "wire-2" not in controller._order_safety_wal.scoped_order_ids(
            old.session_id, old.epoch)
        assert connector.cancels == []
    finally:
        controller.stop()


def test_pre_send_recovery_rejects_filled_reservation(tmp_path):
    recovery_dir = tmp_path / "recovery"
    _seed_recovery(recovery_dir)
    wal = IntentWAL(recovery_dir / "intents.json")
    old = wal.get("i1")
    wal.begin("i2", client_order_id="wire-2", session_id=old.session_id,
              epoch=old.epoch, reservation_id="i2")
    ledger = ReservationLedger.restore(recovery_dir / "reservations.json", limits=_limits())
    assert ledger.reserve(SpotIntent("i2", "BUY", Decimal("1"), Decimal("1"),
                                     old.session_id, old.epoch),
                          reference_price=Decimal("1")).allowed
    ledger.record_fill("i2", "unexpected-fill", Decimal("0.1"), Decimal("1"))
    provider = MagicMock()
    provider.get_connector_with_fallback.return_value = FakeOkx()
    controller = LifeLiquidityController(_recovery_config(recovery_dir), provider, MagicMock())
    controller.on_safety_tick(10)
    assert controller.order_safety_reason_code == "ORDER_SAFETY_RECOVERY_FAILED"
    assert IntentWAL(wal.path).get("i2").state == "PREPARED"


def test_recovery_reads_journals_only_after_account_lock(tmp_path, monkeypatch):
    recovery_dir = tmp_path / "recovery"
    _seed_recovery(recovery_dir)
    wal = IntentWAL(recovery_dir / "intents.json")
    old = wal.get("i1")
    wal.begin("i2", client_order_id="wire-2", session_id=old.session_id,
              epoch=old.epoch, reservation_id="i2")
    original_acquire = account_lock.AccountRiskPoolLock.acquire

    def competing_writer_before_lock(self):
        wal.arm_send("i2", client_order_id="wire-2", session_id=old.session_id,
                     epoch=old.epoch, reservation_id="i2")
        return original_acquire(self)

    monkeypatch.setattr(account_lock.AccountRiskPoolLock, "acquire",
                        competing_writer_before_lock)
    provider = MagicMock()
    provider.get_connector_with_fallback.return_value = FakeOkx()
    controller = LifeLiquidityController(_recovery_config(recovery_dir), provider, MagicMock())
    try:
        controller.on_safety_tick(10)
        assert controller.order_safety_reason_code == "ORDER_SAFETY_RECOVERY_FAILED"
        assert IntentWAL(wal.path).get("i2").state == "SEND_UNKNOWN"
    finally:
        controller.stop()


def _seed_recovery(directory):
    directory.mkdir()
    clock = FakeClock()
    clock.wall = datetime.now(timezone.utc) - timedelta(seconds=20)
    primary = start(manager(directory, clock))
    (directory / "transition.json").replace(directory / "session.json")
    wal = IntentWAL(directory / "intents.json")
    wal.prepare("i1", client_order_id="wire-1", session_id=primary.session_id,
                epoch=primary.epoch, reservation_id="i1")
    _reservation(directory / "reservations.json", primary.session_id, primary.epoch)
    _cashflows(directory)


@pytest.mark.asyncio
async def test_two_controller_ids_on_same_uid_cannot_recover_concurrently(tmp_path):
    first_dir, second_dir = tmp_path / "first", tmp_path / "second"
    _seed_recovery(first_dir)
    _seed_recovery(second_dir)
    connector = FakeOkx()
    connector.bill_pages[None] = [{"billId": "100"}]
    connector.status["wire-1"] = {"clOrdId": "wire-1", "ordId": "exchange-1",
                                  "state": "live", "accFillSz": "0"}
    provider = MagicMock()
    provider.get_connector_with_fallback.return_value = connector
    first = LifeLiquidityController(_recovery_config(first_dir), provider, MagicMock())
    second_config = _recovery_config(second_dir).model_copy(update={"id": "other-strategy"})
    second = LifeLiquidityController(second_config, provider, MagicMock())
    try:
        first.on_safety_tick(10)
        await first.order_safety_task
        cancels_after_first = list(connector.cancels)
        second.on_safety_tick(10)
        assert second.order_safety_task is None
        assert second.order_safety_reason_code == "ACCOUNT_LOCK_HELD_ELSEWHERE"
        assert connector.cancels == cancels_after_first
        first.stop()
        second.on_safety_tick(11)
        await second.order_safety_task
        assert len(connector.cancels) > len(cancels_after_first)
    finally:
        first.stop()
        second.stop()


@pytest.mark.asyncio
async def test_wrong_account_uid_blocks_recovery_before_cancel(tmp_path):
    recovery_dir = tmp_path / "recovery"
    _seed_recovery(recovery_dir)
    connector = FakeOkx()
    connector.account_uid = "67890"
    provider = MagicMock()
    provider.get_connector_with_fallback.return_value = connector
    controller = LifeLiquidityController(_recovery_config(recovery_dir), provider, MagicMock())
    try:
        controller.on_safety_tick(10)
        await controller.order_safety_task
        assert controller.order_safety_reason_code == "ACCOUNT_UID_MISMATCH"
        assert connector.cancels == []
    finally:
        controller.stop()
