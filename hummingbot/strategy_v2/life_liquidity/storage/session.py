"""Typed session decisions on the same file coordinator as financial commands.

SessionManager remains the decision engine. Its planned journal is published
behind a saga barrier; neither a tick nor cold completion issues a send grant.
"""
import asyncio
import time
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from hummingbot.strategy_v2.life_liquidity.session import OrderReconciliation, SessionJournal, SessionManager
from hummingbot.strategy_v2.life_liquidity.storage.contracts import (
    AtomicScope,
    CommitUnknown,
    OwnerMode,
    OwnerToken,
    StateBinding,
    StorageConflict,
    StorageError,
    encode,
    fingerprint,
    identifier,
    thaw,
    utc,
)
from hummingbot.strategy_v2.life_liquidity.storage.financial import FinancialCut, decimal
from hummingbot.strategy_v2.life_liquidity.storage.recovery import SpotFinancialRecoveryFacts


@dataclass(frozen=True)
class SessionTick:
    operation_id: str
    session_id: str
    epoch: int
    reference_ready: bool
    all_gates_ready: bool
    market_reference_ready: bool
    max_reconciliation_age_ms: int
    reconciliation: OrderReconciliation | None = None
    account: SpotFinancialRecoveryFacts | None = None
    market_anchor_usdt: Decimal | None = None

    def __post_init__(self):
        identifier(self.operation_id)
        identifier(self.session_id)
        if (len(self.operation_id) > 96 or type(self.epoch) is not int or self.epoch <= 0
                or type(self.max_reconciliation_age_ms) is not int or self.max_reconciliation_age_ms <= 0
                or any(type(getattr(self, key)) is not bool for key in (
                    'reference_ready', 'all_gates_ready', 'market_reference_ready'))
                or (self.reconciliation is None) != (self.account is None)
                or self.reconciliation is not None and (not isinstance(self.reconciliation, OrderReconciliation)
                                                        or not isinstance(self.account, SpotFinancialRecoveryFacts))):
            raise StorageError('SESSION_TICK_INVALID')
        if self.market_anchor_usdt is not None:
            object.__setattr__(self, 'market_anchor_usdt', decimal(self.market_anchor_usdt, positive=True))

    def json(self):
        data = dict(self.__dict__)
        data['market_anchor_usdt'] = None if self.market_anchor_usdt is None else str(self.market_anchor_usdt)
        data['account'] = None if self.account is None else self.account.json()
        data['reconciliation'] = None if self.reconciliation is None else {
            key: value.isoformat() if isinstance(value, datetime) else list(value) if type(value) is tuple else value
            for key, value in self.reconciliation.__dict__.items()}
        return data

    @classmethod
    def from_json(cls, value):
        data = dict(value)
        if data['market_anchor_usdt'] is not None:
            data['market_anchor_usdt'] = Decimal(data['market_anchor_usdt'])
        if data['account'] is not None:
            data['account'] = SpotFinancialRecoveryFacts.from_json(data['account'])
        if data['reconciliation'] is not None:
            result = dict(data['reconciliation'])
            result['observed_at'] = datetime.fromisoformat(result['observed_at'])
            for key in ('open_order_ids', 'pending_cancel_ids', 'unknown_order_ids'):
                result[key] = tuple(result[key])
            data['reconciliation'] = OrderReconciliation(**result)
        return cls(**data)


@dataclass(frozen=True)
class PendingSessionTick:
    command: SessionTick
    original_cut: FinancialCut
    owner: OwnerToken


@dataclass(frozen=True)
class SessionTickReceipt:
    binding: StateBinding
    operation_id: str
    state: str
    reason_code: str
    replayed: bool
    atomic_scope: AtomicScope = AtomicScope.RECOVERABLE_SAGA


class SessionRepository(Protocol):
    async def tick_session(self, command: SessionTick, *, cut: FinancialCut, owner: OwnerToken) -> SessionTickReceipt:
        ...

    async def pending_session_ticks(self, binding: StateBinding) -> tuple[PendingSessionTick, ...]:
        ...


