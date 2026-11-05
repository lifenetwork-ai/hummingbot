"""Narrow Disconnect close authority; it cannot authorize a quote or account change."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from decimal import Decimal

from sqlalchemy import select

from hummingbot.core.data_type.common import OrderType, PositionAction, PositionMode, TradeType
from hummingbot.core.order_submission_guard import SubmissionBlocked
from hummingbot.model.exchange_credential import ExchangeCredential
from hummingbot.model.order import Order
from hummingbot.model.position import Position

_scope = ContextVar("credential_cleanup_order", default=None)


@dataclass(frozen=True)
class CleanupOrderScope:
    operation_id: str
    control_version: int
    owner_generation: int
    position_id: str
    pair: str
    position_side: str
    amount: Decimal
    credential_version: int
    deadline_ms: int

    def json(self):
        return {**asdict(self), "amount": str(self.amount)}


@contextmanager
def cleanup_order_scope(scope):
    if not isinstance(scope, CleanupOrderScope):
        raise ValueError("Cleanup requires explicit immutable close scope")
    token = _scope.set(scope)
    try:
        yield
    finally:
        _scope.reset(token)


def current_cleanup_scope():
    return _scope.get()


def validate_close(owner, row, connector, order, scope, *, final=False):
    owner._check_shutdown()
    operation = (row["recovery_state"] or {}).get("credential_operation") or {}
    now_ms = int(row["database_now"] * 1000)
    if (scope.operation_id != owner.cleanup_operation_id or scope.operation_id != operation.get("id")
            or operation.get("action") != "credentials_disconnect" or operation.get("stage") != "closing_positions"
            or operation.get("approved_generation") != owner.generation or scope.owner_generation != owner.generation
            or scope.control_version != row["control_version"] or row["desired_state"] != "stopped"
            or (row["recovery_state"] or {}).get("pending_mutations") or (row["recovery_state"] or {}).get("shutdown_incomplete")
            or connector.name != "okx_perpetual" or connector.position_mode != PositionMode.HEDGE
            or order.position != PositionAction.CLOSE or order.order_type != OrderType.MARKET
            or scope.position_side not in ("LONG", "SHORT") or not scope.pair.endswith("-USDT")
            or scope.pair != order.trading_pair or scope.amount != order.amount or not scope.amount.is_finite() or scope.amount <= 0
            or order.trade_type != (TradeType.SELL if scope.position_side == "LONG" else TradeType.BUY)
            or owner.cleanup_versions.get(connector.name) != scope.credential_version
            or operation.get("affected_versions", {}).get(connector.name) != scope.credential_version
            or not now_ms < scope.deadline_ms <= now_ms + 5000):
        raise SubmissionBlocked("Disconnect close authority is unavailable, expired or changed")
    with owner.engine.connect() as connection:
        credential = connection.execute(select(ExchangeCredential.version, ExchangeCredential.state).where(
            ExchangeCredential.bot_id == owner.store.bot_id, ExchangeCredential.exchange == operation.get("exchange"),
            ExchangeCredential.connector == connector.name, ExchangeCredential.environment == operation.get("environment"))).first()
        position = connection.execute(select(Position).where(Position.id == scope.position_id)).mappings().first()
        if (credential is None or credential.version != scope.credential_version or credential.state not in ("active", "revoking")
                or position is None or position["connector_name"] != connector.name or position["trading_pair"] != scope.pair
                or position["side"] != ("BUY" if scope.position_side == "LONG" else "SELL") or position["amount"] < scope.amount):
            raise SubmissionBlocked("Disconnect position/credential proof changed")
        if final:
            persisted = connection.execute(select(Order.cleanup_operation_id, Order.cleanup_scope).where(Order.id == order.client_order_id)).first()
            if persisted is None or persisted.cleanup_operation_id != scope.operation_id or persisted.cleanup_scope != scope.json():
                raise SubmissionBlocked("Disconnect order has no matching durable scope")
        else:
            live = connection.execute(select(Order.id).where(Order.cleanup_operation_id == scope.operation_id,
                                                             Order.cleanup_scope["position_id"].astext == scope.position_id,
                                                             Order.last_status.notin_(("BuyOrderCompleted", "SellOrderCompleted", "OrderCancelled", "OrderFailure", "OrderExpired"))).limit(1)).first()
            if live:
                raise SubmissionBlocked("A previous Disconnect close must be reconciled first")


def validate_payload(connector, order, scope, data):
    expected = dict(clOrdId=order.client_order_id, instId=scope.pair + "-SWAP", tdMode="cross", ordType="market",
                    side="sell" if scope.position_side == "LONG" else "buy", posSide=scope.position_side.lower())
    try:
        if (set(data) != set(expected) | {"sz"} or any(data.get(key) != value for key, value in expected.items())
                or connector._format_size_to_amount(scope.pair, Decimal(str(data["sz"]))) != scope.amount):
            raise ValueError
    except Exception:
        raise SubmissionBlocked("Disconnect close payload differs from its durable scope") from None
