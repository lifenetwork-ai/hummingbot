"""P5.7 spot cancellation and reconciliation over persisted OKX wire IDs."""

from datetime import datetime, timezone
from decimal import Decimal
from test.hummingbot.strategy_v2.life_liquidity.test_reference_transition import manager, start
from test.hummingbot.strategy_v2.life_liquidity.test_session import FakeClock
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from hummingbot.connector.exchange.okx import okx_constants as CONSTANTS
from hummingbot.connector.exchange.okx.okx_exchange import OkxExchange
from hummingbot.strategy_v2.life_liquidity.order_gateway import (
    OkxSpotOrderGateway,
    SpotAccountReconciler,
    SpotReservationReconciler,
)
from hummingbot.strategy_v2.life_liquidity.risk import ReservationLedger, RiskLimits, SpotIntent
from hummingbot.strategy_v2.life_liquidity.state import IntentWAL
from hummingbot.strategy_v2.life_liquidity.transition import advance_successor

NOW = datetime(2026, 10, 5, 12, tzinfo=timezone.utc)


class FakeOkx:
    def __init__(self):
        self.account_uid = "12345"
        self.cancels = []
        self.status = {}
        self.fills = {}
        self.fail_status = False
        self.open_pages = {None: []}
        self.account_open_queries = []
        self.history_pages = {None: []}
        self.fill_history_pages = {None: []}
        self.bill_pages = {None: []}
        self.cash_balances = {"LIFE": "10", "USDT": "10"}

    async def get_account_uid(self):
        return self.account_uid

    async def cancel_by_client_id(self, pair, wire_id):
        self.cancels.append((pair, wire_id))
        return True  # request ACK, not terminal confirmation

    async def cancel_by_exchange_order_id(self, pair, exchange_id):
        self.cancels.append((pair, exchange_id))
        return True

    async def get_order_by_client_id(self, pair, wire_id):
        if self.fail_status:
            raise TimeoutError("status unknown")
        return {"code": "0", "data": [self.status[wire_id]]}

    async def get_order_by_exchange_order_id(self, pair, exchange_id):
        if self.fail_status:
            raise TimeoutError("status unknown")
        for item in self.status.values():
            if item["ordId"] == exchange_id:
                return {"code": "0", "data": [item]}
        return {"code": "0", "data": []}

    async def get_fills_by_exchange_order_id(self, pair, exchange_id):
        return {"code": "0", "data": self.fills.get(exchange_id, [])}

    async def get_open_spot_orders_page(self, pair, after=None):
        orders = [item for item in self.open_pages[after] if item["instId"] == pair]
        return {"code": "0", "data": orders}

    async def get_all_open_spot_orders_page(self, after=None):
        self.account_open_queries.append(after)
        return {"code": "0", "data": [
            {"instType": "SPOT", **item} for item in self.open_pages[after]]}

    async def get_spot_cash_balances(self):
        return {"code": "0", "data": [{"details": [
            {"ccy": currency, "cashBal": amount, "liab": "0"}
            for currency, amount in self.cash_balances.items()]}]}

    async def get_spot_order_history_page(self, pair, after=None):
        return {"code": "0", "data": self.history_pages[after]}

    async def get_spot_fill_history_page(self, pair, exchange_id, after=None):
        return {"code": "0", "data": self.fill_history_pages[after]}

    async def get_account_bills_page(self, after=None):
        return {"code": "0", "data": self.bill_pages[after]}


def prepared(tmp_path, *, quantity=Decimal("1")):
    path = tmp_path / "intents.json"
    wal = IntentWAL(path)
    wal.prepare("i1", client_order_id="wire-1", session_id="old-session",
                epoch=1, reservation_id="r1")
    return path, wal


def order(state, *, filled="0"):
    return {"clOrdId": "wire-1", "ordId": "exchange-1", "state": state,
            "accFillSz": filled}


def full_scope(_session_id, _epoch, _wire_ids):
    return True


def confirmed_terminal(_wire_id, _state, _cumulative):
    return True


@pytest.mark.asyncio
async def test_cancel_ack_stays_pending_until_authoritative_terminal_status(tmp_path):
    path, wal = prepared(tmp_path)
    connector = FakeOkx()
    connector.status["wire-1"] = order("live")
    gateway = OkxSpotOrderGateway(connector, wal, trading_pair="LIFE-USDT",
                                  clock=lambda: NOW, scope_check=full_scope,
                                  confirm_terminal=confirmed_terminal)
    await gateway.request_cancel("old-session", 1)
    assert connector.cancels == [("LIFE-USDT", "wire-1")]
    assert IntentWAL(path).get("i1").cancel_requested
    pending = await gateway.reconcile("old-session", 1)
    assert pending.scope_complete
    assert pending.pending_cancel_ids == ("wire-1",)
    assert pending.open_order_ids == ()
    connector.status["wire-1"] = order("canceled")
    terminal = await gateway.reconcile("old-session", 1)
    assert terminal.scope_complete and terminal.trade_events_reconciled
    assert not terminal.pending_cancel_ids and not terminal.unknown_order_ids
    assert IntentWAL(path).get("i1").state == "TERMINAL"
    assert IntentWAL(path).pending_reconciliation("old-session", 1) == ()


