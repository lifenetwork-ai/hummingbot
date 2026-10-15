"""Worker-owned native joint recovery ports for explicit offline file assembly.

The existing joint journal supplies the cross-journal APPLYING/COMMITTED saga,
financial replay and qualified release. Transport stays on the event loop. No
snapshot, successful refresh or reporting row activates a successor writer.
"""
import asyncio
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal
from typing import Protocol

from hummingbot.strategy_v2.life_liquidity.history_worker import HistoryWorkerPolicy, _capture
from hummingbot.strategy_v2.life_liquidity.joint_recovery import JointCapitalMeasurement, JointRecovery, RecoveryPayment
from hummingbot.strategy_v2.life_liquidity.protected_swap import SwapAccountObservation
from hummingbot.strategy_v2.life_liquidity.shared_capital import CapitalSnapshot
from hummingbot.strategy_v2.life_liquidity.storage.capital import CashflowSpot
from hummingbot.strategy_v2.life_liquidity.storage.contracts import (
    AtomicScope,
    OwnerMode,
    OwnerToken,
    StateBinding,
    StorageError,
    StorageUnavailable,
    encode,
    fingerprint,
    thaw,
    utc,
)
from hummingbot.strategy_v2.life_liquidity.storage.file import FileCheckpointStore
from hummingbot.strategy_v2.life_liquidity.storage.financial import FillSpot, PrepareSpot, SpotFillObservation
from hummingbot.strategy_v2.life_liquidity.storage.recorder_configuration import financial_controller_config
from hummingbot.strategy_v2.life_liquidity.storage.recovery import SpotAckRecoveryFacts, SpotFinancialRecoveryFacts
from hummingbot.strategy_v2.life_liquidity.storage.terminal import SpotTerminalFacts, TerminalSpot


@dataclass(frozen=True)
class JointLimits:
    max_orders: int
    max_trades: int
    max_snapshot_bytes: int

    def __post_init__(self):
        if any(type(v) is not int or v <= 0 for v in vars(self).values()):
            raise StorageError('JOINT_CAPACITY_INVALID')


@dataclass(frozen=True)
class _SpotFillProjection:
    spot_fills: tuple[FillSpot, ...]
    observed_at: datetime


@dataclass(frozen=True)
class JointSpotRecoveryInputs:
    """Detached observations, not a transfer approval or a financial grant."""
    fills: tuple[FillSpot, ...]
    facts: SpotFinancialRecoveryFacts | None
    cashflows: tuple[CashflowSpot, ...] = ()

    def __post_init__(self):
        if (type(self.fills) is not tuple or any(type(v) is not FillSpot for v in self.fills)
                or type(self.cashflows) is not tuple or any(type(v) is not CashflowSpot for v in self.cashflows)
                or self.facts is not None and type(self.facts) is not SpotFinancialRecoveryFacts
                or self.cashflows and self.facts is None):
            raise StorageError('JOINT_SPOT_INPUTS_INVALID')


@dataclass(frozen=True)
class JointView:
    binding: StateBinding
    phase: str
    proof_id: str | None
    claim_ids: tuple[str, ...]
    measurement: JointCapitalMeasurement | None
    reason_code: str
    atomic_scope: AtomicScope = AtomicScope.RECOVERABLE_SAGA


@dataclass(frozen=True)
class SwapFinancialView:
    binding: StateBinding
    proof_id: str
    capital: CapitalSnapshot  # Positions in LIFE; margin/collateral/funding in USDT.
    account: SwapAccountObservation  # SDK positions in contracts, never LIFE.
    settled_payments: tuple[RecoveryPayment, ...]  # Signed USDT; timestamp in ms.


class JointRepository(Protocol):
    async def reconcile(self, binding: StateBinding, owner: OwnerToken, *,
                        spot_fills: tuple[FillSpot, ...] = (), spot_facts: SpotFinancialRecoveryFacts | None = None,
                        spot_cashflows: tuple[CashflowSpot, ...] = ()) -> bool:
        ...

    async def cancel_working_swaps(self, binding: StateBinding, owner: OwnerToken) -> None:
        ...

    async def state(self, binding: StateBinding) -> JointView:
        ...

    async def swap_state(self, binding: StateBinding) -> SwapFinancialView:
        ...

    async def restore_original_spot_holds(self, command: PrepareSpot, facts: SpotFinancialRecoveryFacts) -> None:
        ...

    async def restore_original_arm_bookkeeping(self, operation_id: str, facts: SpotFinancialRecoveryFacts) -> None:
        ...


