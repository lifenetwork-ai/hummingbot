"""Apply an identified close fill to its held checkpoint in the fill transaction."""
from hummingbot.model.position import Position


def record_cleanup_intent(session, scope, order):
    from hummingbot.core.admin_identity import Principal
    from hummingbot.model.admin_access import AdminAccessStore, AdminAuditEvent
    accepted = session.query(AdminAuditEvent).filter(AdminAuditEvent.operation_id == scope.operation_id,
                                                     AdminAuditEvent.action == "credentials_disconnect", AdminAuditEvent.outcome == "accepted").one()
    actor = Principal(accepted.actor_id, accepted.actor_kind, accepted.actor_role, accepted.actor_email)
    access = AdminAccessStore(session.get_bind(), accepted.bot_id)
    access.audit(session.connection(), actor=actor, action="credentials_close_submitted", outcome="accepted",
                 request_id=accepted.request_id, operation_id=scope.operation_id, exchange=accepted.exchange,
                 connector=order.market, environment=accepted.environment, target_id=order.id,
                 event_key=scope.operation_id + ":close:" + order.id,
                 details={"version": scope.credential_version, "order_id": order.id, "position_id": scope.position_id,
                 "pair": scope.pair, "position_side": scope.position_side, "amount": str(scope.amount)})


def apply_cleanup_fill(session, order, fill):
    if order.cleanup_operation_id is None:
        return
    from sqlalchemy import func

    from hummingbot.model.bot_state import BotState
    from hummingbot.model.trade_fill import TradeFill
    from hummingbot.strategy_v2.executors.executor_orchestrator import PositionHold
    active = session.query(BotState.bot_id).filter(
        BotState.recovery_state["credential_operation"]["id"].astext == order.cleanup_operation_id,
        BotState.recovery_state["credential_operation"]["action"].astext == "credentials_disconnect").first()
    if not active:
        raise ValueError("Cleanup fill requires reconciliation after its operation ended")
    scope = order.cleanup_scope or {}
    position = session.query(Position).filter(Position.id == scope.get("position_id")).with_for_update().one_or_none()
    executed = session.query(func.sum(TradeFill.amount)).filter(TradeFill.market == order.market,
                                                                TradeFill.order_id == order.id).scalar()
    side = "LONG" if position is not None and position.side == "BUY" else "SHORT"
    if (position is None or position.side not in ("BUY", "SELL") or position.connector_name != "okx_perpetual" or order.market != position.connector_name
            or order.symbol != position.trading_pair or fill.symbol != position.trading_pair
            or order.position != "CLOSE" or fill.position != "CLOSE" or scope.get("position_side") != side
            or fill.trade_type != ("SELL" if side == "LONG" else "BUY")
            or not fill.amount.is_finite() or not 0 < fill.amount <= position.amount
            or executed is None or not 0 < executed <= order.amount):
        raise ValueError("Cleanup fill does not reduce its identified position")
    held = PositionHold(position.connector_name, position.trading_pair)
    held.net_amount_base = position.amount if side == "LONG" else -position.amount
    held.avg_entry_price = position.breakeven_price
    held.realized_pnl_quote = position.realized_pnl_quote
    held._process_order(fill.trade_type == "BUY", fill.amount, fill.amount * fill.price)
    position.amount = abs(held.net_amount_base)
    position.breakeven_price = held.avg_entry_price
    position.realized_pnl_quote = held.realized_pnl_quote
    position.unrealized_pnl_quote = (fill.price - held.avg_entry_price) * held.net_amount_base
    position.volume_traded_quote += fill.amount * fill.price
    if fill.trade_fee_in_quote is not None:
        position.cum_fees_quote += fill.trade_fee_in_quote
    position.timestamp = max(position.timestamp, fill.timestamp)