@pytest.mark.asyncio
async def test_status_timeout_after_restart_remains_unknown(tmp_path):
    path, wal = prepared(tmp_path)
    connector = FakeOkx()
    first = OkxSpotOrderGateway(connector, wal, trading_pair="LIFE-USDT", clock=lambda: NOW,
                                scope_check=full_scope, confirm_terminal=confirmed_terminal)
    await first.request_cancel("old-session", 1)
    connector.fail_status = True
    restarted = OkxSpotOrderGateway(connector, IntentWAL(path), trading_pair="LIFE-USDT",
                                    clock=lambda: NOW, scope_check=full_scope,
                                    confirm_terminal=confirmed_terminal)
    result = await restarted.reconcile("old-session", 1)
    assert not result.scope_complete
    assert result.unknown_order_ids == ("wire-1",)
    assert IntentWAL(path).get("i1").cancel_requested
    await restarted.request_cancel("old-session", 1)
    assert connector.cancels == [("LIFE-USDT", "wire-1"), ("LIFE-USDT", "wire-1")]


@pytest.mark.asyncio
async def test_partial_fill_during_cancel_requires_matching_fills_and_durable_apply(tmp_path):
    _, wal = prepared(tmp_path)
    connector = FakeOkx()
    connector.status["wire-1"] = order("canceled", filled="0.4")
    connector.fills["exchange-1"] = [{"tradeId": "trade-1", "ordId": "exchange-1",
                                      "fillSz": "0.4", "fillPx": "1.02"}]
    observed = []

    def apply(wire_id, fills, cumulative):
        observed.append((wire_id, fills[0].trade_id, fills[0].quantity_base, cumulative))
        return False

    gateway = OkxSpotOrderGateway(connector, wal, trading_pair="LIFE-USDT",
                                  clock=lambda: NOW, apply_fills=apply,
                                  scope_check=full_scope, confirm_terminal=confirmed_terminal)
    unresolved = await gateway.reconcile("old-session", 1)
    assert unresolved.scope_complete
    assert not unresolved.trade_events_reconciled
    assert observed == [("wire-1", "trade-1", Decimal("0.4"), Decimal("0.4"))]
    gateway.apply_fills = lambda *_: True
    complete = await gateway.reconcile("old-session", 1)
    assert complete.scope_complete and complete.trade_events_reconciled
    connector.fills["exchange-1"] = []
    mismatch = await gateway.reconcile("old-session", 1)
    assert not mismatch.trade_events_reconciled
    assert mismatch.unknown_order_ids == ("wire-1",)


@pytest.mark.asyncio
async def test_only_old_epoch_wire_ids_are_canceled(tmp_path):
    _, wal = prepared(tmp_path)
    wal.prepare("i2", client_order_id="wire-2", session_id="new-session",
                epoch=2, reservation_id="r2")
    connector = FakeOkx()
    gateway = OkxSpotOrderGateway(connector, wal, trading_pair="LIFE-USDT", clock=lambda: NOW,
                                  scope_check=full_scope, confirm_terminal=confirmed_terminal)
    await gateway.request_cancel("old-session", 1)
    assert connector.cancels == [("LIFE-USDT", "wire-1")]


@pytest.mark.asyncio
async def test_cancel_transport_without_ack_preserves_pending_intent(tmp_path):
    path, wal = prepared(tmp_path)
    connector = FakeOkx()

    async def no_ack(pair, wire_id):
        connector.cancels.append((pair, wire_id))
        return False

    connector.cancel_by_client_id = no_ack
    gateway = OkxSpotOrderGateway(connector, wal, trading_pair="LIFE-USDT",
                                  clock=lambda: NOW, scope_check=full_scope,
                                  confirm_terminal=confirmed_terminal)
    with pytest.raises(IOError, match="CANCEL_ACK_UNAVAILABLE"):
        await gateway.request_cancel("old-session", 1)
    assert IntentWAL(path).get("i1").cancel_requested


@pytest.mark.asyncio
async def test_okx_queries_and_cancels_by_wire_id_without_inflight_tracker():
    connector = OkxExchange("key", "secret", "passphrase", trading_pairs=[], trading_required=False)
    connector.exchange_symbol_associated_to_pair = AsyncMock(return_value="LIFE-USDT")
    connector._api_request = AsyncMock(side_effect=[
        {"data": [order("live")]},
        {"data": []},
        {"code": "0", "data": [{"sCode": "0"}]},
    ])
    assert (await connector.get_order_by_client_id("LIFE-USDT", "wire-1"))["data"][0]["state"] == "live"
    assert (await connector.get_fills_by_exchange_order_id("LIFE-USDT", "exchange-1"))["data"] == []
    assert await connector.cancel_by_client_id("LIFE-USDT", "wire-1")
    status_call, fills_call, cancel_call = connector._api_request.await_args_list
    assert status_call.kwargs["path_url"] == CONSTANTS.OKX_ORDER_DETAILS_PATH
    assert status_call.kwargs["params"]["clOrdId"] == "wire-1"
    assert fills_call.kwargs["path_url"] == CONSTANTS.OKX_TRADE_FILLS_PATH
    assert fills_call.kwargs["params"]["ordId"] == "exchange-1"
    assert cancel_call.kwargs["path_url"] == CONSTANTS.OKX_ORDER_CANCEL_PATH
    assert cancel_call.kwargs["data"]["clOrdId"] == "wire-1"
    assert all(call.kwargs["is_auth_required"] for call in connector._api_request.await_args_list)