class FileJointRepository:
    """Native checkpoints share the owning store worker.

Explicit cash coupling publishes SWAP cash and loss provenance to the spot
store. Spot coupling additionally requires independent fill and account facts.
"""
    atomic_scope = AtomicScope.RECOVERABLE_SAGA

    def __init__(self, store, recovery, owner, limits, couple_cash=False, couple_spot=False):
        from hummingbot.strategy_v2.life_liquidity.storage.legacy_spot import LegacySpotStore
        if (type(couple_cash) is not bool or type(couple_spot) is not bool or couple_spot and not couple_cash
                or type(store) is not (LegacySpotStore if couple_cash else FileCheckpointStore)
                or type(recovery) is not JointRecovery
                or not isinstance(owner, OwnerToken) or not isinstance(limits, JointLimits)
                or recovery.history_worker is not None or recovery._repository_application is not None
                or store.binding.environment != 'OFFLINE_SIMULATION'):
            raise StorageError('JOINT_REPOSITORY_ASSEMBLY_UNSUPPORTED')
        self.store, self.binding = store, store.binding
        self.owner, self._recovery, self.limits = owner, recovery, limits
        self._couple_cash = couple_cash
        self._couple_spot = couple_spot
        if couple_cash and (store._ledger is not recovery.reservations or store._wal is not recovery.spot_wal
                            or recovery.controller._execution_loss_budget is not store._loss
                            or store._accounting_policy is None or store._recovery_policy is None):
            raise StorageError('JOINT_CASH_LEDGER_BINDING_MISMATCH')
        if couple_spot and (store._terminal_policy is None or recovery.controller._fill_attributor is not None):
            raise StorageError('JOINT_SPOT_APPLICATION_ASSEMBLY_UNSUPPORTED')
        self._revoked = False
        self._admitted = set()
        self._capture_policy = HistoryWorkerPolicy(limits.max_orders, limits.max_trades,
                                                   limits.max_snapshot_bytes, recovery.max_age_ms)

    async def restore_original_spot_holds(self, command, facts):
        if not self._couple_spot or type(command) is not PrepareSpot or type(facts) is not SpotFinancialRecoveryFacts:
            raise StorageError('JOINT_ORIGINAL_HOLD_COMMAND_INVALID')
        self.store._assert_binding(facts.binding)
        try:
            await self.store._call(self.store._restore_original_preparation_holds, command, facts, self.owner, self)
        except asyncio.CancelledError:
            self.store._uncertain = True
            raise

    async def restore_original_arm_bookkeeping(self, operation_id, facts):
        if not self._couple_spot or type(operation_id) is not str or type(facts) is not SpotFinancialRecoveryFacts:
            raise StorageError('JOINT_ORIGINAL_ARM_COMMAND_INVALID')
        self.store._assert_binding(facts.binding)
        try:
            await self.store._call(self.store._restore_original_arm_bookkeeping, operation_id, facts, self.owner, self)
        except asyncio.CancelledError:
            self.store._uncertain = True
            raise

    def _policy(self):
        recovery = self._recovery
        c = recovery.controller
        if (c._joint_recovery is not recovery or recovery.capital is not c._shared_capital_authority
                or recovery.sender is not c._protected_swap_sender
                or recovery.spot_wal is not c._order_safety_wal
                or recovery.reservations is not c._order_safety_reservations
                or recovery.capital.policy.account_uid != self.binding.account_uid
                or c.config.recovery_account_uid != self.binding.account_uid
                or fingerprint(c.config.strategy.model_dump(mode='json')) != self.binding.config_hash
                or recovery._binding != recovery.journal.policy
                or recovery.sender._policy() != recovery.sender.journal.policy
                or recovery.capital.journal.policy != recovery._binding['capital_policy']):
            raise StorageError('JOINT_REPOSITORY_BINDING_MISMATCH')
        products = {}
        for key, component in (('hedge', c._hedge_coordinator), ('carry', c._carry_monitor)):
            products[key] = None if component is None else fingerprint(component.journal.policy)
        result = {'binding': self.binding.json(), 'native': fingerprint(recovery.journal.policy),
                  'limits': vars(self.limits), 'controller': c.config.id,
                  'configuration': fingerprint(financial_controller_config(c.config)), 'products': products}
        if self._couple_cash:
            if (recovery.reservations is not self.store._ledger or recovery.spot_wal is not self.store._wal
                    or c._execution_loss_budget is not self.store._loss):
                raise StorageError('JOINT_CASH_LEDGER_BINDING_MISMATCH')
            result['coupled_cash'] = True
        if self._couple_spot:
            if c._fill_attributor is not None or self.store._terminal_policy is None:
                raise StorageError('JOINT_SPOT_APPLICATION_ASSEMBLY_UNSUPPORTED')
            result['coupled_spot'] = True
        return result

    def qualified_cashflows(self, proposed=()):
        if not self._couple_cash:
            return None
        resources = self.store._read().resources
        result = {key: self.store._cashflow_from_json(row) for key, row in resources.get('cashflows', {}).items()}
        # An admitted joint command permanently captures a new transfer before
        # native money. Cold recovery reads that original value, never reprices
        # it or requires a caller to invent a standalone cashflow-start receipt.
        from hummingbot.strategy_v2.life_liquidity.storage.joint_settlement import JointCashSpot
        retained = []
        native = self._recovery.journal._read()
        self._recovery._validate_state(native)
        retained.extend(self.store._cashflow_from_json(v) for v in
                        (native['bundle'] or {}).get('repository_cashflows', ()))
        for key, operation in resources.get('joint_cash_operations', {}).items():
            if (operation['state'] not in ('APPLYING', 'APPLIED') or key != operation['command']['operation_id']
                    or operation['command']['binding'] != self.binding.json()
                    or operation['fingerprint'] != fingerprint({'command': operation['command'],
                                                               'owner': operation['owner'], 'cut': operation['cut']})
                    or operation['state'] != 'APPLIED' and operation['owner'] != self.owner.json()):
                raise StorageError('JOINT_CASHFLOW_ORIGIN_REQUIRED')
            retained.extend(JointCashSpot.from_json(thaw(operation['command'])).spot_cashflows)
        for row in resources.get('cashflow_operations', {}).values():
            command = self.store._cashflow_from_json(row['command'])
            if (row['fingerprint'] != fingerprint({'command': command.json(), 'owner': row['owner']})
                    or row['state'] not in ('CASHFLOWING', 'APPLIED')
                    or row['state'] != 'APPLIED' and row['owner'] != self.owner.json()):
                raise StorageError('JOINT_CASHFLOW_ORIGIN_REQUIRED')
            self.store._validate_cashflow_value(command)
            previous = result.setdefault(command.bill_id, command)
            if any(previous.json()[k] != v for k, v in command.json().items() if k not in ('operation_id', 'observed_at')):
                raise StorageError('JOINT_CASHFLOW_HISTORY_CHANGED')
        for command in (*retained, *proposed):
            self.store._validate_cashflow_value(command)
            previous = result.setdefault(command.bill_id, command)
            if previous != command:
                raise StorageError('JOINT_CASHFLOW_HISTORY_CHANGED')
            original = resources.get('cashflow_operations', {}).get(command.operation_id)
            if original is not None and original['command'] != command.json():
                raise StorageError('JOINT_CASHFLOW_OPERATION_CONFLICT')
        if any(key not in result or value != (result[key].currency, result[key].signed_amount)
               for key, value in self.store._ledger.cashflow_events.items()):
            raise StorageError('JOINT_CASHFLOW_NATIVE_HISTORY_CHANGED')
        return result

    def qualified_acks(self, orders):
        if not self._couple_spot:
            return ()
        result = []
        for operation in self.store._read().resources.get('acknowledgements', {}).values():
            ack = self.store._ack_from_json(operation['command'])
            if (operation['fingerprint'] != fingerprint({'command': ack.json(), 'owner': operation['owner']})
                    or operation['state'] not in ('ACKED', 'ACKING')):
                raise StorageError('JOINT_ACK_ORIGIN_INVALID')
            if operation['state'] == 'ACKING':
                order = orders.get(ack.intent_id)
                if (operation['owner'] != self.owner.json() or order is None or order.state == 'unsent'
                        or order.exchange_order_id != ack.exchange_order_id or order.claim.wire_id != ack.client_order_id
                        or (order.claim.session_id, order.claim.epoch) != (ack.session_id, ack.epoch)):
                    raise StorageError('JOINT_ACK_SOURCE_MISMATCH')
            result.append(ack)
        return tuple(result)

    def prepare_cash_settlement(self, orders, events, encoded, observation, proof_id, spot_fills=(), spot_facts=None,
                                spot_cashflows=()):
        if not self._couple_cash:
            return None
        spot_orders = {key: o for key, o in orders.items() if o.claim.product == 'SPOT'}
        if spot_orders and not self._couple_spot:
            raise StorageError('JOINT_SPOT_BATCH_PORT_REQUIRED')
        from hummingbot.strategy_v2.life_liquidity.storage.joint_settlement import JointCashEvent, JointCashSpot
        source_events = {}
        for order in orders.values():
            if order.claim.product != 'SWAP':
                continue
            for trade in order.trades:
                key = f'JOINT:{trade.at_ms}:TRADE:{trade.trade_id}'
                source_events[key] = JointCashEvent(key, trade.realized_pnl_quote + trade.signed_fee,
                                                    trade.realized_pnl_quote, trade.signed_fee, order.claim.session_id)
        for payment in encoded['payments']:
            key = f"JOINT:{payment['at_ms']}:FUNDING:{payment['bill_id']}"
            source_events[key] = JointCashEvent(key, Decimal(payment['signed_amount_quote']))
        if {key: v.signed_amount_usdt for key, v in source_events.items()} != events:
            raise StorageError('JOINT_CASH_SOURCE_MISMATCH')
        resources = self.store._read().resources
        transfers = self.qualified_cashflows(spot_cashflows)
        observed_at = datetime.fromtimestamp(encoded['observed_at_ms'] / 1000, timezone.utc)
        if any(v.observed_at > observed_at for v in transfers.values()):
            raise StorageError('JOINT_CASHFLOW_OBSERVATION_FUTURE')
        qualified_fills = self._prepare_spot_fills(spot_orders, spot_fills, spot_facts, resources,
                                                   observation, encoded, proof_id, transfers)
        retained = {key: JointCashEvent.from_json(v['event']) for key, v in resources.get('joint_cashflows', {}).items()}
        for key, value in source_events.items():
            if key in retained and retained[key] != value:
                raise StorageError('JOINT_CASH_SOURCE_MISMATCH')
            retained[key] = value
        if not retained and not spot_orders and not transfers:
            return None
        operation_id = 'joint:' + proof_id
        existing = resources.get('joint_cash_operations', {}).get(operation_id)
        receipts = (tuple(PrepareSpot.from_json(v) for v in existing['command'].get('spot_prepared_receipts', ()))
                    if existing is not None else tuple(PrepareSpot.from_json(row['command']) for row in resources['commands'].values()
                                                       if row['state'] == 'PREPARING'))
        for prepared in receipts:
            source = spot_orders.get(prepared.intent_id)
            if source is None or source.state != 'unsent':
                raise StorageError('JOINT_PREPARED_RECEIPT_UNSENT_SOURCE_REQUIRED')
        return JointCashSpot(operation_id, self.binding, proof_id,
                             datetime.fromtimestamp(encoded['observed_at_ms'] / 1000, timezone.utc),
                             tuple(retained.values()), observation.spot_life_base, observation.spot_cash_quote,
                             True, True, 'okx_joint_reconciled', qualified_fills, tuple(transfers.values()), self.qualified_acks(orders), receipts)

    def _prepare_spot_fills(self, orders, supplied, facts, resources, observation, encoded, proof_id, transfers=None):
        if not orders and facts is None:
            if supplied:
                raise StorageError('JOINT_SPOT_SOURCE_SCOPE_MISMATCH')
            return ()
        if (facts is None or facts.binding != self.binding
                or facts.observed_at != datetime.fromtimestamp(encoded['observed_at_ms'] / 1000, timezone.utc)
                or (facts.account_base_life, facts.account_quote_usdt) != (
                    observation.spot_life_base, observation.spot_cash_quote)
                or not facts.order_scope_complete or not facts.bill_scope_complete or not facts.runner_scope_complete
                or facts.liability_base_life != 0 or facts.liability_quote_usdt != 0
                or set(facts.accepted_wire_ids) != {o.claim.wire_id for o in orders.values() if o.state != 'unsent'}
                or not set(facts.runner_wire_ids).issubset({r.client_order_id for r in self.store._wal.all_records()})):
            raise StorageError('JOINT_SPOT_ACCOUNT_FACTS_REQUIRED')
        if not orders:
            if (supplied or set(facts.account_trade_ids) != set(resources.get('fills', {}))
                    or set(facts.account_cashflow_ids) != set(transfers or {})):
                raise StorageError('JOINT_SPOT_SOURCE_SCOPE_MISMATCH')
            return ()
        source = {v.intent_id: v for v in supplied}
        if len(source) != len(supplied) or not set(source).issubset(orders):
            raise StorageError('JOINT_SPOT_SOURCE_SCOPE_MISMATCH')
        retained = {key: SpotFillObservation.from_json(row['fill']) for key, row in resources.get('fills', {}).items()}
        previous_rows = [FillSpot.from_json(v['command']) for v in resources.get('fill_operations', {}).values()]
        from hummingbot.strategy_v2.life_liquidity.storage.joint_settlement import JointCashSpot
        for operation in resources.get('joint_cash_operations', {}).values():
            for row in JointCashSpot.from_json(operation['command']).spot_fills:
                previous_rows.append(row)
                for fill in row.fills:
                    previous = retained.setdefault(fill.trade_id, fill)
                    if previous != fill:
                        raise StorageError('JOINT_SPOT_SOURCE_HISTORY_CHANGED')
        result = []
        for key, order in orders.items():
            matches = [v for v in resources['commands'].values() if v['command']['intent_id'] == key]
            if len(matches) != 1:
                raise StorageError('JOINT_SPOT_PREPARATION_REQUIRED')
            prepared = PrepareSpot.from_json(matches[0]['command'])
            claim = order.claim
            if (claim.wire_id, claim.session_id, claim.epoch, claim.config_version, claim.side,
                    claim.quantity_base, claim.price_usdt) != (
                    prepared.client_order_id, prepared.session_id, prepared.epoch, prepared.config_version,
                    prepared.side, prepared.quantity_base, prepared.limit_price_usdt):
                raise StorageError('JOINT_SPOT_CLAIM_MISMATCH')
            if prepared.risk_epoch is not None and claim.risk_epoch != prepared.risk_epoch:
                raise StorageError('JOINT_SPOT_RISK_PROVENANCE_MISMATCH')
            if not order.trades:
                if key in source:
                    raise StorageError('JOINT_SPOT_SOURCE_SCOPE_MISMATCH')
                continue
            row = source.get(key)
            if row is None:
                if any(t.trade_id not in retained for t in order.trades):
                    raise StorageError('JOINT_SPOT_FILL_REFERENCE_REQUIRED')
                ids = {t.trade_id for t in order.trades}
                row = next((v for v in previous_rows if v.intent_id == key
                            and {f.trade_id for f in v.fills} == ids), None)
                if row is None:
                    raise StorageError('JOINT_SPOT_FILL_REFERENCE_REQUIRED')
            expected = {(t.trade_id, t.quantity_base, t.price_usdt, t.fee_currency, t.signed_fee, t.at_ms)
                        for t in order.trades}
            if ((row.prepare_operation_id, row.client_order_id, row.market, row.side, row.session_id, row.epoch,
                 row.exchange_order_id) != (prepared.operation_id, claim.wire_id, prepared.market, claim.side,
                                            claim.session_id, claim.epoch, order.exchange_order_id)
                    or {(f.trade_id, f.quantity_base_life, f.price_usdt, f.fee_currency, f.signed_fee, f.fill_at_ms)
                        for f in row.fills} != expected
                    or any(f.trade_id in retained and retained[f.trade_id] != f for f in row.fills)):
                raise StorageError('JOINT_SPOT_SOURCE_HISTORY_CHANGED')
            result.append(row)
        candidate = _SpotFillProjection(tuple(result), facts.observed_at)
        fills, _ = self.store._joint_fill_projection(candidate, resources, self.owner)
        if set(facts.account_trade_ids) != set(fills) or set(facts.account_cashflow_ids) != set(transfers or {}):
            raise StorageError('JOINT_SPOT_ACCOUNT_FACTS_REQUIRED')
        return tuple(result)

    def apply_cash_settlement(self, command, orders=None, spot_facts=None):
        if command is None:
            return
        from hummingbot.strategy_v2.life_liquidity.storage.financial import FinancialCut
        from hummingbot.strategy_v2.life_liquidity.storage.joint_settlement import JointCashSpot
        for row in self.store._read().resources.get('joint_cash_operations', {}).values():
            if row['state'] == 'APPLIED':
                continue
            original = JointCashSpot.from_json(thaw(row['command']))
            self.store._apply_joint_cash(original, FinancialCut.from_json(thaw(row['cut'])), self.owner, True)
        if self._couple_spot:
            for row in self.store._read().resources.get('lifecycle_operations', {}).values():
                if row['state'] != 'APPLIED':
                    operation = row['command']
                    order = orders.get(operation['intent_id'])
                    if operation['action'] != 'ABORT_PREPARED' or order is None or order.state != 'unsent':
                        raise StorageError('JOINT_SPOT_LIFECYCLE_SOURCE_MISSING')
                    self.apply_spot_order(order, spot_facts)
            for row in self.store._read().resources.get('terminal_operations', {}).values():
                if row['state'] != 'TERMINAL':
                    order = orders.get(row['command']['intent_id'])
                    if order is None:
                        raise StorageError('JOINT_SPOT_TERMINAL_SOURCE_MISSING')
                    self.apply_spot_order(order, spot_facts)
        previous = self.store._read().resources.get('joint_cash_operations', {}).get(command.operation_id)
        cut = (FinancialCut.from_json(thaw(previous['cut'])) if previous is not None
               else self.store._snapshot().cut)
        self.store._apply_joint_cash(command, cut, self.owner, recovery_authority=self._recovery)

    def apply_spot_order(self, order, facts):
        if not self._couple_spot or not self._recovery.authorizes_order(order, self._recovery.journal._state['proof_id']):
            raise StorageError('JOINT_SPOT_ORDER_AUTHORITY_INVALID')
        for operation in self.store._read().resources.get('acknowledgements', {}).values():
            if operation['state'] == 'ACKING' and operation['command']['intent_id'] == order.claim.intent_id:
                ack = self.store._ack_from_json(operation['command'])
                if facts is None or order.exchange_order_id != ack.exchange_order_id:
                    raise StorageError('JOINT_ACK_SOURCE_MISMATCH')
                ack_facts = SpotAckRecoveryFacts(facts, ack.client_order_id, ack.exchange_order_id, ack.market, ack.side,
                                                 order.claim.quantity_base, order.claim.price_usdt,
                                                 sum((v.quantity_base for v in order.trades), Decimal('0')), order.state.upper())
                self.store._resume_ack(ack, ack_facts, self.store._snapshot().cut, self.owner)
        if order.state == 'unsent':
            resources = self.store._read().resources
            rows = [r for r in resources['commands'].values() if r['command']['intent_id'] == order.claim.intent_id]
            if len(rows) != 1 or order.trades or order.exchange_order_id is not None:
                raise StorageError('JOINT_SPOT_UNSENT_ORIGIN_REQUIRED')
            prepared = PrepareSpot.from_json(rows[0]['command'])
            from hummingbot.strategy_v2.life_liquidity.storage.spot_lifecycle import SpotLifecycleCommand
            command = SpotLifecycleCommand('jointunsent:' + fingerprint(prepared.json()), prepared.operation_id,
                                           prepared.intent_id, prepared.client_order_id, 'ABORT_PREPARED')
            self.store._apply_lifecycle(command, self.store._snapshot().cut, self.owner,
                                        joint_authority=self._recovery)
            return
        # A working order stays conservatively SEND_UNKNOWN until terminal
        # qualification. A recorded fill is not an ACK or a resend permission.
        if order.state not in ('filled', 'canceled'):
            return
        resources = self.store._read().resources
        rows = [r for r in resources['commands'].values() if r['command']['intent_id'] == order.claim.intent_id]
        if len(rows) != 1 or facts is None:
            raise StorageError('JOINT_SPOT_TERMINAL_SOURCE_MISSING')
        prepared = PrepareSpot.from_json(rows[0]['command'])
        recorded = tuple(SpotFillObservation.from_json(r['fill']) for r in resources.get('fills', {}).values()
                         if r['intent_id'] == order.claim.intent_id)
        wires = {r.client_order_id for r in self.store._wal.all_records()}
        pending = tuple(v for v in self._recovery.journal._state['bundle']['pending_wire_ids'] if v in wires)
        exchange = tuple(r.exchange_order_id for r in self.store._wal.all_records()
                         if r.client_order_id in pending and r.exchange_order_id is not None)
        terminal_facts = SpotTerminalFacts(
            self.binding, facts.observed_at, 'FILLED' if order.state == 'filled' else 'CANCELED',
            sum((f.quantity_base_life for f in recorded), Decimal('0')), prepared.quantity_base,
            prepared.limit_price_usdt, recorded, facts.account_base_life, facts.account_quote_usdt,
            facts.liability_base_life, facts.liability_quote_usdt, facts.account_trade_ids, facts.account_cashflow_ids,
            pending, exchange, facts.runner_wire_ids, facts.order_scope_complete, facts.bill_scope_complete,
            facts.runner_scope_complete)
        operation_id = 'jointterm:' + fingerprint({'intent': order.claim.intent_id, 'exchange': order.exchange_order_id})
        command = TerminalSpot(operation_id, prepared.operation_id, prepared.intent_id, prepared.client_order_id,
                               prepared.market, prepared.side, prepared.session_id, prepared.epoch,
                               order.exchange_order_id, terminal_facts)
        previous = resources.get('terminal_operations', {}).get(operation_id)
        self.store._confirm_terminal(command, self.store._snapshot().cut, self.owner, previous is not None,
                                     joint_authority=self._recovery)

    def _session_context_matches(self, meta):
        """An expired primary may observe its verified quiescent successor.

        This bridge never transfers the old writer's grant. It only lets the
        account collector obtain the fresh cut required for a new signed grant.
        """
        context = self._recovery._context()
        if context[:2] == (str(self.owner.session_id), self.owner.epoch):
            return True
        journal = self.store._sessions.load()
        parent, successor = journal.primary, journal.successor
        if (successor is None or context[:2] != (successor.session_id, successor.epoch)
                or (str(self.owner.session_id), self.owner.epoch) != (parent.session_id, parent.epoch)
                or utc(self.store._clock()) < parent.expires_at or self.owner.expires_at > parent.expires_at
                or journal.clock_rollback_latched or not meta.resources.get('session_operations')
                or self.store._incomplete(meta.resources) or self.store._reporting_pending(meta.resources)
                or self._recovery.capital.claim_ids()
                or any(r.state not in ('TERMINAL', 'ABORTED_BEFORE_SEND') for r in self._recovery._records())):
            return False
        self.store._verify_session_projection(meta.resources)
        view = self.store._snapshot()
        if dict(view.cut.components) != dict(meta.resources['journal_hashes']):
            return False
        self._recovery._armed = False
        self._recovery.sender._runtime_enabled = False
        return True

    def _guard(self, *, observation=False):
        self.store.assert_worker_thread()
        if self._revoked and not observation:
            raise StorageUnavailable('JOINT_REPOSITORY_REVOKED')
        meta = self.store._read()
        if thaw(meta.resources.get('joint_repository_policy')) != self._policy():
            raise StorageError('JOINT_REPOSITORY_POLICY_MISMATCH')
        now = utc(self.store._clock())
        # Same-writer observations and owned cancellations remain possible after
        # expiry; they do not create new risk or rearm the native sender.
        if (meta.ownership.token != self.owner or meta.ownership.mode not in (
                OwnerMode.ACTIVE, OwnerMode.DRAINING, OwnerMode.RECOVERY_ONLY)
                or now < self.owner.valid_from):
            raise StorageError('JOINT_OWNER_MISMATCH')
        if now >= self.owner.expires_at or meta.ownership.mode is not OwnerMode.ACTIVE:
            self._recovery._armed = False
            self._recovery.sender._runtime_enabled = False
        with self.store._state.inspect() as state:
            if state['checked_at'] is not None and now < datetime.fromisoformat(state['checked_at']):
                raise StorageError('JOINT_CLOCK_ROLLBACK')
        if not self._session_context_matches(meta):
            raise StorageError('JOINT_SESSION_MISMATCH')
        if any(row['state'] != 'APPLIED' for name in (
                'product_operations', 'recorder_outbox', 'recorder_progress', 'recorder_positions', 'recorder_events', 'recorder_configurations')
                for row in meta.resources.get(name, {}).values()):
            raise StorageUnavailable('JOINT_REPOSITORY_REPAIR_REQUIRED')
        self._recovery._verify_storage()
        return meta

    def _initialize(self):
        self.store.assert_worker_thread()
        meta = self.store._read()
        policy = self._policy()
        if (meta.ownership.token != self.owner
                or not self._session_context_matches(meta)):
            raise StorageError('JOINT_OWNER_OR_SESSION_MISMATCH')
        previous = meta.resources.get('joint_repository_policy')
        if previous is None:
            if (not self.store._created_offline or meta.ownership.token != self.owner
                    or meta.ownership.mode is not OwnerMode.ACTIVE
                    or self._recovery.capital.claim_ids()
                    or any(r.state not in ('TERMINAL', 'ABORTED_BEFORE_SEND') for r in self._recovery._records())):
                raise StorageError('JOINT_REPOSITORY_IMPORT_REQUIRED')
            self.store._commit(meta.cut, self.owner, 'joint:bind', (
                ('joint_repository_policy', encode(policy)),))
        elif thaw(previous) != policy:
            raise StorageError('JOINT_REPOSITORY_POLICY_MISMATCH')
        self._guard()
        if not self.store._created_offline:
            self._recovery._healthy = self._recovery._armed = False
            self._recovery.sender._runtime_enabled = False

    def capture(self, bundle):
        # Pure bounded detachment: neither callbacks nor mutable SDK objects
        # cross the owning worker boundary.
        return _capture(bundle, self._capture_policy)

    def assert_settlement_capacity(self, encoded):
        self.store.assert_worker_thread()
        # Include retained repository transfers/ACKs, not just this poll's SDK
        # response or newly supplied observations, in the durable proof budget.
        if len(encode(encoded)) > self.limits.max_snapshot_bytes:
            raise StorageError('JOINT_HISTORY_SNAPSHOT_CAPACITY')

    def assert_transport_ready(self):
        if self._revoked or self.store._closed or self.store._uncertain:
            raise StorageUnavailable('JOINT_REPOSITORY_REVOKED')

    def revoke(self):
        self._revoked = True
        self._recovery._healthy = self._recovery._armed = False
        self._recovery.sender._runtime_enabled = False

    def _execute(self, function, args):
        if self._recovery._repository_application is not self:
            raise StorageError('JOINT_REPOSITORY_APPLICATION_MISMATCH')
        self._guard()
        try:
            return function(*args)
        finally:
            # A cancelled awaiter cannot stop the physical worker. Its result
            # may complete accounting, but must never publish late eligibility.
            if self._revoked or self.store._closed or self.store._uncertain:
                self._recovery._healthy = self._recovery._armed = False
                self._recovery.sender._runtime_enabled = False
            else:
                try:
                    if utc(self.store._clock()) >= self.owner.expires_at:
                        self._recovery._armed = False
                        self._recovery.sender._runtime_enabled = False
                except Exception:
                    self.revoke()

    async def _checkpoint(self, function, *args):
        try:
            self.assert_transport_ready()
            return await self.store._call(self._execute, function, args)
        except asyncio.CancelledError:
            self.store._uncertain = True
            self.revoke()
            raise

    def _request(self, binding, owner):
        self.store._assert_binding(binding)
        if owner != self.owner:
            raise StorageError('JOINT_OWNER_MISMATCH')
        self.assert_transport_ready()

    def _admit(self):
        task = asyncio.current_task()
        if task is None or task in self._admitted:
            raise StorageError('JOINT_REPOSITORY_REENTRANT')
        if len(self._admitted) >= self.store.limits.max_pending:
            raise StorageUnavailable('JOINT_REPOSITORY_BACKPRESSURE')
        self._admitted.add(task)
        return task

    async def reconcile(self, binding, owner, *, spot_fills=(), spot_facts=None, spot_cashflows=()):
        self._request(binding, owner)
        if (type(spot_fills) is not tuple or any(type(v) is not FillSpot for v in spot_fills)
                or spot_facts is not None and type(spot_facts) is not SpotFinancialRecoveryFacts
                or type(spot_cashflows) is not tuple or any(type(v) is not CashflowSpot for v in spot_cashflows)
                or len(spot_cashflows) > self.limits.max_trades
                or len({v.bill_id for v in spot_cashflows}) != len(spot_cashflows)
                or len({v.operation_id for v in spot_cashflows}) != len(spot_cashflows)
                or spot_cashflows and spot_facts is None
                or len(spot_fills) > self.limits.max_orders
                or sum(len(v.fills) for v in spot_fills) > self.limits.max_trades
                or len(encode({'fills': [v.json() for v in spot_fills],
                               'cashflows': [v.json() for v in spot_cashflows],
                               'facts': None if spot_facts is None else spot_facts.json()})) > self.limits.max_snapshot_bytes
                or not self._couple_spot and (spot_fills or spot_facts is not None or spot_cashflows)):
            raise StorageError('JOINT_SPOT_CAPTURE_INVALID_OR_CAPACITY')
        task = self._admit()
        try:
            return await self._recovery._reconcile(spot_fills, spot_facts, spot_cashflows)
        except asyncio.CancelledError:
            self.revoke()
            raise
        finally:
            self._admitted.discard(task)

    async def cancel_working_swaps(self, binding, owner):
        self._request(binding, owner)
        task = self._admit()
        try:
            await self._recovery._cancel_with_failure_guard()
        except asyncio.CancelledError:
            self.revoke()
            raise
        finally:
            self._admitted.discard(task)

    def _state(self):
        with self._recovery.journal.inspect() as state:
            self._recovery._validate_state(state)
            phase, proof_id = state['phase'], state['proof_id']
        return JointView(self.binding, phase, proof_id, self._recovery.capital.claim_ids(),
                         self._recovery.measure(), self._recovery.reason_code)

    async def state(self, binding):
        self.store._assert_binding(binding)
        return await self._checkpoint(self._state)

    def _swap_state(self):
        recovery = self._recovery
        state = recovery._fresh()
        now = recovery.clock_ms()
        observation = recovery.capital.qualified_snapshot()
        recovery._check_receipt(state, now, observation=observation)
        account = recovery._swap_account(observation, now)
        payments = tuple(RecoveryPayment(p['bill_id'], Decimal(p['signed_amount_quote']), p['at_ms'])
                         for p in state['bundle']['payments'])
        return SwapFinancialView(self.binding, state['proof_id'],
                                 replace(observation, pending=tuple(observation.pending)), replace(account), payments)

    async def swap_state(self, binding):
        self.store._assert_binding(binding)
        return await self._checkpoint(self._swap_state)


async def open_file_joint(store, *, recovery, owner, limits, couple_cash=False, couple_spot=False):
    repo = FileJointRepository(store, recovery, owner, limits, couple_cash, couple_spot)
    try:
        await store._call(repo._initialize)
    except asyncio.CancelledError:
        repo.revoke()
        # Bootstrap may have physically bound the journal despite cancellation.
        # Close/readback must precede attempting another binding on this store.
        store._uncertain = True
        raise
    except BaseException:
        repo.revoke()
        # Validation refusal is not an unknown write. _commit itself latches
        # uncertainty when publication may have begun.
        raise
    # This is installation, not activation. Native public async entry points
    # delegate to the ports; their synchronous authority helpers stay private
    # to the worker-owned economic decision chain.
    recovery._repository_application = repo
    return repo
