"""P3 successor transition against an authoritative, session-scoped order gateway.

The gateway must request cancellation idempotently and reconcile exchange
orders, executor states, and fills for the complete old session/epoch. The
concrete connector adapter and final order-send gate are P4/P5 work.
"""

from decimal import Decimal
from typing import Protocol

from hummingbot.strategy_v2.life_liquidity.session import OrderReconciliation, SessionManager


class OldOrderGateway(Protocol):
    async def request_cancel(self, session_id: str, epoch: int) -> None:
        """Request cancellation of every order attributed to the old epoch."""

    async def reconcile(self, session_id: str, epoch: int) -> OrderReconciliation:
        """Return a complete authoritative view, including pending and unknown orders."""


async def advance_successor(manager: SessionManager, gateway: OldOrderGateway, *,
                            reference_ready: bool, all_gates_ready: bool,
                            market_reference_ready: bool,
                            market_anchor_usdt: Decimal | None) -> str:
    """Persist revocation, request cancels, reconcile, then consider activation."""
    state = manager.tick(reference_ready=reference_ready, all_gates_ready=all_gates_ready,
                         market_reference_ready=market_reference_ready,
                         market_anchor_usdt=market_anchor_usdt)
    if state != "TRANSITIONING":
        return state
    primary = manager.current_session
    try:
        await gateway.request_cancel(primary.session_id, primary.epoch)
    except Exception:
        manager.record_transition_failure("CANCEL_REQUEST_FAILED")
        return manager.state
    try:
        reconciliation = await gateway.reconcile(primary.session_id, primary.epoch)
    except Exception:
        manager.record_transition_failure("RECONCILIATION_FETCH_FAILED")
        return manager.state
    return manager.tick(reference_ready=reference_ready, all_gates_ready=all_gates_ready,
                        reconciliation=reconciliation, market_reference_ready=market_reference_ready,
                        market_anchor_usdt=market_anchor_usdt)