@pytest.mark.asyncio
async def test_okx_cancel_error_never_counts_as_terminal_confirmation():
    connector = OkxExchange("key", "secret", "passphrase", trading_pairs=[], trading_required=False)
    connector.exchange_symbol_associated_to_pair = AsyncMock(return_value="LIFE-USDT")
    connector._api_request = AsyncMock(return_value={
        "code": "0", "data": [{"sCode": "51000", "sMsg": "failed"}]})
    with pytest.raises(IOError, match="Error cancelling order"):
        await connector.cancel_by_client_id("LIFE-USDT", "wire-1")


@pytest.mark.asyncio
async def test_successor_waits_for_gateway_terminal_evidence(tmp_path):
    clock = FakeClock()
    active = manager(tmp_path, clock)
    primary = start(active)
    wal = IntentWAL(tmp_path / "intents.json")
    wal.prepare("i1", client_order_id="wire-1", session_id=primary.session_id,
                epoch=primary.epoch, reservation_id="r1")
    connector = FakeOkx()
    connector.status["wire-1"] = order("live")
    gateway = OkxSpotOrderGateway(connector, wal, trading_pair="LIFE-USDT",
                                  clock=lambda: clock.wall, scope_check=full_scope,
                                  confirm_terminal=confirmed_terminal)
    clock.advance(10)

    async def advance():
        return await advance_successor(
            active, gateway, reference_ready=True, all_gates_ready=True,
            market_reference_ready=True, market_anchor_usdt=Decimal("1.01"))

    assert await advance() == "TRANSITIONING"
    assert active.reason_code == "OLD_ORDERS_UNRESOLVED"
    connector.status["wire-1"] = order("canceled")
    assert await advance() == "ACTIVE"
    assert active.current_session.epoch == primary.epoch + 1


@pytest.mark.asyncio
async def test_terminal_without_full_scope_or_reservation_confirmation_cannot_activate(tmp_path):
    _, wal = prepared(tmp_path)
    connector = FakeOkx()
    connector.status["wire-1"] = order("canceled")
    connector.open_pages = {}  # account-scope query unavailable
    no_scope = OkxSpotOrderGateway(connector, wal, trading_pair="LIFE-USDT",
                                   clock=lambda: NOW, confirm_terminal=confirmed_terminal)
    result = await no_scope.reconcile("old-session", 1)
    assert not result.scope_complete
    no_terminal = OkxSpotOrderGateway(connector, wal, trading_pair="LIFE-USDT",
                                      clock=lambda: NOW, scope_check=full_scope)
    result = await no_terminal.reconcile("old-session", 1)
    assert not result.trade_events_reconciled


@pytest.mark.asyncio
async def test_account_open_order_outside_wal_blocks_complete_scope(tmp_path):
    _, wal = prepared(tmp_path)
    connector = FakeOkx()
    connector.status["wire-1"] = order("canceled")
    connector.open_pages[None] = [{"clOrdId": "manual-order", "ordId": "exchange-2",
                                   "instId": "LIFE-USDT", "state": "live"}]
    gateway = OkxSpotOrderGateway(connector, wal, trading_pair="LIFE-USDT",
                                  clock=lambda: NOW, confirm_terminal=confirmed_terminal)
    blocked = await gateway.reconcile("old-session", 1)
    assert not blocked.scope_complete
    connector.open_pages[None] = []
    complete = await gateway.reconcile("old-session", 1)
    assert complete.scope_complete


@pytest.mark.asyncio
async def test_manual_open_order_on_another_spot_pair_blocks_scope_without_cancel(tmp_path):
    _, wal = prepared(tmp_path)
    connector = FakeOkx()
    connector.status["wire-1"] = order("canceled")
    connector.open_pages[None] = [{"clOrdId": "manual-btc", "ordId": "exchange-btc",
                                   "instId": "BTC-USDT", "state": "live"}]
    gateway = OkxSpotOrderGateway(connector, wal, trading_pair="LIFE-USDT",
                                  clock=lambda: NOW, confirm_terminal=confirmed_terminal)
    await gateway.request_cancel("old-session", 1)
    result = await gateway.reconcile("old-session", 1)
    assert not result.scope_complete
    assert wal.get("i1").state != "TERMINAL"
    assert connector.account_open_queries == [None]
    assert connector.cancels == [("LIFE-USDT", "wire-1")]


