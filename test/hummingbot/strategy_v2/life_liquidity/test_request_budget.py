"""Host-local account request capacity is conservative across restart."""

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from test.hummingbot.strategy_v2.life_liquidity.test_executor_protected_send import _setup
from test.hummingbot.strategy_v2.life_liquidity.test_order_gateway import FakeOkx
from test.hummingbot.strategy_v2.life_liquidity.test_protected_okx_send import PausedThrottler
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from hummingbot.connector.exchange.okx.okx_exchange import OkxExchange
from hummingbot.connector.trading_rule import TradingRule
from hummingbot.core.data_type.common import OrderType
from hummingbot.core.web_assistant.connections.rest_connection import RESTConnection
from hummingbot.core.web_assistant.rest_assistant import RESTAssistant
from hummingbot.strategy_v2.life_liquidity.order_gateway import CancelRetryPolicy, OkxSpotOrderGateway
from hummingbot.strategy_v2.life_liquidity.protected_send import ProtectedSpotGateway
from hummingbot.strategy_v2.life_liquidity.request_budget import (
    AccountRequestBudget,
    RequestBudgetExceeded,
    RequestBudgetPolicy,
)
from hummingbot.strategy_v2.life_liquidity.send_gate import SendPermit
from hummingbot.strategy_v2.life_liquidity.state import IntentWAL


def _budget(tmp_path, now, *, capacity=3, reserve=1, refresh_ms=1000):
    policy = RequestBudgetPolicy(window_ms=2000, max_requests=capacity,
                                 cancel_reserve=reserve,
                                 min_slot_refresh_interval_ms=refresh_ms)
    path = tmp_path / "request_budget.json"
    if not path.exists():
        AccountRequestBudget(path, account_uid="12345", policy=policy,
                             clock=lambda: now[0]).initialize_empty()
    return AccountRequestBudget(path, account_uid="12345", policy=policy,
                                clock=lambda: now[0])


def test_explicit_policy_and_uid_are_required(tmp_path):
    with pytest.raises(ValueError, match="REQUEST_BUDGET_POLICY_INVALID"):
        RequestBudgetPolicy(window_ms=2000, max_requests=2, cancel_reserve=2,
                            min_slot_refresh_interval_ms=1)
    with pytest.raises(ValueError, match="REQUEST_BUDGET_ACCOUNT_INVALID"):
        AccountRequestBudget(tmp_path / "budget.json", account_uid="", policy=RequestBudgetPolicy(
            window_ms=2000, max_requests=3, cancel_reserve=1,
            min_slot_refresh_interval_ms=1), clock=lambda: datetime.now(timezone.utc))


def test_create_capacity_preserves_cancel_reserve_across_restart(tmp_path):
    now = [datetime(2026, 10, 6, tzinfo=timezone.utc)]
    budget = _budget(tmp_path, now, capacity=3, reserve=1)
    budget.charge("CREATE", "create-1", slot="LIFE-USDT:BUY:0")
    budget.charge("CREATE", "create-2", slot="LIFE-USDT:SELL:0")
    restarted = _budget(tmp_path, now, capacity=3, reserve=1)
    with pytest.raises(RequestBudgetExceeded):
        restarted.charge("CREATE", "create-3", slot="LIFE-USDT:BUY:1")
    restarted.charge("CANCEL", "cancel-1")
    with pytest.raises(RequestBudgetExceeded):
        restarted.charge("CANCEL", "cancel-2")


def test_slot_refresh_cooldown_and_window_expiry(tmp_path):
    now = [datetime(2026, 10, 6, tzinfo=timezone.utc)]
    budget = _budget(tmp_path, now)
    budget.charge("CREATE", "create-1", slot="LIFE-USDT:BUY:0")
    now[0] += timedelta(milliseconds=999)
    with pytest.raises(RequestBudgetExceeded, match="SLOT_REFRESH_TOO_SOON"):
        budget.charge("CREATE", "create-2", slot="LIFE-USDT:BUY:0")
    now[0] += timedelta(milliseconds=1101)
    budget.charge("CREATE", "create-2", slot="LIFE-USDT:BUY:0")


