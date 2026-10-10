"""Opt-in SWAP executor boundary. Capital approval and recovery are external gates.

Issuance is durable, sends are single attempt, and ACK never releases exposure.
Restoring the journal retains unresolved claims but does not restore send authority.
"""

import re
import uuid
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum
from pathlib import Path
from typing import Callable

from hummingbot.core.data_type.common import OrderType, PositionAction, PositionMode, TradeType
from hummingbot.strategy_v2.executors.order_executor.data_types import ExecutionStrategy, OrderExecutorConfig
from hummingbot.strategy_v2.life_liquidity.market_data import LinearSwapContract
from hummingbot.strategy_v2.life_liquidity.policy_state import PolicyState
from hummingbot.strategy_v2.life_liquidity.request_budget import AccountRequestBudget
from hummingbot.strategy_v2.life_liquidity.send_gate import SendPermit
from hummingbot.strategy_v2.life_liquidity.state import IntentWAL
from hummingbot.strategy_v2.models.executor_actions import CreateExecutorAction


@dataclass(frozen=True)
class SwapAccountObservation:
    account_uid: str
    account_mode: str
    position_mode: PositionMode
    margin_mode: str
    leverage: int
    observed_at_ms: int
    net_contracts: Decimal
    long_contracts: Decimal
    short_contracts: Decimal
    pending_close_buy_contracts: Decimal
    pending_close_sell_contracts: Decimal
    connector_ready: bool


@dataclass(frozen=True)
class SwapSendPermit(SendPermit):
    """Capital approval receives direction, product and units, not only notional."""

    side: TradeType
    position_action: PositionAction
    contracts: Decimal
    contract_value_life: Decimal
    instrument: str
    position_mode: PositionMode
    leverage: int
    account_uid: str