@pytest.mark.asyncio
async def test_manual_other_pair_on_second_account_page_blocks_scope():
    connector = FakeOkx()
    records = tuple(SimpleNamespace(client_order_id=f"wire-{number}",
                                    exchange_order_id=str(200 - number))
                    for number in range(100))
    wal = SimpleNamespace(all_records=lambda: records)
    connector.open_pages[None] = [
        {"clOrdId": record.client_order_id, "ordId": record.exchange_order_id,
         "instId": "LIFE-USDT", "state": "live"} for record in records]
    connector.open_pages["101"] = [{"clOrdId": "manual-btc", "ordId": "100",
                                    "instId": "BTC-USDT", "state": "live"}]
    gateway = OkxSpotOrderGateway(connector, wal, trading_pair="LIFE-USDT", clock=lambda: NOW)
    assert not await gateway._account_scope_complete(set(), {})
    assert connector.account_open_queries == [None, "101"]


@pytest.mark.asyncio
async def test_missing_account_wide_open_order_method_fails_closed(tmp_path):
    _, wal = prepared(tmp_path)
    connector = FakeOkx()
    connector.get_all_open_spot_orders_page = None
    connector.status["wire-1"] = order("canceled")
    gateway = OkxSpotOrderGateway(connector, wal, trading_pair="LIFE-USDT",
                                  clock=lambda: NOW, confirm_terminal=confirmed_terminal)
    assert not (await gateway.reconcile("old-session", 1)).scope_complete


@pytest.mark.asyncio
async def test_account_open_list_cannot_disagree_with_terminal_status(tmp_path):
    _, wal = prepared(tmp_path)
    connector = FakeOkx()
    connector.status["wire-1"] = order("canceled")
    connector.open_pages[None] = [{"clOrdId": "wire-1", "ordId": "exchange-1",
                                   "instId": "LIFE-USDT", "state": "live"}]
    gateway = OkxSpotOrderGateway(connector, wal, trading_pair="LIFE-USDT",
                                  clock=lambda: NOW, confirm_terminal=confirmed_terminal)
    assert not (await gateway.reconcile("old-session", 1)).scope_complete


@pytest.mark.asyncio
async def test_okx_open_order_query_uses_spot_pair_and_cursor():
    connector = OkxExchange("key", "secret", "passphrase", trading_pairs=[], trading_required=False)
    connector.exchange_symbol_associated_to_pair = AsyncMock(return_value="LIFE-USDT")
    connector._api_request = AsyncMock(return_value={"data": []})
    await connector.get_open_spot_orders_page("LIFE-USDT", after="123")
    kwargs = connector._api_request.await_args.kwargs
    assert kwargs["path_url"] == CONSTANTS.OKX_ORDERS_PENDING_PATH
    assert kwargs["params"] == {"instType": "SPOT", "instId": "LIFE-USDT",
                                "limit": "100", "after": "123"}
    assert kwargs["is_auth_required"]


@pytest.mark.asyncio
async def test_okx_account_wide_open_order_query_has_no_instrument_filter():
    connector = OkxExchange("key", "secret", "passphrase", trading_pairs=[], trading_required=False)
    connector._api_request = AsyncMock(return_value={"code": "0", "data": []})
    await connector.get_all_open_spot_orders_page(after="123")
    kwargs = connector._api_request.await_args.kwargs
    assert kwargs["path_url"] == CONSTANTS.OKX_ORDERS_PENDING_PATH
    assert kwargs["params"] == {"instType": "SPOT", "limit": "100", "after": "123"}
    assert kwargs["is_auth_required"]


@pytest.mark.asyncio
async def test_nonzero_okx_response_code_cannot_clear_old_order(tmp_path):
    _, wal = prepared(tmp_path)
    connector = FakeOkx()
    connector.status["wire-1"] = order("canceled")

    async def error_status(pair, wire_id):
        return {"code": "51000", "data": [connector.status[wire_id]]}

    connector.get_order_by_client_id = error_status
    gateway = OkxSpotOrderGateway(connector, wal, trading_pair="LIFE-USDT",
                                  clock=lambda: NOW, confirm_terminal=confirmed_terminal)
    result = await gateway.reconcile("old-session", 1)
    assert not result.scope_complete
    assert result.unknown_order_ids == ("wire-1",)


