"""Opt-in bridge from a pre-reserved LIFE OrderExecutor to protected spot send.

The LIFE controller does not install this bridge or grant trading permission at
runtime yet. A quote engine must first create a matching durable reservation,
and a live authorization observer remains a separate release gate.
"""

from decimal import Decimal
from typing import Callable

from hummingbot.core.data_type.common import OrderType, TradeType
from hummingbot.strategy_v2.executors.order_executor.data_types import ExecutionStrategy, OrderExecutorConfig
from hummingbot.strategy_v2.life_liquidity.protected_send import ProtectedSpotGateway
from hummingbot.strategy_v2.life_liquidity.risk import ReservationLedger, SpotIntent
from hummingbot.strategy_v2.life_liquidity.send_gate import SendPermit


class ProtectedSpotExecutorSender:
    def __init__(self, controller, gateway: ProtectedSpotGateway,
                 reservations: ReservationLedger, *, risk_epoch: Callable[[], int],
                 authorize: Callable[[SendPermit], bool]):
        self.controller = controller
        self.gateway = gateway
        self.reservations = reservations
        self.risk_epoch = risk_epoch
        self.policy_authorize = authorize
        self.manager = controller._order_safety_manager
        self._issued_intents: dict[str, SpotIntent] = {}
        self._attempted_intents: set[str] = set()
        self.gateway.authorize = self._authorized

    def _authorized(self, permit: SendPermit) -> bool:
        current = self.manager.current_session
        if (self.controller.allow_create_executor_actions() is not True
                or current is None or current.session_id != permit.session_id
                or current.epoch != permit.epoch
                or current.config_version != permit.config_version
                or not self.manager.can_quote(reference_ready=True, all_gates_ready=True,
                                              market_reference_ready=True)
                or self.risk_epoch() != permit.risk_epoch):
            return False
        intent = self._issued_intents.get(permit.intent_id)
        if (intent is None or intent.quantity_base != permit.quantity_base
                or intent.limit_price_usdt != permit.price_usdt
                or intent.session_id != permit.session_id or intent.epoch != permit.epoch
                or permit.reservation_id != permit.intent_id
                or not self.reservations.matches_open_intent(intent)):
            return False
        records = self.gateway.wal.all_records()
        record_ids = {record.reservation_id for record in records}
        expected_ids = (record_ids if permit.intent_id in {record.intent_id for record in records}
                        else record_ids | {permit.reservation_id})
        if self.reservations.reservation_ids != expected_ids:
            return False
        decision = self.policy_authorize(permit)
        return decision is True or getattr(decision, "allowed", False) is True

    def submit(self, config: OrderExecutorConfig, *, amount: Decimal,
               price: Decimal, order_type: OrderType) -> str:
        pair = self.controller.config.strategy.spot.pair
        connector_name = self.controller.config.strategy.spot.connector
        if (not isinstance(config, OrderExecutorConfig) or not config.id
                or config.controller_id != self.controller.config.id
                or config.connector_name != connector_name or config.trading_pair != pair
                or config.execution_strategy != ExecutionStrategy.LIMIT_MAKER
                or order_type != OrderType.LIMIT_MAKER
                or config.side not in (TradeType.BUY, TradeType.SELL)
                or not isinstance(amount, Decimal) or not amount.is_finite()
                or amount <= 0 or amount != config.amount
                or not isinstance(price, Decimal) or not price.is_finite() or price <= 0):
            raise ValueError("PROTECTED_EXECUTOR_ORDER_INVALID")
        if self.controller.allow_create_executor_actions() is not True:
            raise PermissionError("LIFE_TRADING_DISABLED")
        current = self.manager.current_session
        if (current is None or not self.manager.can_quote(
                reference_ready=True, all_gates_ready=True, market_reference_ready=True)):
            raise PermissionError("SESSION_PERMISSION_REVOKED")
        if config.id in self._attempted_intents:
            raise ValueError("RECONCILE_BEFORE_RETRY")
        records = self.gateway.wal.all_records()
        if any(item.intent_id == config.id for item in records):
            raise ValueError("RECONCILE_BEFORE_RETRY")
        side = config.side.name
        intent = SpotIntent(config.id, side, amount, price, current.session_id, current.epoch)
        if not self.reservations.matches_open_intent(intent):
            raise PermissionError("RESERVATION_UNAVAILABLE")
        if self.reservations.reservation_ids != {
                item.reservation_id for item in records} | {config.id}:
            raise PermissionError("RECOVERY_JOURNALS_DISAGREE")
        wire_id = self.gateway.allocate_client_order_id(side=side, trading_pair=pair)
        permit = SendPermit(config.id, wire_id, config.id, current.session_id, current.epoch,
                            current.config_version, self.risk_epoch(), price, amount)
        self._issued_intents[config.id] = intent
        # A synchronous failure can occur after WAL persistence but before
        # the caller receives an ID. Never infer from the exception that the
        # connector did not enqueue a request.
        self._attempted_intents.add(config.id)
        return self.gateway.submit(permit, side=side, trading_pair=pair,
                                   order_type=order_type)