class _PlannedSessionStore:
    """Worker-local decision buffer; this is never a filesystem adapter."""

    def __init__(self, journal):
        self.journal = SessionJournal.from_dict(journal.to_dict())

    def load(self):
        return self.journal

    def save(self, journal):
        self.journal = journal


class LegacySessionAdapter:
    def _pending_session_ticks(self):
        self._snapshot()
        return tuple(PendingSessionTick(SessionTick.from_json(row['command']), FinancialCut.from_json(row['cut']),
                                        OwnerToken.from_json(row['owner']))
                     for row in self._read().resources.get('session_operations', {}).values() if row['state'] != 'APPLIED')

    async def pending_session_ticks(self, binding):
        self._assert_binding(binding)
        return await self._call(self._pending_session_ticks)

    def _publish_session(self, key, owner, operations, head, hashes):
        meta = self._read()
        return self._commit(meta.cut, owner, key, (
            ('session_operations', encode(operations)), ('session_head', encode(head)),
            ('journal_hashes', encode(hashes))), observation=True)

    def _verify_session_projection(self, resources):
        operations = thaw(resources.get('session_operations', {}))
        if not operations:
            return
        ordered = sorted(operations.items(), key=lambda pair: pair[1]['sequence'])
        last = None
        for sequence, (key, row) in enumerate(ordered, 1):
            if (row['sequence'] != sequence or row['state'] != 'APPLIED'
                    or row['command']['operation_id'] != key
                    or row['identity'] != fingerprint({'command': row['command'], 'cut': row['cut'], 'owner': row['owner']})
                    or last is not None and row['before'] != last):
                raise StorageError('SESSION_PROJECTION_UNVERIFIED')
            SessionJournal.from_dict(row['before'])
            SessionJournal.from_dict(row['after'])
            last = row['after']
        if (resources.get('session_head') != ordered[-1][0]
                or self._sessions.load().to_dict() != last):
            raise StorageError('SESSION_PROJECTION_UNVERIFIED')

    def _session_evidence(self, command, resources, now):
        if command.reconciliation is None:
            return
        facts, result = command.account, command.reconciliation
        self._assert_binding(facts.binding)
        if (facts.observed_at != result.observed_at or result.session_id != command.session_id
                or result.epoch != command.epoch or not result.scope_complete or not result.trade_events_reconciled
                or result.open_order_ids or result.pending_cancel_ids or result.unknown_order_ids
                or not facts.order_scope_complete or not facts.bill_scope_complete or not facts.runner_scope_complete
                or facts.accepted_wire_ids or facts.runner_wire_ids
                or facts.liability_base_life != 0 or facts.liability_quote_usdt != 0
                or not 0 <= (now - facts.observed_at).total_seconds() * 1000 <= command.max_reconciliation_age_ms
                or any(row.state not in ('TERMINAL', 'ABORTED_BEFORE_SEND') for row in self._wal.all_records())
                or self._ledger.joint_cash_events):
            raise StorageError('SESSION_RECONCILIATION_UNQUALIFIED')
        self._verify_recovery_projection(resources['commands'], resources.get('fills', {}),
                                         resources.get('cashflows', {}), facts)

    def _tick_session(self, command, cut, owner):
        self._assert_binding(cut.checkpoint.binding)
        view, meta = self._snapshot(), self._read()
        operations = thaw(meta.resources.get('session_operations', {}))
        identity = fingerprint({'command': command.json(), 'cut': cut.json(), 'owner': owner.json()})
        previous = operations.get(command.operation_id)
        if previous is not None and previous['identity'] != identity:
            raise StorageConflict('SESSION_OPERATION_CONFLICT')
        if previous is not None and previous['state'] == 'APPLIED':
            journal = SessionJournal.from_dict(previous['after'])
            return SessionTickReceipt(self.binding, command.operation_id, journal.state, journal.reason_code, True)
        now = utc(self._clock())
        if previous is None:
            self._ack_guard(view, owner)
            if cut != view.cut:
                raise StorageConflict('SESSION_CUT_STALE')
            current = self._sessions.load()
            record = current.successor or current.primary
            if (command.session_id, command.epoch) != (record.session_id, record.epoch):
                raise StorageError('SESSION_SCOPE_MISMATCH')
            policies = {row['command']['max_reconciliation_age_ms'] for row in operations.values()}
            if policies and policies != {command.max_reconciliation_age_ms}:
                raise StorageError('SESSION_POLICY_MISMATCH')
            if (self._cold_unarmed or meta.ownership.mode is not OwnerMode.ACTIVE) and (
                    command.reference_ready or command.all_gates_ready or command.market_reference_ready):
                raise StorageError('SESSION_UNARMED_GATE')
            self._session_evidence(command, meta.resources, now)
            planned = _PlannedSessionStore(current)
            mono = time.monotonic()
            manager = SessionManager(planned, wall_clock=lambda: now,
                                     max_reconciliation_age_ms=command.max_reconciliation_age_ms,
                                     monotonic_clock=lambda: mono)
            manager._monotonic_deadline = self._monotonic_deadline
            manager.tick(reference_ready=command.reference_ready, all_gates_ready=command.all_gates_ready,
                         market_reference_ready=command.market_reference_ready,
                         reconciliation=command.reconciliation, market_anchor_usdt=command.market_anchor_usdt)
            operations[command.operation_id] = {'identity': identity, 'command': command.json(), 'cut': cut.json(),
                                                'owner': owner.json(), 'state': 'APPLYING', 'sequence': len(operations) + 1,
                                                'before': current.to_dict(), 'after': planned.load().to_dict()}
            self._publish_session('session-start:' + command.operation_id, owner, operations,
                                  meta.resources.get('session_head'), dict(view.cut.components))
        else:
            if (meta.ownership.token != owner or meta.ownership.mode not in (
                    OwnerMode.ACTIVE, OwnerMode.DRAINING, OwnerMode.RECOVERY_ONLY) or now < owner.valid_from
                    or any(key != command.operation_id and row['state'] != 'APPLIED' for key, row in operations.items())
                    or self._incomplete({**meta.resources, 'session_operations': {}})):
                raise StorageError('SESSION_RECOVERY_OWNER_OR_SCOPE_INVALID')
        row = operations[command.operation_id]
        try:
            original_components = dict(row['cut']['components'])
            if any(token != original_components[name] for name, token in view.cut.components if name != 'session'):
                raise StorageError('SESSION_UNRELATED_NATIVE_MUTATION')
            current = self._sessions.load().to_dict()
            if current not in (row['before'], row['after']):
                raise StorageError('SESSION_NATIVE_STATE_CONFLICT')
            if current != row['after']:
                self._sessions.save(SessionJournal.from_dict(row['after']))
            if self._sessions.load().to_dict() != row['after']:
                raise StorageError('SESSION_NATIVE_PUBLICATION_INCOMPLETE')
            verified = self._snapshot()
            if any(token != original_components[name] for name, token in verified.cut.components if name != 'session'):
                raise StorageError('SESSION_UNRELATED_NATIVE_MUTATION')
            row['state'] = 'APPLIED'
            self._publish_session('session-done:' + command.operation_id, owner, operations,
                                  command.operation_id, dict(verified.cut.components))
            self._snapshot()
            journal = SessionJournal.from_dict(row['after'])
            return SessionTickReceipt(self.binding, command.operation_id, journal.state, journal.reason_code,
                                      previous is not None)
        except BaseException:
            self._uncertain = True
            raise CommitUnknown('SESSION_TICK_UNKNOWN') from None

    async def tick_session(self, command, *, cut, owner):
        if not isinstance(command, SessionTick) or not isinstance(cut, FinancialCut) or not isinstance(owner, OwnerToken):
            raise StorageError('SESSION_TICK_INVALID')
        try:
            return await self._call(self._tick_session, command, cut, owner)
        except asyncio.CancelledError:
            self._uncertain = True
            raise