@pytest.mark.asyncio
async def test_real_reservation_ledger_keeps_remainder_until_terminal_fill_reconciliation(tmp_path):
    _, wal = prepared(tmp_path)
    limits = RiskLimits(Decimal("0"), Decimal("10"), Decimal("10"), Decimal("10"))
    reservations = ReservationLedger(life_balance=Decimal("1"), usdt_balance=Decimal("2"),
                                     limits=limits)
    intent = SpotIntent("i1", "BUY", Decimal("1"), Decimal("1"), "old-session", 1)
    assert reservations.reserve(intent, reference_price=Decimal("1")).allowed
    connector = FakeOkx()
    connector.status["wire-1"] = order("partially_filled", filled="0.4")
    connector.fills["exchange-1"] = [{"tradeId": "trade-1", "ordId": "exchange-1",
                                      "fillSz": "0.4", "fillPx": "1"}]
    reconciler = SpotReservationReconciler(wal, reservations)
    gateway = OkxSpotOrderGateway(
        connector, wal, trading_pair="LIFE-USDT", clock=lambda: NOW,
        apply_fills=reconciler.apply_fills,
        confirm_terminal=reconciler.confirm_terminal,
        scope_check=full_scope)
    pending = await gateway.reconcile("old-session", 1)
    assert pending.open_order_ids == ("wire-1",)
    assert reservations.reserved_usdt == Decimal("0.6")
    connector.status["wire-1"] = order("canceled", filled="0.4")
    terminal = await gateway.reconcile("old-session", 1)
    assert terminal.trade_events_reconciled
    assert reservations.reserved_usdt == 0
    assert reservations.life_balance == Decimal("1.4")
    again = await gateway.reconcile("old-session", 1)
    assert again.trade_events_reconciled
    assert reservations.life_balance == Decimal("1.4")


@pytest.mark.asyncio
async def test_account_open_list_with_reused_client_id_and_different_exchange_id_blocks(tmp_path):
    _, wal = prepared(tmp_path)
    wal.acknowledge("i1", "exchange-1")
    connector = FakeOkx()
    connector.status["wire-1"] = order("live")
    connector.open_pages[None] = [{"clOrdId": "wire-1", "ordId": "exchange-2",
                                   "instId": "LIFE-USDT", "state": "live"}]
    gateway = OkxSpotOrderGateway(connector, wal, trading_pair="LIFE-USDT",
                                  clock=lambda: NOW)
    result = await gateway.reconcile("old-session", 1)
    assert not result.scope_complete


@pytest.mark.asyncio
async def test_open_order_scope_checks_second_page_and_fails_on_unknown_id():
    connector = FakeOkx()
    records = tuple(SimpleNamespace(client_order_id=f"wire-{number}",
                                    exchange_order_id=str(200 - number))
                    for number in range(100))
    wal = SimpleNamespace(all_records=lambda: records)
    connector.open_pages[None] = [
        {"clOrdId": record.client_order_id, "ordId": record.exchange_order_id,
         "instId": "LIFE-USDT", "state": "live"}
        for record in records]
    connector.open_pages["101"] = []
    gateway = OkxSpotOrderGateway(connector, wal, trading_pair="LIFE-USDT", clock=lambda: NOW)
    assert await gateway._account_scope_complete(set(), {})
    connector.open_pages.pop("101")  # second page unavailable: scan is incomplete
    assert not await gateway._account_scope_complete(set(), {})
    connector.open_pages["101"] = [{"clOrdId": "manual", "ordId": "100",
                                    "instId": "LIFE-USDT", "state": "live"}]
    assert not await gateway._account_scope_complete(set(), {})


@pytest.mark.asyncio
async def test_restart_without_restored_reservation_cannot_clear_terminal_fill(tmp_path):
    path, _ = prepared(tmp_path)
    connector = FakeOkx()
    connector.status["wire-1"] = order("canceled", filled="0.4")
    connector.fills["exchange-1"] = [{"tradeId": "trade-1", "ordId": "exchange-1",
                                      "fillSz": "0.4", "fillPx": "1"}]
    limits = RiskLimits(Decimal("0"), Decimal("10"), Decimal("10"), Decimal("10"))
    empty_ledger = ReservationLedger(life_balance=Decimal("1"),
                                     usdt_balance=Decimal("2"), limits=limits)
    restarted_wal = IntentWAL(path)
    reconciler = SpotReservationReconciler(restarted_wal, empty_ledger)
    gateway = OkxSpotOrderGateway(
        connector, restarted_wal, trading_pair="LIFE-USDT", clock=lambda: NOW,
        apply_fills=reconciler.apply_fills,
        confirm_terminal=reconciler.confirm_terminal)
    result = await gateway.reconcile("old-session", 1)
    assert not result.trade_events_reconciled
    assert restarted_wal.get("i1").state == "ACKED"  # ID found; fill still unverified