class ProtectedSwapExecutorSender:
    def __init__(self, controller, connector, wal: IntentWAL, contract: LinearSwapContract,
                 path: Path, *, account_observation: Callable[[], SwapAccountObservation],
                 authorize_reservation: Callable[[SwapSendPermit], bool], risk_epoch: Callable[[], int],
                 clock_ms: Callable[[], int], max_age_ms: int, account_mode: str,
                 request_budget: AccountRequestBudget, create: bool):
        self.controller, self.connector, self.wal = controller, connector, wal
        self.contract, self.manager = contract, controller._order_safety_manager
        self.account_observation, self.authorize_reservation = account_observation, authorize_reservation
        self.risk_epoch, self.clock_ms = risk_epoch, clock_ms
        self.max_age_ms, self.account_mode = max_age_ms, account_mode
        self.request_budget = request_budget
        if (self.manager is None or not isinstance(contract, LinearSwapContract)
                or contract.instrument != "LIFE-USDT-SWAP" or contract.trading_pair != "LIFE-USDT"
                or any(not isinstance(v, Decimal) or not v.is_finite() or v <= 0 for v in (
                    contract.contract_value_life, contract.lot_size_contracts,
                    contract.min_size_contracts, contract.tick_size_usdt))
                or type(max_age_ms) is not int or max_age_ms <= 0 or account_mode not in ("2", "3", "4")
                or not isinstance(request_budget, AccountRequestBudget)
                or request_budget.account_uid != controller.config.recovery_account_uid
                or any(not callable(f) for f in (account_observation, authorize_reservation, risk_epoch, clock_ms))):
            raise ValueError("SWAP_SENDER_POLICY_INVALID")
        self.journal = PolicyState(path, policy=self._policy(),
                                   initial={"last_checked_ms": None, "actions": {}}, create=create)
        self._configs = {}
        self._permits = {}
        self._revoked = set()
        self._boundary_attempts = set()
        self._authorized_sends = set()

    def _policy(self):
        return {"controller_id": self.controller.config.id,
                "strategy": self.controller.config.strategy.model_dump(mode="json"),
                "account_uid": self.controller.config.recovery_account_uid,
                "account_mode": self.account_mode, "max_age_ms": self.max_age_ms,
                "contract": {k: str(v) for k, v in asdict(self.contract).items()},
                "request_budget": str(self.request_budget.path.resolve()),
                "request_policy": asdict(self.request_budget.policy)}

    def unresolved_order_ids(self):
        return tuple(r.client_order_id for r in self.wal.all_records()
                     if r.state not in ("TERMINAL", "ABORTED_BEFORE_SEND"))

    def owns_intent(self, intent_id):
        return intent_id in self._permits

    def _check(self, config, permit, *, issued=True):
        try:
            self._validate(config)
            if (self.controller.trading_permissions_ready() is not True
                    or self.controller.config_update_state.order_permission() is not True
                    or self.controller._order_safety_stopped
                    or self.controller._safety_rearm_in_progress
                    or self.controller._runner_scope_invalid
                    or self.controller._runner_stops_pending()
                    or self.controller._runtime_risk_ready() is not True
                    or self.controller._telemetry is not None and not self.controller._telemetry.healthy
                    or self.controller._order_safety_manager is not self.manager
                    or self._policy() != self.journal.policy
                    or config.id in self._revoked
                    or config.id in self.controller._runner_stop_revoked_ids):
                raise ValueError("SWAP_PERMISSION_REVOKED")
            if issued and self._configs.get(config.id) != config.model_dump(mode="json"):
                raise ValueError("SWAP_ACTION_NOT_ISSUED")
            current = self.manager.current_session
            ready = (self.manager.can_reduce() if config.position_action == PositionAction.CLOSE
                     else self.manager.can_quote(reference_ready=True, all_gates_ready=True,
                                                 market_reference_ready=True))
            risk_epoch = self.risk_epoch()
            if (not ready or current is None
                    or (current.session_id, current.epoch, current.config_version) != (
                        permit.session_id, permit.epoch, permit.config_version)
                    or type(risk_epoch) is not int or risk_epoch != permit.risk_epoch
                    or self.authorize_reservation(permit) is not True):
                raise ValueError("SWAP_RESERVATION_REVOKED")
            if (self.wal._uncertain or not self.wal.path.is_file() or self.wal.path.is_symlink()
                    or {r.intent_id: r for r in IntentWAL(self.wal.path).all_records()}
                    != {r.intent_id: r for r in self.wal.all_records()}):
                raise ValueError("SWAP_WAL_UNVERIFIED")
            if issued:
                record = self.wal.get(config.id)
                if (record.cancel_requested or record.state not in ("PREPARED", "SEND_UNKNOWN")
                        or (record.client_order_id, record.session_id, record.epoch, record.reservation_id,
                            record.slot_market, record.slot_side, record.slot_level) != (
                            permit.client_order_id, permit.session_id, permit.epoch, permit.reservation_id,
                            self.contract.instrument, config.side.name, int(config.level_id))):
                    raise ValueError("SWAP_WAL_PERMISSION_REVOKED")
            if any(r.state not in ("TERMINAL", "ABORTED_BEFORE_SEND") and r.intent_id != config.id
                   and r.intent_id not in self._permits for r in self.wal.all_records()):
                raise ValueError("SWAP_RECOVERY_REQUIRED")
            now = self.clock_ms()
            obs = self.account_observation()
            mode = PositionMode[self.controller.config.strategy.perpetual.position_mode]
            quantities = (obs.net_contracts, obs.long_contracts, obs.short_contracts,
                          obs.pending_close_buy_contracts, obs.pending_close_sell_contracts)
            if (type(now) is not int or now < 0 or not isinstance(obs, SwapAccountObservation)
                    or type(obs.observed_at_ms) is not int or not 0 <= now - obs.observed_at_ms <= self.max_age_ms
                    or obs.account_uid != self.controller.config.recovery_account_uid
                    or obs.account_mode != self.account_mode or obs.position_mode != mode
                    or obs.margin_mode != "cross" or type(obs.leverage) is not int
                    or obs.leverage != config.leverage or obs.connector_ready is not True
                    or self.connector.position_mode != mode
                    or self.connector.get_leverage(config.trading_pair) != config.leverage
                    or self.connector._contract_sizes.get(config.trading_pair) != self.contract.contract_value_life
                    or any(not isinstance(v, Decimal) or not v.is_finite() for v in quantities)
                    or any(v < 0 for v in quantities[1:])
                    or mode == PositionMode.ONEWAY and (obs.long_contracts != 0 or obs.short_contracts != 0)
                    or mode == PositionMode.HEDGE and obs.net_contracts != 0):
                raise ValueError("SWAP_ACCOUNT_UNVERIFIED")
            contracts = self.contract.life_to_contracts(config.amount)
            if config.position_action == PositionAction.CLOSE:
                available = ((max(obs.net_contracts, Decimal(0)) if config.side == TradeType.SELL
                              else max(-obs.net_contracts, Decimal(0))) if mode == PositionMode.ONEWAY
                             else obs.long_contracts if config.side == TradeType.SELL else obs.short_contracts)
                pending = (obs.pending_close_sell_contracts if config.side == TradeType.SELL
                           else obs.pending_close_buy_contracts)
                # Include every other locally issued close, not just the connector snapshot.
                local = sum((self.contract.life_to_contracts(p.quantity_base)
                             for key, p in self._permits.items() if key != config.id
                             and self._configs[key]["position_action"] == config.model_dump(mode="json")["position_action"]
                             and self._configs[key]["side"] == config.model_dump(mode="json")["side"]
                             and self.wal.get(key).state not in ("TERMINAL", "ABORTED_BEFORE_SEND")), Decimal(0))
                # Conservative overlap: never assume the external pending set is
                # the same set as the local WAL without authenticated identity.
                if contracts > available - pending - local:
                    raise ValueError("SWAP_CLOSE_EXCEEDS_POSITION")
            with self.journal.locked() as state:
                if (set(state) != {"last_checked_ms", "actions"}
                        or not isinstance(state["actions"], dict)
                        or set(state["actions"]) != {r.intent_id for r in self.wal.all_records()}):
                    raise ValueError("SWAP_ACTION_JOURNAL_MISMATCH")
                last = state["last_checked_ms"]
                if last is not None and (type(last) is not int or now < last):
                    raise ValueError("SWAP_CLOCK_ROLLBACK")
                if issued and state["actions"].get(config.id) != self._entry(config, permit):
                    raise ValueError("SWAP_ACTION_JOURNAL_MISMATCH")
                state["last_checked_ms"] = now
                self.journal.commit(state)
        except Exception as exc:
            raise PermissionError("SWAP_PERMISSION_REVOKED") from exc

    def _validate(self, config):
        perpetual = self.controller.config.strategy.perpetual
        if (not isinstance(config, OrderExecutorConfig) or perpetual.enabled is not True
                or config.controller_id != self.controller.config.id
                or config.connector_name != perpetual.connector or config.trading_pair != perpetual.pair
                or config.trading_pair != self.contract.trading_pair
                or perpetual.margin_mode != "cross" or perpetual.position_mode not in ("ONEWAY", "HEDGE")
                or config.leverage != perpetual.leverage
                or config.execution_strategy != ExecutionStrategy.LIMIT_MAKER
                or config.position_action not in (PositionAction.OPEN, PositionAction.CLOSE)
                or config.side not in (TradeType.BUY, TradeType.SELL)
                or not isinstance(config.price, Decimal) or not config.price.is_finite() or config.price <= 0
                or config.price % self.contract.tick_size_usdt != 0
                or not self.contract.valid_order_contracts(self.contract.life_to_contracts(config.amount))
                or not isinstance(config.level_id, str) or not re.fullmatch(r"0|[1-9][0-9]*", config.level_id)
                or int(config.level_id) >= len(self.controller.config.strategy.quotes.spreads_bps)):
            raise ValueError("SWAP_ORDER_INVALID")

    @staticmethod
    def _entry(config, permit):
        return {"config": config.model_dump(mode="json"),
                "permit": {k: str(v) if isinstance(v, Decimal) else v.name if isinstance(v, Enum) else v
                           for k, v in asdict(permit).items()}}

    def propose(self, config):
        self._validate(config)
        spot_wal = self.controller._order_safety_wal
        if spot_wal is not None and any(r.intent_id == config.id for r in spot_wal.all_records()):
            raise PermissionError("INTENT_ID_ALREADY_OWNED_BY_SPOT")
        current = self.manager.current_session
        if current is None:
            raise PermissionError("SESSION_PERMISSION_REVOKED")
        epoch = self.risk_epoch()
        if type(epoch) is not int or epoch < 1:
            raise PermissionError("RISK_EPOCH_INVALID")
        wire_id = "HBOT" + uuid.uuid4().hex[:28]
        permit = SwapSendPermit(
            config.id, wire_id, config.id, current.session_id, current.epoch,
            current.config_version, epoch, config.price, config.amount, config.side,
            config.position_action, self.contract.life_to_contracts(config.amount),
            self.contract.contract_value_life, self.contract.instrument,
            PositionMode[self.controller.config.strategy.perpetual.position_mode],
            config.leverage, self.controller.config.recovery_account_uid)
        self._check(config, permit, issued=False)
        self.wal.begin(config.id, client_order_id=wire_id, session_id=permit.session_id,
                       epoch=permit.epoch, reservation_id=permit.reservation_id,
                       slot_market=self.contract.instrument, slot_side=config.side.name,
                       slot_level=int(config.level_id))
        with self.journal.locked() as state:
            if config.id in state["actions"]:
                raise ValueError("SWAP_ACTION_ALREADY_ISSUED")
            state["actions"][config.id] = self._entry(config, permit)
            self.journal.commit(state)
        self._configs[config.id] = config.model_dump(mode="json")
        self._permits[config.id] = permit
        return CreateExecutorAction(controller_id=self.controller.config.id, executor_config=config)

    def authorizes_config(self, config):
        try:
            self._check(config, self._permits[config.id])
            return self.wal.get(config.id).state == "PREPARED"
        except (KeyError, PermissionError):
            return False

    def revoke(self, intent_id):
        if intent_id in self._permits:
            self._revoked.add(intent_id)
            self.wal.mark_cancel_requested(intent_id)

    def submit(self, config, *, amount, price, order_type):
        if (not isinstance(amount, Decimal) or not amount.is_finite()
                or not isinstance(price, Decimal) or not price.is_finite()
                or amount != config.amount or price != config.price or order_type != OrderType.LIMIT_MAKER
                or not self.authorizes_config(config)):
            raise PermissionError("SWAP_ACTION_NOT_AUTHORIZED")
        permit = self._permits[config.id]
        self.wal.arm_send(config.id, client_order_id=permit.client_order_id,
                          session_id=permit.session_id, epoch=permit.epoch, reservation_id=permit.reservation_id)

        def final_check(wire):
            if config.id in self._boundary_attempts:
                raise PermissionError("RECONCILE_BEFORE_RETRY")
            self._boundary_attempts.add(config.id)
            self._check(config, permit)
            if self.wal.get(config.id).state != "SEND_UNKNOWN":
                raise PermissionError("SWAP_WAL_PERMISSION_REVOKED")
            expected = {"clOrdId": permit.client_order_id, "tdMode": "cross", "ordType": "post_only",
                        "instId": self.contract.instrument, "side": config.side.name.lower(),
                        "sz": self.contract.life_to_contracts(config.amount), "px": config.price}
            mode = PositionMode[self.controller.config.strategy.perpetual.position_mode]
            if mode == PositionMode.ONEWAY:
                expected["posSide"] = "net"
                if config.position_action == PositionAction.CLOSE:
                    expected["reduceOnly"] = True
            else:
                expected["posSide"] = ("long" if config.side == TradeType.BUY else "short")
                if config.position_action == PositionAction.CLOSE:
                    expected["posSide"] = "short" if config.side == TradeType.BUY else "long"
            try:
                actual = dict(wire)
                for key in ("px", "sz"):
                    actual[key] = Decimal(actual[key]) if isinstance(actual[key], str) else None
                if (actual != expected or "reduceOnly" in actual and type(actual["reduceOnly"]) is not bool):
                    raise ValueError("ORDER_CHANGED")
            except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
                raise PermissionError("ORDER_CHANGED") from exc
            try:
                self.request_budget.charge("CREATE", "swap:" + permit.client_order_id,
                                           slot=f"{self.contract.instrument}:{config.side.name}:{config.level_id}")
            except Exception as exc:
                raise PermissionError("SWAP_REQUEST_BUDGET_UNAVAILABLE") from exc
            self._authorized_sends.add(config.id)

        def acknowledge(exchange_id):
            if config.id not in self._authorized_sends:
                raise PermissionError("SWAP_ACK_WITHOUT_SEND")
            self.wal.acknowledge(config.id, exchange_id)

        return self.connector.submit_protected_order(
            order_id=permit.client_order_id, trading_pair=config.trading_pair,
            amount=amount, price=price, trade_type=config.side, order_type=order_type,
            position_action=config.position_action, pre_send_check=final_check,
            on_ack=acknowledge)
