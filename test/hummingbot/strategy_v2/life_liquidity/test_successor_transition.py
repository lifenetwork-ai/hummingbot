"""P3.9 cancels and reconciles the old epoch before permitting its successor."""

from decimal import Decimal
from test.hummingbot.strategy_v2.life_liquidity.test_reference_transition import manager, start
from test.hummingbot.strategy_v2.life_liquidity.test_session import FakeClock

import pytest

from hummingbot.strategy_v2.life_liquidity.session import OrderReconciliation, SessionStore
from hummingbot.strategy_v2.life_liquidity.transition import advance_successor


class FakeOrderGateway:
    def __init__(self, store, clock):
        self.store = store
        self.clock = clock
        self.pending = ("old-order",)
        self.cancel_requests = []
        self.fail_cancel = False

    async def request_cancel(self, session_id, epoch):
        journal = self.store.load()
        assert journal.state == "TRANSITIONING"
        assert journal.transition_started_at is not None
        self.cancel_requests.append((session_id, epoch))
        if self.fail_cancel:
            raise OSError("cancel transport unavailable")

    async def reconcile(self, session_id, epoch):
        return OrderReconciliation(
            session_id=session_id, epoch=epoch, observed_at=self.clock.wall,
            scope_complete=True, open_order_ids=(), pending_cancel_ids=self.pending,
            unknown_order_ids=(), trade_events_reconciled=True,
        )


async def advance(active, gateway, anchor=Decimal("1.01")):
    return await advance_successor(
        active, gateway, reference_ready=True, all_gates_ready=True,
        market_reference_ready=True, market_anchor_usdt=anchor,
    )


@pytest.mark.asyncio
async def test_cancel_request_is_after_durable_revoke_and_pending_cancel_blocks(tmp_path):
    clock = FakeClock()
    active = manager(tmp_path, clock)
    primary = start(active)
    gateway = FakeOrderGateway(SessionStore(tmp_path / "transition.json"), clock)
    clock.advance(10)
    assert await advance(active, gateway) == "TRANSITIONING"
    assert gateway.cancel_requests == [(primary.session_id, primary.epoch)]
    assert not active.can_quote(reference_ready=True, all_gates_ready=True)
    gateway.pending = ()
    restarted = manager(tmp_path, clock)
    assert await advance(restarted, gateway) == "ACTIVE"
    assert restarted.current_session.epoch == primary.epoch + 1
    assert restarted.can_quote(reference_ready=True, all_gates_ready=True,
                               market_reference_ready=True)
    assert await advance(restarted, gateway) == "ACTIVE"
    assert len(gateway.cancel_requests) == 2


@pytest.mark.asyncio
async def test_cancel_transport_failure_stays_persistently_transitioning(tmp_path):
    clock = FakeClock()
    active = manager(tmp_path, clock)
    start(active)
    gateway = FakeOrderGateway(SessionStore(tmp_path / "transition.json"), clock)
    gateway.fail_cancel = True
    clock.advance(10)
    assert await advance(active, gateway) == "TRANSITIONING"
    assert active.reason_code == "CANCEL_REQUEST_FAILED"
    restarted = manager(tmp_path, clock)
    assert restarted.state == "TRANSITIONING"
    assert not restarted.can_quote(reference_ready=True, all_gates_ready=True)


@pytest.mark.asyncio
async def test_reconciled_orders_do_not_override_failed_market_reference(tmp_path):
    clock = FakeClock()
    active = manager(tmp_path, clock)
    start(active)
    gateway = FakeOrderGateway(SessionStore(tmp_path / "transition.json"), clock)
    gateway.pending = ()
    clock.advance(10)
    assert await advance(active, gateway, anchor=None) == "TRANSITIONING"
    assert active.reason_code == "MARKET_REFERENCE_UNAVAILABLE"
    assert active.current_session.reference_mode == "bounded_benchmark"
    assert not active.can_quote(reference_ready=True, all_gates_ready=True)