@pytest.mark.asyncio
async def test_restored_reservation_replays_fill_once_and_clears_terminal(tmp_path):
    wal_path, wal = prepared(tmp_path)
    risk_path = tmp_path / "risk.json"
    limits = RiskLimits(Decimal("0"), Decimal("10"), Decimal("10"), Decimal("10"))
    first_ledger = ReservationLedger(life_balance=Decimal("1"),
                                     usdt_balance=Decimal("2"), limits=limits, path=risk_path)
    intent = SpotIntent("i1", "BUY", Decimal("1"), Decimal("1"), "old-session", 1)
    assert first_ledger.reserve(intent, reference_price=Decimal("1")).allowed
    connector = FakeOkx()
    connector.status["wire-1"] = order("partially_filled", filled="0.4")
    connector.fills["exchange-1"] = [{"tradeId": "trade-1", "ordId": "exchange-1",
                                      "fillSz": "0.4", "fillPx": "1"}]
    first_adapter = SpotReservationReconciler(wal, first_ledger)
    first_gateway = OkxSpotOrderGateway(
        connector, wal, trading_pair="LIFE-USDT", clock=lambda: NOW,
        apply_fills=first_adapter.apply_fills,
        confirm_terminal=first_adapter.confirm_terminal)
    assert (await first_gateway.reconcile("old-session", 1)).open_order_ids == ("wire-1",)
    assert first_ledger.reserved_usdt == Decimal("0.6")

    restarted_wal = IntentWAL(wal_path)
    restarted_ledger = ReservationLedger.restore(risk_path, limits=limits)
    restarted_adapter = SpotReservationReconciler(restarted_wal, restarted_ledger)
    connector.status["wire-1"] = order("canceled", filled="0.4")
    restarted_gateway = OkxSpotOrderGateway(
        connector, restarted_wal, trading_pair="LIFE-USDT", clock=lambda: NOW,
        apply_fills=restarted_adapter.apply_fills,
        confirm_terminal=restarted_adapter.confirm_terminal)
    result = await restarted_gateway.reconcile("old-session", 1)
    assert result.scope_complete and result.trade_events_reconciled
    assert restarted_ledger.reserved_usdt == 0
    assert restarted_ledger.life_balance == Decimal("1.4")
    assert ReservationLedger.restore(risk_path, limits=limits).life_balance == Decimal("1.4")


@pytest.mark.asyncio
async def test_balance_mismatch_blocks_terminal_release_until_account_reconciles(tmp_path):
    _, wal = prepared(tmp_path)
    limits = RiskLimits(Decimal("0"), Decimal("10"), Decimal("10"), Decimal("10"))
    reservations = ReservationLedger(life_balance=Decimal("1"), usdt_balance=Decimal("2"),
                                     limits=limits)
    intent = SpotIntent("i1", "BUY", Decimal("1"), Decimal("1"), "old-session", 1)
    assert reservations.reserve(intent, reference_price=Decimal("1")).allowed
    connector = FakeOkx()
    connector.status["wire-1"] = order("canceled", filled="0.4")
    connector.fills["exchange-1"] = [{"tradeId": "trade-1", "ordId": "exchange-1",
                                      "fillSz": "0.4", "fillPx": "1"}]
    connector.cash_balances = {"LIFE": "1", "USDT": "2"}
    reconciler = SpotReservationReconciler(wal, reservations)
    gateway = OkxSpotOrderGateway(
        connector, wal, trading_pair="LIFE-USDT", clock=lambda: NOW,
        apply_fills=reconciler.apply_fills, confirm_terminal=reconciler.confirm_terminal,
        account_check=SpotAccountReconciler(connector, reservations).check)
    blocked = await gateway.reconcile("old-session", 1)
    assert not blocked.scope_complete and not blocked.trade_events_reconciled
    assert reservations.reserved_usdt == Decimal("0.6")
    assert wal.get("i1").state != "TERMINAL"
    connector.cash_balances = {"LIFE": "1.4", "USDT": "1.6"}
    complete = await gateway.reconcile("old-session", 1)
    assert complete.scope_complete and complete.trade_events_reconciled
    assert reservations.reserved_usdt == 0


@pytest.mark.asyncio
async def test_account_check_rejects_missing_duplicate_or_liability_balances():
    connector = FakeOkx()
    ledger = ReservationLedger(life_balance=Decimal("10"), usdt_balance=Decimal("10"),
                               limits=RiskLimits(Decimal("0"), Decimal("20"),
                                                 Decimal("30"), Decimal("20")))
    checker = SpotAccountReconciler(connector, ledger)
    assert await checker.check()
    connector.cash_balances.pop("LIFE")
    assert not await checker.check()
    connector.cash_balances["LIFE"] = "NaN"
    assert not await checker.check()
    connector.cash_balances["LIFE"] = "10"

    async def duplicate():
        return {"code": "0", "data": [{"details": [
            {"ccy": "LIFE", "cashBal": "10"}, {"ccy": "LIFE", "cashBal": "10"},
            {"ccy": "USDT", "cashBal": "10"}]}]}

    connector.get_spot_cash_balances = duplicate
    assert not await checker.check()


@pytest.mark.asyncio
async def test_history_fallback_needs_complete_unique_terminal_match(tmp_path):
    _, wal = prepared(tmp_path)
    connector = FakeOkx()
    connector.fail_status = True
    historical = {**order("canceled"), "instId": "LIFE-USDT"}
    connector.history_pages[None] = [historical]
    gateway = OkxSpotOrderGateway(connector, wal, trading_pair="LIFE-USDT",
                                  clock=lambda: NOW, confirm_terminal=confirmed_terminal)
    result = await gateway.reconcile("old-session", 1)
    assert result.scope_complete and result.trade_events_reconciled
    assert wal.get("i1").state == "TERMINAL"

    _, second_wal = prepared(tmp_path / "second")
    connector.history_pages[None] = [historical, {**historical, "ordId": "exchange-2"}]
    ambiguous = OkxSpotOrderGateway(connector, second_wal, trading_pair="LIFE-USDT",
                                    clock=lambda: NOW, confirm_terminal=confirmed_terminal)
    result = await ambiguous.reconcile("old-session", 1)
    assert not result.scope_complete and result.unknown_order_ids == ("wire-1",)
    assert second_wal.get("i1").state != "TERMINAL"


