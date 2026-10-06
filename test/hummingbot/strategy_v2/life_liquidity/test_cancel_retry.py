"""Persisted, bounded spot cancellation retries preserve capacity for fresh orders."""

import json
from datetime import datetime, timedelta, timezone
from test.hummingbot.strategy_v2.life_liquidity.test_order_gateway import FakeOkx

import pytest

from hummingbot.strategy_v2.life_liquidity.order_gateway import CancelRetryPolicy, OkxSpotOrderGateway
from hummingbot.strategy_v2.life_liquidity.state import IntentWAL


def _wal(tmp_path, count=3):
    wal = IntentWAL(tmp_path / "intents.json")
    for number in range(1, count + 1):
        wal.prepare(f"i{number}", client_order_id=f"wire-{number}",
                    session_id="s1", epoch=1, reservation_id=f"i{number}")
    return wal


def _gateway(connector, wal, current, *, batch=2, retry_ms=1000):
    return OkxSpotOrderGateway(
        connector, wal, trading_pair="LIFE-USDT", clock=lambda: current[0],
        cancel_retry_policy=CancelRetryPolicy(retry_interval_ms=retry_ms,
                                              max_requests_per_cycle=batch))


@pytest.mark.asyncio
async def test_fresh_cancellations_outrank_retries_and_batch_is_bounded(tmp_path):
    current = [datetime(2026, 10, 5, tzinfo=timezone.utc)]
    wal = _wal(tmp_path)
    connector = FakeOkx()
    connector.status["wire-1"] = {"clOrdId": "wire-1", "ordId": "exchange-1",
                                  "state": "live", "accFillSz": "0"}
    wal.mark_cancel_attempt("i1", at=current[0] - timedelta(seconds=2))
    gateway = _gateway(connector, wal, current)

    await gateway.request_cancel("s1", 1)
    assert connector.cancels == [("LIFE-USDT", "wire-2"), ("LIFE-USDT", "wire-3")]
    assert IntentWAL(wal.path).get("i1").cancel_attempts == 1
    assert IntentWAL(wal.path).get("i2").cancel_attempts == 1
    await gateway.request_cancel("s1", 1)
    assert connector.cancels[-1] == ("LIFE-USDT", "wire-1")
    assert IntentWAL(wal.path).get("i1").cancel_attempts == 2
    await gateway.request_cancel("s1", 1)
    assert len(connector.cancels) == 3


@pytest.mark.asyncio
async def test_restart_keeps_retry_deadline_and_terminal_status_avoids_recancel(tmp_path):
    current = [datetime(2026, 10, 5, tzinfo=timezone.utc)]
    wal = _wal(tmp_path, count=1)
    connector = FakeOkx()
    connector.status["wire-1"] = {"clOrdId": "wire-1", "ordId": "exchange-1",
                                  "state": "live", "accFillSz": "0"}
    await _gateway(connector, wal, current).request_cancel("s1", 1)
    restarted = _gateway(connector, IntentWAL(wal.path), current)
    await restarted.request_cancel("s1", 1)
    assert connector.cancels == [("LIFE-USDT", "wire-1")]
    current[0] += timedelta(seconds=1)
    connector.status["wire-1"]["state"] = "canceled"
    await restarted.request_cancel("s1", 1)
    assert connector.cancels == [("LIFE-USDT", "wire-1")]
    assert restarted.wal.get("i1").state != "TERMINAL"  # status alone cannot release risk
    assert restarted.wal.get("i1").exchange_terminal_observed
    connector.fail_status = True
    current[0] += timedelta(seconds=1)
    await _gateway(connector, IntentWAL(wal.path), current).request_cancel("s1", 1)
    assert connector.cancels == [("LIFE-USDT", "wire-1")]


@pytest.mark.asyncio
async def test_failed_cancel_does_not_starve_other_fresh_cancels(tmp_path):
    current = [datetime(2026, 10, 5, tzinfo=timezone.utc)]
    wal = _wal(tmp_path, count=2)
    connector = FakeOkx()

    async def cancel(pair, wire_id):
        connector.cancels.append((pair, wire_id))
        return wire_id != "wire-1"

    connector.cancel_by_client_id = cancel
    gateway = _gateway(connector, wal, current)
    with pytest.raises(IOError, match="CANCEL_ACK_UNAVAILABLE"):
        await gateway.request_cancel("s1", 1)
    assert connector.cancels == [("LIFE-USDT", "wire-1"), ("LIFE-USDT", "wire-2")]
    assert IntentWAL(wal.path).get("i1").cancel_attempts == 1
    assert IntentWAL(wal.path).get("i2").cancel_attempts == 1
    await gateway.request_cancel("s1", 1)
    assert len(connector.cancels) == 2


@pytest.mark.asyncio
async def test_clock_rollback_blocks_retry_without_erasing_pending_intent(tmp_path):
    current = [datetime(2026, 10, 5, tzinfo=timezone.utc)]
    wal = _wal(tmp_path, count=1)
    connector = FakeOkx()
    await _gateway(connector, wal, current).request_cancel("s1", 1)
    current[0] -= timedelta(seconds=1)
    with pytest.raises(ValueError, match="CANCEL_CLOCK_ROLLBACK"):
        await _gateway(connector, IntentWAL(wal.path), current).request_cancel("s1", 1)
    assert len(connector.cancels) == 1
    assert IntentWAL(wal.path).get("i1").cancel_attempts == 1


def test_corrupt_cancel_retry_metadata_blocks_wal_restore(tmp_path):
    wal = _wal(tmp_path, count=1)
    raw = json.loads(wal.path.read_text())
    raw["records"]["i1"]["cancel_attempts"] = "100"
    wal.path.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="intent WAL cancel state invalid"):
        IntentWAL(wal.path)


@pytest.mark.asyncio
async def test_shared_safety_cycle_budget_carries_across_session_scopes(tmp_path):
    current = [datetime(2026, 10, 5, tzinfo=timezone.utc)]
    wal = _wal(tmp_path, count=1)
    wal.prepare("i2", client_order_id="wire-2", session_id="s2",
                epoch=2, reservation_id="i2")
    connector = FakeOkx()
    gateway = _gateway(connector, wal, current, batch=2)
    remaining = 1
    remaining -= await gateway.request_cancel("s1", 1, max_requests=remaining)
    remaining -= await gateway.request_cancel("s2", 2, max_requests=remaining)
    assert remaining == 0
    assert connector.cancels == [("LIFE-USDT", "wire-1")]
    assert not wal.get("i2").cancel_requested


@pytest.mark.asyncio
async def test_fresh_order_in_later_scope_outranks_earlier_scope_retry(tmp_path):
    current = [datetime(2026, 10, 5, tzinfo=timezone.utc)]
    wal = _wal(tmp_path, count=1)
    wal.prepare("i2", client_order_id="wire-2", session_id="s2",
                epoch=2, reservation_id="i2")
    wal.mark_cancel_attempt("i1", at=current[0] - timedelta(seconds=2))
    connector = FakeOkx()
    gateway = _gateway(connector, wal, current, batch=1)
    assert await gateway.request_cancel_scopes((("s1", 1), ("s2", 2))) == 1
    assert connector.cancels == [("LIFE-USDT", "wire-2")]
    assert wal.get("i1").cancel_attempts == 1
