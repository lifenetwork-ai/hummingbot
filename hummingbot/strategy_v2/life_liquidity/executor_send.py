"""Opt-in bridge from a LIFE OrderExecutor to protected spot send.

The LIFE controller does not install this bridge or grant trading permission at
runtime yet. A qualified reference-price provider and a live authorization
observer remain separate release gates.
"""

import re
from decimal import Decimal
from typing import Callable

from hummingbot.core.data_type.common import OrderType, TradeType
from hummingbot.strategy_v2.executors.order_executor.data_types import ExecutionStrategy, OrderExecutorConfig
from hummingbot.strategy_v2.life_liquidity.protected_send import ProtectedSpotGateway
from hummingbot.strategy_v2.life_liquidity.risk import ReservationLedger, SpotIntent
from hummingbot.strategy_v2.life_liquidity.send_gate import SendPermit
from hummingbot.strategy_v2.life_liquidity.slots import SpotQuoteSlots


class ProtectedSpotExecutorSender:
    def __init__(self, controller, gateway: ProtectedSpotGateway,
                 reservations: ReservationLedger, *, risk_epoch: Callable[[], int],
                 authorize: Callable[[SendPermit], bool],
                 reference_price: Callable[[], Decimal]):
        self.controller = controller
        self.gateway = gateway
        self.reservations = reservations
        self.risk_epoch = risk_epoch
        self.policy_authorize = authorize
        self.reference_price = reference_price
        self.manager = controller._order_safety_manager
        self.slots = SpotQuoteSlots(gateway.wal, market=controller.config.strategy.spot.pair)
        self._issued_intents: dict[str, SpotIntent] = {}
        self._issued_slots: dict[str, int] = {}
        self._attempted_intents: set[str] = set()
        self.gateway.authorize = self._authorized

    def _journals_match(self, records, *, pending_intent_id: str | None = None) -> bool:
        reservation_ids = self.reservations.reservation_ids
        required_ids = set()
        for record in records:
            if record.state == "PREPARED" and record.intent_id != pending_intent_id:
                return False
            if (record.state == "TERMINAL"
                    and not self.reservations.is_terminal_intent(record.reservation_id)):
                return False
            if record.state == "ABORTED_BEFORE_SEND":
                if record.reservation_id in reservation_ids:
                    if not self.reservations.is_terminal_intent(record.reservation_id):
                        return False
                    required_ids.add(record.reservation_id)
            else:
                if (record.state != "TERMINAL"
                        and record.reservation_id in reservation_ids
                        and self.reservations.is_terminal_intent(record.reservation_id)):
                    return False
                required_ids.add(record.reservation_id)
        return reservation_ids == required_ids

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
        if not self._journals_match(records, pending_intent_id=permit.intent_id):
            return False
        record = next((item for item in records if item.intent_id == permit.intent_id), None)
        if (record is None or record.slot_market != self.slots.market
                or record.slot_side != intent.side
                or record.slot_level != self._issued_slots.get(permit.intent_id)):
            return False
        planner = self.controller._quote_action_planner
        if planner is not None and not planner.authorizes_permit(permit):
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
        if (not isinstance(config.level_id, str)
                or re.fullmatch(r"0|[1-9][0-9]*", config.level_id) is None):
            raise ValueError("SLOT_LEVEL_INVALID")
        try:
            level = int(config.level_id)
        except ValueError as exc:
            raise ValueError("SLOT_LEVEL_INVALID") from exc
        if level >= len(self.controller.config.strategy.quotes.spreads_bps):
            raise ValueError("SLOT_LEVEL_UNCONFIGURED")
        if self.controller.allow_create_executor_actions() is not True:
            raise PermissionError("LIFE_TRADING_DISABLED")
        current = self.manager.current_session
        if (current is None or not self.manager.can_quote(
                reference_ready=True, all_gates_ready=True, market_reference_ready=True)):
            raise PermissionError("SESSION_PERMISSION_REVOKED")
        reservation_path = self.reservations.path
        if (reservation_path is None or not reservation_path.is_file()
                or reservation_path.is_symlink()):
            raise PermissionError("RESERVATION_JOURNAL_NOT_DURABLE")
        if config.id in self._attempted_intents:
            raise ValueError("RECONCILE_BEFORE_RETRY")
        records = self.gateway.wal.all_records()
        if any(item.intent_id == config.id for item in records):
            raise ValueError("RECONCILE_BEFORE_RETRY")
        if config.id in self.reservations.reservation_ids:
            raise PermissionError("RESERVATION_UNAVAILABLE")
        if not self._journals_match(records):
            raise PermissionError("RECOVERY_JOURNALS_DISAGREE")
        side = config.side.name
        intent = SpotIntent(config.id, side, amount, price, current.session_id, current.epoch)
        wire_id = self.gateway.allocate_client_order_id(side=side, trading_pair=pair)
        permit = SendPermit(config.id, wire_id, config.id, current.session_id, current.epoch,
                            current.config_version, self.risk_epoch(), price, amount)
        # A synchronous failure can occur after either journal commit.
        # Never infer from the exception that a connector request was absent.
        self._attempted_intents.add(config.id)
        self.slots.claim(intent_id=config.id, client_order_id=wire_id,
                         reservation_id=config.id, session_id=current.session_id,
                         epoch=current.epoch, side=side, level=level)
        decision = self.reservations.reserve(intent, reference_price=self.reference_price())
        if not decision.allowed:
            self.gateway.wal.abort_before_send(config.id)
            raise PermissionError("RESERVATION_UNAVAILABLE")
        self._issued_intents[config.id] = intent
        self._issued_slots[config.id] = level

        def release_rejected_send() -> None:
            # Called only by the connector's final check before REST request I/O.
            # If either durable transition fails, the slot remains unavailable
            # until recovery can prove the complete journal state.
            self.gateway.wal.abort_rejected_at_send_boundary(
                config.id, client_order_id=wire_id,
                session_id=current.session_id, epoch=current.epoch)
            self.reservations.abort_unsent(
                config.id, session_id=current.session_id,
                epoch=current.epoch, wal=self.gateway.wal)

        try:
            return self.gateway.submit(permit, side=side, trading_pair=pair,
                                       order_type=order_type,
                                       on_unsent_budget_rejection=release_rejected_send)
        except Exception:
            # The gateway arms SEND_UNKNOWN before touching the connector. Only
            # a durable PREPARED record proves that no request was enqueued.
            # If an fsync error left memory behind disk, abort_before_send
            # refuses the stale snapshot and the reservation stays locked.
            try:
                if self.gateway.wal.get(config.id).state == "PREPARED":
                    self.gateway.wal.abort_before_send(config.id)
                    self.reservations.abort_unsent(
                        config.id, session_id=current.session_id,
                        epoch=current.epoch, wal=self.gateway.wal)
            except (KeyError, OSError, ValueError):
                pass  # startup recovery retains or repairs uncertain exposure
            raise