@pytest.mark.asyncio
async def test_history_fallback_rejects_incomplete_pages(tmp_path):
    _, wal = prepared(tmp_path)
    connector = FakeOkx()
    connector.fail_status = True
    connector.history_pages[None] = [
        {**order("canceled"), "clOrdId": f"other-{index}", "ordId": str(index),
         "instId": "LIFE-USDT"} for index in range(100)]
    gateway = OkxSpotOrderGateway(connector, wal, trading_pair="LIFE-USDT",
                                  clock=lambda: NOW, confirm_terminal=confirmed_terminal)
    result = await gateway.reconcile("old-session", 1)
    assert not result.scope_complete
    assert result.unknown_order_ids == ("wire-1",)


@pytest.mark.asyncio
async def test_okx_history_and_cash_queries_use_authenticated_endpoints():
    connector = OkxExchange("key", "secret", "passphrase", trading_pairs=[], trading_required=False)
    connector.exchange_symbol_associated_to_pair = AsyncMock(return_value="LIFE-USDT")
    connector._api_get = AsyncMock(return_value={"code": "0", "data": []})
    await connector.get_spot_order_history_page("LIFE-USDT", after="123")
    await connector.get_spot_cash_balances()
    history_call, cash_call = connector._api_get.await_args_list
    assert history_call.kwargs["path_url"] == CONSTANTS.OKX_ORDERS_HISTORY_PATH
    assert history_call.kwargs["params"] == {
        "instType": "SPOT", "instId": "LIFE-USDT", "limit": "100", "after": "123"}
    assert history_call.kwargs["is_auth_required"]
    assert cash_call.kwargs["path_url"] == CONSTANTS.OKX_BALANCE_PATH
    assert cash_call.kwargs["is_auth_required"]


@pytest.mark.asyncio
async def test_acked_order_uses_exchange_id_for_cancel_and_status(tmp_path):
    _, wal = prepared(tmp_path)
    wal.acknowledge("i1", "exchange-1")
    connector = FakeOkx()
    connector.status["wire-1"] = order("canceled")

    async def by_client_id(*_):
        raise AssertionError("clOrdId must not select an ACKed order")

    connector.cancel_by_client_id = by_client_id
    connector.get_order_by_client_id = by_client_id
    gateway = OkxSpotOrderGateway(connector, wal, trading_pair="LIFE-USDT",
                                  clock=lambda: NOW, confirm_terminal=confirmed_terminal)
    await gateway.request_cancel("old-session", 1)
    result = await gateway.reconcile("old-session", 1)
    assert connector.cancels == [("LIFE-USDT", "exchange-1")]
    assert result.scope_complete and result.trade_events_reconciled


@pytest.mark.asyncio
async def test_okx_exchange_id_paths_do_not_use_client_id():
    connector = OkxExchange("key", "secret", "passphrase", trading_pairs=[], trading_required=False)
    connector.exchange_symbol_associated_to_pair = AsyncMock(return_value="LIFE-USDT")
    connector._api_get = AsyncMock(return_value={"code": "0", "data": []})
    connector._api_post = AsyncMock(return_value={"code": "0", "data": [{"sCode": "0"}]})
    await connector.get_order_by_exchange_order_id("LIFE-USDT", "exchange-1")
    assert connector._api_get.await_args.kwargs["params"] == {
        "instId": "LIFE-USDT", "ordId": "exchange-1"}
    assert await connector.cancel_by_exchange_order_id("LIFE-USDT", "exchange-1")
    assert connector._api_post.await_args.kwargs["data"] == {
        "instId": "LIFE-USDT", "ordId": "exchange-1"}


@pytest.mark.asyncio
async def test_fill_history_recovers_old_partial_fill_after_restart(tmp_path):
    _, wal = prepared(tmp_path)
    connector = FakeOkx()
    connector.status["wire-1"] = order("canceled", filled="0.4")
    connector.fill_history_pages[None] = [{"tradeId": "trade-old", "billId": "bill-1",
                                           "ordId": "exchange-1", "instId": "LIFE-USDT",
                                           "fillSz": "0.4", "fillPx": "1"}]
    limits = RiskLimits(Decimal("0"), Decimal("10"), Decimal("10"), Decimal("10"))
    reservations = ReservationLedger(life_balance=Decimal("1"), usdt_balance=Decimal("2"),
                                     limits=limits)
    assert reservations.reserve(SpotIntent("i1", "BUY", Decimal("1"), Decimal("1"),
                                           "old-session", 1), reference_price=Decimal("1")).allowed
    reconciler = SpotReservationReconciler(wal, reservations)
    gateway = OkxSpotOrderGateway(connector, wal, trading_pair="LIFE-USDT", clock=lambda: NOW,
                                  apply_fills=reconciler.apply_fills,
                                  confirm_terminal=reconciler.confirm_terminal)
    result = await gateway.reconcile("old-session", 1)
    assert result.scope_complete and result.trade_events_reconciled
    assert reservations.life_balance == Decimal("1.4")
    assert reservations.reserved_usdt == 0
    assert wal.get("i1").state == "TERMINAL"