def test_status_cannot_consume_cancel_reserve(tmp_path):
    now = [datetime(2026, 10, 6, tzinfo=timezone.utc)]
    budget = _budget(tmp_path, now, capacity=2, reserve=1)
    budget.charge("STATUS", "status-1")
    with pytest.raises(RequestBudgetExceeded):
        budget.charge("STATUS", "status-2")
    budget.charge("CANCEL", "cancel-1")


def test_stale_instance_duplicate_id_and_clock_rollback_fail_closed(tmp_path):
    now = [datetime(2026, 10, 6, tzinfo=timezone.utc)]
    first = _budget(tmp_path, now)
    second = _budget(tmp_path, now)
    first.charge("CREATE", "create-1", slot="LIFE-USDT:BUY:0")
    with pytest.raises(ValueError, match="REQUEST_ALREADY_ACCOUNTED"):
        second.charge("CREATE", "create-1", slot="LIFE-USDT:BUY:0")
    now[0] -= timedelta(milliseconds=1)
    with pytest.raises(ValueError, match="REQUEST_BUDGET_CLOCK_ROLLBACK"):
        second.charge("CANCEL", "cancel-1")


def test_missing_or_changed_budget_policy_blocks_restore(tmp_path):
    now = [datetime(2026, 10, 6, tzinfo=timezone.utc)]
    budget = _budget(tmp_path, now)
    with pytest.raises(ValueError, match="REQUEST_BUDGET_UNAVAILABLE"):
        AccountRequestBudget(budget.path, account_uid="12345", policy=RequestBudgetPolicy(
            window_ms=2000, max_requests=4, cancel_reserve=1,
            min_slot_refresh_interval_ms=1000), clock=lambda: now[0])
    budget.path.unlink()
    with pytest.raises(ValueError, match="REQUEST_BUDGET_UNAVAILABLE"):
        budget.charge("CANCEL", "cancel-1")


def test_corrupt_budget_journal_blocks_restore(tmp_path):
    now = [datetime(2026, 10, 6, tzinfo=timezone.utc)]
    budget = _budget(tmp_path, now)
    budget.path.write_text("{broken")
    with pytest.raises(ValueError, match="REQUEST_BUDGET_UNAVAILABLE"):
        _budget(tmp_path, now)


def test_ambiguous_fsync_consumes_capacity_after_restart(tmp_path, monkeypatch):
    now = [datetime(2026, 10, 6, tzinfo=timezone.utc)]
    budget = _budget(tmp_path, now, capacity=2, reserve=1)
    original_save = budget._save

    def save_then_fail(state):
        original_save(state)
        raise OSError("directory fsync outcome unknown")

    monkeypatch.setattr(budget, "_save", save_then_fail)
    with pytest.raises(OSError, match="fsync outcome unknown"):
        budget.charge("CREATE", "create-1", slot="LIFE-USDT:BUY:0")
    restarted = _budget(tmp_path, now, capacity=2, reserve=1)
    with pytest.raises(RequestBudgetExceeded):
        restarted.charge("CREATE", "create-2", slot="LIFE-USDT:SELL:0")
    restarted.charge("CANCEL", "cancel-1")


@pytest.mark.asyncio
async def test_cancel_adapter_uses_shared_reserve_and_leaves_excess_wal_pending(tmp_path):
    now = [datetime(2026, 10, 6, tzinfo=timezone.utc)]
    budget = _budget(tmp_path, now, capacity=2, reserve=1)
    budget.charge("CREATE", "create-1", slot="LIFE-USDT:BUY:0")
    wal = IntentWAL(tmp_path / "intents.json")
    for number in (1, 2):
        wal.prepare(f"i{number}", client_order_id=f"wire-{number}",
                    session_id="s1", epoch=1, reservation_id=f"i{number}")
    connector = FakeOkx()
    gateway = OkxSpotOrderGateway(
        connector, wal, trading_pair="LIFE-USDT", clock=lambda: now[0],
        cancel_retry_policy=CancelRetryPolicy(1000, 2), request_budget=budget)

    assert await gateway.request_cancel("s1", 1) == 1
    assert connector.cancels == [("LIFE-USDT", "wire-1")]
    assert wal.get("i1").cancel_attempts == 1
    assert wal.get("i2").cancel_attempts == 0


