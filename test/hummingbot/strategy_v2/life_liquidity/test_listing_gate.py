"""Pre-listing metadata checks use a deterministic clock and no exchange access."""

import asyncio
from decimal import Decimal

import pytest

from hummingbot.strategy_v2.life_liquidity.market_data import ListingGate


def instruments(*items):
    return {"code": "0", "data": list(items)}


def live_life(**changes):
    item = {"instType": "SPOT", "instId": "LIFE-USDT", "state": "live",
            "tickSz": "0.0001", "lotSz": "0.1", "minSz": "1"}
    item.update(changes)
    return item


@pytest.mark.asyncio
async def test_absent_life_stays_waiting_and_retries_with_capped_backoff():
    calls = []

    async def fetch():
        calls.append(True)
        return instruments({"instType": "SPOT", "instId": "BTC-USDT"})

    gate = ListingGate(inst_type="SPOT", inst_id="LIFE-USDT", base_delay=5, max_delay=20)
    assert gate.state == "WAITING_READY"
    assert gate.instrument_found is False
    assert await gate.poll(0, fetch) is False
    assert gate.reason_code == "INSTRUMENT_NOT_FOUND"
    assert gate.next_refresh_at == 5
    assert await gate.poll(4.99, fetch) is False
    assert len(calls) == 1
    assert await gate.poll(5, fetch) is False
    assert gate.next_refresh_at == 15
    assert await gate.poll(15, fetch) is False
    assert gate.next_refresh_at == 35
    assert await gate.poll(35, fetch) is False
    assert gate.next_refresh_at == 55
    assert len(calls) == 4


@pytest.mark.asyncio
async def test_exact_instrument_match_resets_backoff_without_enabling_orders():
    responses = [
        instruments({"instType": "SPOT", "instId": "LIFE-USDC"}),
        instruments(live_life()),
    ]

    async def fetch():
        return responses.pop(0)

    gate = ListingGate(inst_type="SPOT", inst_id="LIFE-USDT", base_delay=5, max_delay=20)
    assert await gate.poll(0, fetch) is False
    assert await gate.poll(5, fetch) is True
    assert gate.instrument_found is True
    assert gate.state == "WAITING_READY"
    assert gate.reason_code == "INSTRUMENT_FOUND_PENDING_DATA_GATES"
    assert gate.instrument_rules.tick_size == Decimal("0.0001")
    assert gate.metadata_ready is True
    assert gate.next_refresh_at == 10


@pytest.mark.asyncio
async def test_bad_response_or_failure_never_looks_like_a_listed_instrument():
    responses = [instruments({"instType": "SPOT", "instId": "LIFE-USDT"}), {"data": []}]

    async def fetch():
        return responses.pop(0)

    gate = ListingGate(inst_type="SPOT", inst_id="LIFE-USDT", base_delay=5)
    assert await gate.poll(0, fetch) is True
    assert await gate.poll(5, fetch) is False
    assert gate.reason_code == "INSTRUMENT_RESPONSE_INVALID"
    assert gate.instrument_found is False

    async def failing_fetch():
        raise OSError("exchange unavailable")

    assert await gate.poll(15, failing_fetch) is False
    assert gate.reason_code == "INSTRUMENT_FETCH_ERROR"
    assert gate.state == "WAITING_READY"


@pytest.mark.asyncio
async def test_failed_refresh_revokes_a_previously_found_instrument_while_request_is_pending():
    request_started = asyncio.Event()
    finish_request = asyncio.Event()

    async def found():
        return instruments({"instType": "SPOT", "instId": "LIFE-USDT"})

    async def delayed_failure():
        request_started.set()
        await finish_request.wait()
        raise OSError("metadata feed disconnected")

    gate = ListingGate(inst_type="SPOT", inst_id="LIFE-USDT", base_delay=5)
    assert await gate.poll(0, found) is True
    pending = asyncio.create_task(gate.poll(5, delayed_failure))
    await request_started.wait()
    assert gate.instrument_found is False
    finish_request.set()
    assert await pending is False
    assert gate.reason_code == "INSTRUMENT_FETCH_ERROR"


@pytest.mark.asyncio
async def test_cancelled_poll_propagates_and_invalid_clock_is_rejected():
    async def cancelled_fetch():
        raise asyncio.CancelledError

    gate = ListingGate(inst_type="SPOT", inst_id="LIFE-USDT")
    with pytest.raises(asyncio.CancelledError):
        await gate.poll(0, cancelled_fetch)
    with pytest.raises(ValueError, match="monotonic"):
        await gate.poll(float("nan"), cancelled_fetch)


@pytest.mark.parametrize("field,value", [
    ("tickSz", None), ("lotSz", ""), ("minSz", None),
    ("tickSz", "0"), ("lotSz", "-1"), ("minSz", "NaN"),
    ("tickSz", "Infinity"), ("minSz", "not-a-number"),
])
@pytest.mark.asyncio
async def test_missing_or_invalid_trading_rule_keeps_market_unready(field, value):
    metadata = live_life()
    if value is None:
        metadata.pop(field)
    else:
        metadata[field] = value

    async def fetch():
        return instruments(metadata)

    gate = ListingGate(inst_type="SPOT", inst_id="LIFE-USDT")
    assert await gate.poll(0, fetch) is True
    assert gate.instrument_found is True
    assert gate.instrument_rules is None
    assert gate.metadata_ready is False
    assert gate.reason_code == "INSTRUMENT_RULES_INVALID"


@pytest.mark.parametrize("state", ["suspend", "preopen", "post_only", "", None])
@pytest.mark.asyncio
async def test_only_live_instrument_state_passes_metadata_gate(state):
    async def fetch():
        return instruments(live_life(state=state))

    gate = ListingGate(inst_type="SPOT", inst_id="LIFE-USDT")
    assert await gate.poll(0, fetch) is True
    assert gate.instrument_rules is not None
    assert gate.metadata_ready is False
    assert gate.reason_code == "INSTRUMENT_NOT_LIVE"