@pytest.mark.asyncio
async def test_incomplete_or_mismatched_fill_history_keeps_order_unknown(tmp_path):
    _, wal = prepared(tmp_path)
    connector = FakeOkx()
    connector.status["wire-1"] = order("canceled", filled="0.4")
    connector.fill_history_pages[None] = [{"tradeId": "trade-other", "billId": "bill-1",
                                           "ordId": "exchange-2", "instId": "LIFE-USDT",
                                           "fillSz": "0.4", "fillPx": "1"}]
    gateway = OkxSpotOrderGateway(connector, wal, trading_pair="LIFE-USDT", clock=lambda: NOW,
                                  confirm_terminal=confirmed_terminal)
    result = await gateway.reconcile("old-session", 1)
    assert not result.scope_complete and result.unknown_order_ids == ("wire-1",)
    connector.fill_history_pages[None] = []
    result = await gateway.reconcile("old-session", 1)
    assert not result.scope_complete and result.unknown_order_ids == ("wire-1",)
    assert wal.get("i1").state != "TERMINAL"


@pytest.mark.asyncio
async def test_malformed_recent_fill_cannot_be_overridden_by_history(tmp_path):
    _, wal = prepared(tmp_path)
    connector = FakeOkx()
    connector.status["wire-1"] = order("canceled", filled="0.4")
    duplicate = {"tradeId": "trade-1", "ordId": "exchange-1",
                 "fillSz": "0.2", "fillPx": "1"}
    connector.fills["exchange-1"] = [duplicate, duplicate]
    connector.fill_history_pages[None] = [{**duplicate, "billId": "bill-1",
                                           "instId": "LIFE-USDT", "fillSz": "0.4"}]
    gateway = OkxSpotOrderGateway(connector, wal, trading_pair="LIFE-USDT",
                                  clock=lambda: NOW, confirm_terminal=confirmed_terminal)
    result = await gateway.reconcile("old-session", 1)
    assert not result.scope_complete and result.unknown_order_ids == ("wire-1",)
    assert wal.get("i1").state != "TERMINAL"


@pytest.mark.asyncio
async def test_fill_history_pagination_requires_complete_unique_bill_ids(tmp_path):
    _, wal = prepared(tmp_path)
    connector = FakeOkx()
    connector.status["wire-1"] = order("filled", filled="1")
    connector.fill_history_pages[None] = [
        {"tradeId": f"trade-{index}", "billId": f"bill-{index}",
         "ordId": "exchange-1", "instId": "LIFE-USDT",
         "fillSz": "0.005", "fillPx": "1"} for index in range(100)]
    connector.fill_history_pages["bill-99"] = [
        {"tradeId": f"trade-{index}", "billId": f"bill-{index}",
         "ordId": "exchange-1", "instId": "LIFE-USDT",
         "fillSz": "0.005", "fillPx": "1"} for index in range(100, 200)]
    gateway = OkxSpotOrderGateway(connector, wal, trading_pair="LIFE-USDT", clock=lambda: NOW,
                                  apply_fills=lambda *_: True,
                                  confirm_terminal=confirmed_terminal)
    incomplete = await gateway.reconcile("old-session", 1)
    assert not incomplete.scope_complete
    connector.fill_history_pages["bill-199"] = []
    complete = await gateway.reconcile("old-session", 1)
    assert complete.scope_complete and complete.trade_events_reconciled
    assert wal.get("i1").state == "TERMINAL"


@pytest.mark.asyncio
async def test_okx_fill_history_uses_ord_id_and_bill_cursor():
    connector = OkxExchange("key", "secret", "passphrase", trading_pairs=[], trading_required=False)
    connector.exchange_symbol_associated_to_pair = AsyncMock(return_value="LIFE-USDT")
    connector._api_get = AsyncMock(return_value={"code": "0", "data": []})
    await connector.get_spot_fill_history_page("LIFE-USDT", "exchange-1", after="bill-1")
    kwargs = connector._api_get.await_args.kwargs
    assert kwargs["path_url"] == CONSTANTS.OKX_TRADE_FILLS_HISTORY_PATH
    assert kwargs["params"] == {"instType": "SPOT", "instId": "LIFE-USDT",
                                "ordId": "exchange-1", "limit": "100", "after": "bill-1"}
    assert kwargs["is_auth_required"]