def test_protected_create_charges_budget_at_final_send_boundary(tmp_path):
    now = [datetime(2026, 10, 6, tzinfo=timezone.utc)]
    budget = _budget(tmp_path, now, capacity=2, reserve=1)
    wal = IntentWAL(tmp_path / "intents.json")
    sent = []

    class Connector:
        client_order_id_prefix = "HBOT"
        client_order_id_max_length = 32

        def submit_protected_order(self, **kwargs):
            kwargs["pre_send_check"]({
                "clOrdId": kwargs["order_id"], "instId": "LIFE-USDT",
                "side": "buy", "ordType": "post_only", "tdMode": "cash",
                "px": "1", "sz": "1"})
            sent.append(kwargs["order_id"])
            return kwargs["order_id"]

    gateway = ProtectedSpotGateway(Connector(), wal, lambda _: True, request_budget=budget)
    for number in (1, 2):
        wire = gateway.allocate_client_order_id(side="BUY", trading_pair="LIFE-USDT")
        intent = f"i{number}"
        wal.begin(intent, client_order_id=wire, session_id="s1", epoch=1,
                  reservation_id=intent, slot_market="LIFE-USDT",
                  slot_side="BUY", slot_level=number)
        permit = SendPermit(intent, wire, intent, "s1", 1, 1, 1,
                            Decimal("1"), Decimal("1"))
        if number == 1:
            assert gateway.submit(permit, side="BUY", trading_pair="LIFE-USDT",
                                  order_type=OrderType.LIMIT_MAKER) == wire
        else:
            with pytest.raises(RequestBudgetExceeded):
                gateway.submit(permit, side="BUY", trading_pair="LIFE-USDT",
                               order_type=OrderType.LIMIT_MAKER)
            assert wal.get(intent).state == "SEND_UNKNOWN"
    assert len(sent) == 1


def test_rejected_budget_at_final_check_durably_releases_unsent_slot(tmp_path):
    now = [datetime(2026, 10, 6, tzinfo=timezone.utc)]
    budget = _budget(tmp_path, now, capacity=2, reserve=1)
    budget.charge("CREATE", "other-create", slot="OTHER-USDT:BUY:0")
    _, executor, connector, wal, ledger, _ = _setup(
        tmp_path, request_budget=budget, recovery_account_uid="12345")
    connector.before_send = lambda kwargs: kwargs["pre_send_check"]({
        "clOrdId": kwargs["order_id"], "instId": "LIFE-USDT", "side": "buy",
        "ordType": "post_only", "tdMode": "cash", "px": "1", "sz": "1"})

    with pytest.raises(RequestBudgetExceeded):
        executor.place_open_order()

    assert IntentWAL(wal.path).get("executor-1").state == "ABORTED_BEFORE_SEND"
    assert not ledger.has_open_intent("executor-1")
    assert ledger.is_terminal_intent("executor-1")
    wal.begin("next", client_order_id="wire-next", session_id="s1", epoch=1,
              reservation_id="next", slot_market="LIFE-USDT",
              slot_side="BUY", slot_level=0)


