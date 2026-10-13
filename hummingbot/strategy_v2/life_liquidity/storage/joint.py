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


@dataclass(frozen=True)
class JointLimits:
    max_orders: int
    max_trades: int
    max_snapshot_bytes: int

    def __post_init__(self):
        if any(type(v) is not int or v <= 0 for v in vars(self).values()):
            raise StorageError('JOINT_CAPACITY_INVALID')


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
    async def reconcile(self, binding: StateBinding, owner: OwnerToken) -> bool:
        ...

    async def cancel_working_swaps(self, binding: StateBinding, owner: OwnerToken) -> None:
        ...

    async def state(self, binding: StateBinding) -> JointView:
        ...

    async def swap_state(self, binding: StateBinding) -> SwapFinancialView:
        ...


class FileJointRepository:
    """Native checkpoints share the owning store worker.

Explicit cash coupling publishes SWAP cash and loss provenance to the spot
store. Batches containing SPOT claims still require the joint fill port.
"""
    atomic_scope = AtomicScope.RECOVERABLE_SAGA

    def __init__(self, store, recovery, owner, limits, couple_cash=False):
        from hummingbot.strategy_v2.life_liquidity.storage.legacy_spot import LegacySpotStore
        if (type(couple_cash) is not bool
                or type(store) is not (LegacySpotStore if couple_cash else FileCheckpointStore)
                or type(recovery) is not JointRecovery
                or not isinstance(owner, OwnerToken) or not isinstance(limits, JointLimits)
                or recovery.history_worker is not None or recovery._repository_application is not None
                or store.binding.environment != 'OFFLINE_SIMULATION'):
            raise StorageError('JOINT_REPOSITORY_ASSEMBLY_UNSUPPORTED')
        self.store, self.binding = store, store.binding
        self.owner, self._recovery, self.limits = owner, recovery, limits
        self._couple_cash = couple_cash
        if couple_cash and (store._ledger is not recovery.reservations or store._wal is not recovery.spot_wal
                            or recovery.controller._execution_loss_budget is not store._loss
                            or store._accounting_policy is None or store._recovery_policy is None):
            raise StorageError('JOINT_CASH_LEDGER_BINDING_MISMATCH')
        self._revoked = False
        self._admitted = set()
        self._capture_policy = HistoryWorkerPolicy(limits.max_orders, limits.max_trades,
                                                   limits.max_snapshot_bytes, recovery.max_age_ms)

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
                  'configuration': fingerprint(c.config.model_dump(mode='json')), 'products': products}
        if self._couple_cash:
            if (recovery.reservations is not self.store._ledger or recovery.spot_wal is not self.store._wal
                    or c._execution_loss_budget is not self.store._loss):
                raise StorageError('JOINT_CASH_LEDGER_BINDING_MISMATCH')
            result['coupled_cash'] = True
        return result

    def prepare_cash_settlement(self, orders, events, encoded, observation, proof_id):
        if not self._couple_cash:
            return None
        if any(order.claim.product == 'SPOT' for order in orders.values()):
            raise StorageError('JOINT_SPOT_BATCH_PORT_REQUIRED')
        from hummingbot.strategy_v2.life_liquidity.storage.joint_settlement import JointCashEvent, JointCashSpot
        source_events = {}
        for order in orders.values():
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
        retained = {key: JointCashEvent.from_json(v['event']) for key, v in resources.get('joint_cashflows', {}).items()}
        for key, value in source_events.items():
            if key in retained and retained[key] != value:
                raise StorageError('JOINT_CASH_SOURCE_MISMATCH')
            retained[key] = value
        if not retained:
            return None
        return JointCashSpot('joint:' + proof_id, self.binding, proof_id,
                             datetime.fromtimestamp(encoded['observed_at_ms'] / 1000, timezone.utc),
                             tuple(retained.values()), observation.spot_life_base, observation.spot_cash_quote,
                             True, True, 'okx_joint_reconciled')

    def apply_cash_settlement(self, command):
        if command is None:
            return
        from hummingbot.strategy_v2.life_liquidity.storage.financial import FinancialCut
        from hummingbot.strategy_v2.life_liquidity.storage.joint_settlement import JointCashSpot
        for row in self.store._read().resources.get('joint_cash_operations', {}).values():
            if row['state'] == 'APPLIED':
                continue
            original = JointCashSpot.from_json(thaw(row['command']))
            self.store._apply_joint_cash(original, FinancialCut.from_json(thaw(row['cut'])), self.owner, True)
        previous = self.store._read().resources.get('joint_cash_operations', {}).get(command.operation_id)
        cut = (FinancialCut.from_json(thaw(previous['cut'])) if previous is not None
               else self.store._snapshot().cut)
        self.store._apply_joint_cash(command, cut, self.owner, recovery_authority=self._recovery)

    def _guard(self):
        self.store.assert_worker_thread()
        if self._revoked:
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
        context = self._recovery._context()
        if context[:2] != (str(self.owner.session_id), self.owner.epoch):
            raise StorageError('JOINT_SESSION_MISMATCH')
        if any(row['state'] != 'APPLIED' for name in (
                'product_operations', 'recorder_outbox', 'recorder_progress', 'recorder_positions', 'recorder_events')
                for row in meta.resources.get(name, {}).values()):
            raise StorageUnavailable('JOINT_REPOSITORY_REPAIR_REQUIRED')
        self._recovery._verify_storage()
        return meta

    def _initialize(self):
        self.store.assert_worker_thread()
        meta = self.store._read()
        policy = self._policy()
        if (meta.ownership.token != self.owner
                or self._recovery._context()[:2] != (str(self.owner.session_id), self.owner.epoch)):
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

    async def reconcile(self, binding, owner):
        self._request(binding, owner)
        task = self._admit()
        try:
            return await self._recovery._reconcile()
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


async def open_file_joint(store, *, recovery, owner, limits, couple_cash=False):
    repo = FileJointRepository(store, recovery, owner, limits, couple_cash)
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