def test_budget_rejection_does_not_release_if_unsent_wal_commit_fails(tmp_path):
    now = [datetime(2026, 10, 6, tzinfo=timezone.utc)]
    budget = _budget(tmp_path, now, capacity=2, reserve=1)
    budget.charge("CREATE", "other-create", slot="OTHER-USDT:BUY:0")
    _, executor, connector, wal, ledger, _ = _setup(
        tmp_path, request_budget=budget, recovery_account_uid="12345")

    def reject_with_failed_wal_commit(kwargs):
        def fail(_records):
            raise OSError("abort journal unavailable")
        wal._save = fail
        kwargs["pre_send_check"]({
            "clOrdId": kwargs["order_id"], "instId": "LIFE-USDT", "side": "buy",
            "ordType": "post_only", "tdMode": "cash", "px": "1", "sz": "1"})

    connector.before_send = reject_with_failed_wal_commit
    with pytest.raises(OSError, match="abort journal unavailable"):
        executor.place_open_order()
    assert IntentWAL(wal.path).get("executor-1").state == "SEND_UNKNOWN"
    assert ledger.has_open_intent("executor-1")


def test_failure_after_budget_acceptance_keeps_possible_send_unresolved(tmp_path):
    now = [datetime(2026, 10, 6, tzinfo=timezone.utc)]
    budget = _budget(tmp_path, now)
    _, executor, connector, wal, ledger, _ = _setup(
        tmp_path, request_budget=budget, recovery_account_uid="12345")

    def fail_after_final_check(kwargs):
        kwargs["pre_send_check"]({
            "clOrdId": kwargs["order_id"], "instId": "LIFE-USDT", "side": "buy",
            "ordType": "post_only", "tdMode": "cash", "px": "1", "sz": "1"})
        raise TimeoutError("send outcome unknown")

    connector.before_send = fail_after_final_check
    with pytest.raises(TimeoutError, match="send outcome unknown"):
        executor.place_open_order()
    assert IntentWAL(wal.path).get("executor-1").state == "SEND_UNKNOWN"
    assert ledger.has_open_intent("executor-1")


@pytest.mark.asyncio
async def test_budget_rejection_after_real_okx_throttler_sends_no_request(tmp_path):
    now = [datetime(2026, 10, 6, tzinfo=timezone.utc)]
    budget = _budget(tmp_path, now, capacity=2, reserve=1)
    budget.charge("CREATE", "other-create", slot="OTHER-USDT:BUY:0")
    requests = []
    throttler = PausedThrottler()

    class Session:
        async def request(self, **kwargs):
            requests.append(kwargs)
            raise AssertionError("budget-rejected order reached network")

    connector = OkxExchange("key", "secret", "passphrase", trading_pairs=[], trading_required=False)
    connector._trading_rules["LIFE-USDT"] = TradingRule(
        trading_pair="LIFE-USDT", min_order_size=Decimal("0.1"),
        min_price_increment=Decimal("0.01"), min_base_amount_increment=Decimal("0.1"))
    connector._on_order_failure = Mock()
    connector._web_assistants_factory = SimpleNamespace(get_rest_assistant=AsyncMock(
        return_value=RESTAssistant(connection=RESTConnection(Session()), throttler=throttler)))
    connector._api_request_url = AsyncMock(return_value="https://www.okx.com/api/v5/trade/order")
    connector.exchange_symbol_associated_to_pair = AsyncMock(return_value="LIFE-USDT")
    _, executor, _, wal, ledger, _ = _setup(
        tmp_path, connector=connector, request_budget=budget, recovery_account_uid="12345")

    executor.place_open_order()
    await asyncio.wait_for(throttler.entered.wait(), timeout=2)
    throttler.release.set()
    for _ in range(50):
        if connector._on_order_failure.called:
            break
        await asyncio.sleep(0.01)

    assert connector._on_order_failure.called
    assert requests == []
    assert IntentWAL(wal.path).get("executor-1").state == "ABORTED_BEFORE_SEND"
    assert ledger.is_terminal_intent("executor-1")


@pytest.mark.parametrize("configured_uid", [None, "99999"])
def test_budget_uid_must_match_configured_recovery_uid(tmp_path, configured_uid):
    now = [datetime(2026, 10, 6, tzinfo=timezone.utc)]
    budget = _budget(tmp_path, now)
    with pytest.raises(ValueError, match="ORDER_SAFETY_REQUEST_BUDGET_UID_MISMATCH"):
        _setup(tmp_path, request_budget=budget, recovery_account_uid=configured_uid)
