"""Joint settled USDT on the existing spot wallet, never an external transfer.

These are storage facts, not provider authentication or a send/release grant.
The actual joint observer must qualify their scope before invoking this port.
Unknown file publication requires original-command readback on a cold store.
"""
import asyncio
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Protocol

from hummingbot.strategy_v2.life_liquidity.storage.capital import CashflowSpot
from hummingbot.strategy_v2.life_liquidity.storage.contracts import (
    AtomicScope,
    CommitUnknown,
    OwnerMode,
    OwnerToken,
    StateBinding,
    StorageConflict,
    StorageError,
    StorageUnavailable,
    digest,
    encode,
    fingerprint,
    identifier,
    thaw,
    utc,
)
from hummingbot.strategy_v2.life_liquidity.storage.financial import (
    AckSpot,
    FillSpot,
    FinancialCut,
    PrepareSpot,
    decimal,
    signed_decimal,
)
from hummingbot.strategy_v2.life_liquidity.storage.joint_cashflows import LegacyJointCashflowAdapter
from hummingbot.strategy_v2.life_liquidity.storage.joint_fills import LegacyJointFillAdapter
from hummingbot.strategy_v2.life_liquidity.storage.joint_pending import LegacyJointPendingAdapter
from hummingbot.strategy_v2.life_liquidity.storage.joint_preparation import LegacyJointPreparationAdapter


@dataclass(frozen=True)
class JointCashEvent:
    event_id: str
    signed_amount_usdt: Decimal
    realized_pnl_usdt: Decimal | None = None
    signed_fee_usdt: Decimal | None = None
    session_id: str | None = None

    def __post_init__(self):
        if type(self.event_id) is not str:
            raise StorageError('JOINT_CASH_EVENT_INVALID')
        match = re.fullmatch(r'JOINT:([0-9]+):(TRADE|FUNDING):([A-Za-z0-9_.-]{1,64})', self.event_id)
        if match is None or not 0 < int(match[1]) <= 253402300799999 or str(int(match[1])) != match[1]:
            raise StorageError('JOINT_CASH_EVENT_INVALID')
        object.__setattr__(self, 'signed_amount_usdt', signed_decimal(self.signed_amount_usdt))
        if match[2] == 'FUNDING':
            if (not match[3].isascii() or not match[3].isdecimal()
                    or any(v is not None for v in (self.realized_pnl_usdt, self.signed_fee_usdt, self.session_id))):
                raise StorageError('JOINT_FUNDING_EVENT_INVALID')
        else:
            identifier(self.session_id)
            object.__setattr__(self, 'realized_pnl_usdt', signed_decimal(self.realized_pnl_usdt))
            object.__setattr__(self, 'signed_fee_usdt', signed_decimal(self.signed_fee_usdt))
            if self.signed_amount_usdt != self.realized_pnl_usdt + self.signed_fee_usdt:
                raise StorageError('JOINT_TRADE_CASH_MISMATCH')

    @property
    def at_ms(self):
        return int(self.event_id.split(':')[1])

    @property
    def execution_loss_usdt(self):
        if self.session_id is None:
            return Decimal('0')
        return max(Decimal('0'), -self.realized_pnl_usdt) + max(Decimal('0'), -self.signed_fee_usdt)

    def json(self):
        return {key: str(v) if isinstance(v, Decimal) else v for key, v in vars(self).items()}

    @classmethod
    def from_json(cls, data):
        values = dict(data)
        for key in ('signed_amount_usdt', 'realized_pnl_usdt', 'signed_fee_usdt'):
            if values[key] is not None:
                values[key] = Decimal(values[key])
        return cls(**values)


@dataclass(frozen=True)
class JointCashSpot:
    operation_id: str
    binding: StateBinding
    proof_id: str
    observed_at: datetime
    events: tuple[JointCashEvent, ...]  # Complete cumulative joint cash history.
    account_base_life: Decimal
    account_quote_usdt: Decimal
    order_scope_complete: bool
    bill_scope_complete: bool
    source_kind: str
    spot_fills: tuple[FillSpot, ...] = ()
    spot_cashflows: tuple[CashflowSpot, ...] = ()
    spot_acks: tuple[AckSpot, ...] = ()
    spot_prepared_receipts: tuple[PrepareSpot, ...] = ()
    spot_terminal_operations: tuple[str, ...] = ()
    spot_settlement_operations: tuple[str, ...] = ()

    def __post_init__(self):
        identifier(self.operation_id)
        digest(self.proof_id)
        utc(self.observed_at)
        for name in ('spot_terminal_operations', 'spot_settlement_operations'):
            values = getattr(self, name)
            if type(values) is not tuple or len(set(values)) != len(values):
                raise StorageError('JOINT_PENDING_SCOPE_INVALID')
            for key in values:
                identifier(key)
            object.__setattr__(self, name, tuple(sorted(values)))
        if (len(self.operation_id) > 96 or not isinstance(self.binding, StateBinding)
                or type(self.events) is not tuple or any(type(v) is not JointCashEvent for v in self.events)
                or len({v.event_id for v in self.events}) != len(self.events)
                or len({tuple(v.event_id.split(':')[2:]) for v in self.events}) != len(self.events)
                or any(type(v) is not bool for v in (self.order_scope_complete, self.bill_scope_complete))
                or self.source_kind != 'okx_joint_reconciled'):
            raise StorageError('JOINT_CASH_COMMAND_INVALID')
        if (type(self.spot_fills) is not tuple or any(type(v) is not FillSpot for v in self.spot_fills)
                or len({v.intent_id for v in self.spot_fills}) != len(self.spot_fills)
                or len({v.operation_id for v in self.spot_fills}) != len(self.spot_fills)
                or len({f.trade_id for v in self.spot_fills for f in v.fills}) != sum(len(v.fills) for v in self.spot_fills)):
            raise StorageError('JOINT_SPOT_FILL_SCOPE_INVALID')
        if (type(self.spot_cashflows) is not tuple or any(type(v) is not CashflowSpot for v in self.spot_cashflows)
                or len({v.bill_id for v in self.spot_cashflows}) != len(self.spot_cashflows)):
            raise StorageError('JOINT_CASHFLOW_SCOPE_INVALID')
        if (type(self.spot_acks) is not tuple or any(type(v) is not AckSpot for v in self.spot_acks)
                or len({v.operation_id for v in self.spot_acks}) != len(self.spot_acks)):
            raise StorageError('JOINT_ACK_SCOPE_INVALID')
        if (type(self.spot_prepared_receipts) is not tuple or any(type(v) is not PrepareSpot for v in self.spot_prepared_receipts)
                or len({v.operation_id for v in self.spot_prepared_receipts}) != len(self.spot_prepared_receipts)
                or len({v.intent_id for v in self.spot_prepared_receipts}) != len(self.spot_prepared_receipts)):
            raise StorageError('JOINT_PREPARED_RECEIPT_SCOPE_INVALID')
        object.__setattr__(self, 'spot_prepared_receipts', tuple(sorted(self.spot_prepared_receipts, key=lambda v: v.operation_id)))
        object.__setattr__(self, 'spot_acks', tuple(sorted(self.spot_acks, key=lambda v: v.operation_id)))
        object.__setattr__(self, 'spot_cashflows', tuple(sorted(self.spot_cashflows, key=lambda v: int(v.bill_id))))
        object.__setattr__(self, 'spot_fills', tuple(sorted(self.spot_fills, key=lambda v: v.intent_id)))
        object.__setattr__(self, 'account_base_life', decimal(self.account_base_life))
        object.__setattr__(self, 'account_quote_usdt', decimal(self.account_quote_usdt))
        object.__setattr__(self, 'events', tuple(sorted(self.events, key=lambda v: (v.at_ms, v.event_id))))

    def json(self):
        return {key: (v.json() if key == 'binding' else [item.json() for item in v] if key in ('events', 'spot_fills', 'spot_cashflows', 'spot_acks', 'spot_prepared_receipts')
                      else list(v) if key in ('spot_terminal_operations', 'spot_settlement_operations')
                      else str(v) if isinstance(v, Decimal) else v.isoformat() if isinstance(v, datetime) else v)
                for key, v in vars(self).items() if key not in ('spot_fills', 'spot_cashflows', 'spot_acks', 'spot_prepared_receipts',
                                                                'spot_terminal_operations', 'spot_settlement_operations') or v}

    @classmethod
    def from_json(cls, data):
        values = dict(data)
        values['binding'] = StateBinding.from_json(values['binding'])
        values['observed_at'] = datetime.fromisoformat(values['observed_at'])
        values['events'] = tuple(JointCashEvent.from_json(v) for v in values['events'])
        values['spot_fills'] = tuple(FillSpot.from_json(v) for v in values.get('spot_fills', ()))
        from hummingbot.strategy_v2.life_liquidity.storage.legacy_spot import LegacySpotStore
        values['spot_cashflows'] = tuple(LegacySpotStore._cashflow_from_json(v) for v in values.get('spot_cashflows', ()))
        values['spot_acks'] = tuple(LegacySpotStore._ack_from_json(v) for v in values.get('spot_acks', ()))
        values['spot_prepared_receipts'] = tuple(PrepareSpot.from_json(v) for v in values.get('spot_prepared_receipts', ()))
        for name in ('spot_terminal_operations', 'spot_settlement_operations'):
            values[name] = tuple(values.get(name, ()))
        for key in ('account_base_life', 'account_quote_usdt'):
            values[key] = Decimal(values[key])
        return cls(**values)


@dataclass(frozen=True)
class JointCashReceipt:
    binding: StateBinding
    operation_id: str
    state: str
    replayed: bool
    atomic_scope: AtomicScope = AtomicScope.RECOVERABLE_SAGA


class JointCashRepository(Protocol):
    async def prepared_receipt_ready(self, prepared: PrepareSpot, *, owner: OwnerToken,
                                     observed_at: datetime) -> bool:
        ...

    async def apply_joint_cash(self, command: JointCashSpot, *, cut: FinancialCut,
                               owner: OwnerToken) -> JointCashReceipt:
        ...

    async def resume_joint_cash(self, command: JointCashSpot, *, cut: FinancialCut,
                                owner: OwnerToken) -> JointCashReceipt:
        ...


class LegacyJointCashAdapter(LegacyJointFillAdapter, LegacyJointCashflowAdapter, LegacyJointPreparationAdapter, LegacyJointPendingAdapter):
    def _joint_recovery_observation_guard(self, view, owner, authority, proof_id, command=None):
        meta = self._read()
        scope = meta.resources if command is None else self._joint_recovery_resources(meta.resources, command)
        side_effects = self._joint_partial_side_effect_cut(view, meta.resources, command)
        cut_matches = (side_effects or dict(view.cut.components) == dict(meta.resources['journal_hashes'])
                       or self._joint_partial_fill_cut(view, meta.resources, command)
                       or self._joint_partial_cashflow_cut(view, meta.resources, command)
                       or self._joint_partial_ack_cut(view, meta.resources, command)
                       or self._joint_partial_prepared_receipt_cut(view, meta.resources, command))
        now = utc(self._clock())
        if (authority.reservations is not self._ledger or authority.spot_wal is not self._wal
                or authority.controller._joint_recovery is not authority
                or authority._repository_application is None or authority._repository_application.store is not self
                or not authority._busy or authority.journal._state['phase'] != 'APPLYING'
                or authority.journal._state['proof_id'] != proof_id
                or authority.journal._read() != authority.journal._state
                or meta.ownership.token != owner or meta.ownership.mode not in (
                    OwnerMode.ACTIVE, OwnerMode.DRAINING, OwnerMode.RECOVERY_ONLY)
                or now < owner.valid_from or self._incomplete(scope)
                or not cut_matches):
            raise StorageError('JOINT_CASH_RECOVERY_AUTHORITY_INVALID')
        return meta

    @staticmethod
    def _joint_cash_native_images(raw):
        # This saga can repair flat native before/after images. Archived-format
        # imports need their own qualified reconstruction port, before money.
        try:
            loss = json.loads(raw['loss'])
            wallet = json.loads(raw['reservations'])
            if (type(loss.get('events')) is not dict
                    or any(type(wallet.get(key)) is not dict for key in ('reservations', 'trades', 'account_events'))):
                raise ValueError
        except (KeyError, TypeError, ValueError):
            raise StorageError('JOINT_CASH_NATIVE_FORMAT_UNSUPPORTED') from None
        return loss, wallet

    def _verify_joint_cash_projection(self, resources):
        operations = resources.get('joint_cash_operations', {})
        expected_events = {}
        for key, row in operations.items():
            if (key != row['command']['operation_id'] or row['state'] != 'APPLIED'
                    or row['command']['binding'] != self.binding.json()
                    or row['fingerprint'] != fingerprint({'command': row['command'], 'owner': row['owner'],
                                                          'cut': row['cut']})):
                raise StorageUnavailable('JOINT_CASH_PROJECTION_UNVERIFIED')
            command = JointCashSpot.from_json(row['command'])
            if command.spot_terminal_operations or command.spot_settlement_operations:
                self._joint_pending_resources(resources, command, owner=OwnerToken.from_json(row['owner']))
            self._verify_joint_fill_operations(command, resources)
            self._verify_joint_cashflow_operations(command, resources)
            self._verify_joint_prepared_receipts(command, resources, row['owner'])
            for event in command.events:
                previous = expected_events.setdefault(event.event_id, event.json())
                if previous != event.json():
                    raise StorageUnavailable('JOINT_CASH_PROJECTION_UNVERIFIED')
        if expected_events != {key: row['event'] for key, row in resources.get('joint_cashflows', {}).items()}:
            raise StorageUnavailable('JOINT_CASH_PROJECTION_UNVERIFIED')
        for key, row in resources.get('joint_cashflows', {}).items():
            event = JointCashEvent.from_json(row['event'])
            origin = operations.get(row['operation_id'])
            if (key != event.event_id or origin is None
                    or event.json() not in origin['command']['events']):
                raise StorageUnavailable('JOINT_CASH_PROJECTION_UNVERIFIED')
            if event.session_id is not None and not self._loss.matches_event(
                    'swap:' + key.split(':')[3], event.execution_loss_usdt, session_id=event.session_id,
                    at_utc=datetime.fromtimestamp(event.at_ms / 1000, timezone.utc)):
                raise StorageUnavailable('JOINT_CASH_LOSS_UNVERIFIED')

    def _publish_joint_cash(self, key, owner, operations, events, hashes, fill_updates=None):
        meta = self._read()
        changes = (
            ('joint_cash_operations', encode(operations)), ('joint_cashflows', encode(events)),
            ('journal_hashes', encode(hashes)))
        if fill_updates is not None:
            changes += tuple((name, encode(value)) for name, value in fill_updates.items())
        return self._commit(meta.cut, owner, key, changes, observation=True)

    def _apply_joint_cash(self, command, cut, owner, resume=False, *, recovery_authority=None):
        self._require_accounting()
        self._assert_binding(command.binding)
        view = self._snapshot()
        meta = self._read()
        operations = thaw(meta.resources.get('joint_cash_operations', {}))
        events = thaw(meta.resources.get('joint_cashflows', {}))
        identity = fingerprint({'command': command.json(), 'owner': owner.json(), 'cut': cut.json()})
        previous = operations.get(command.operation_id)
        if previous is not None:
            if previous['fingerprint'] != identity:
                raise StorageConflict('JOINT_CASH_OPERATION_CONFLICT')
            before_images = {name: bytes.fromhex(value) for name, value in previous['before_native'].items()}
            if self._hashes(before_images) != dict(cut.components):
                raise StorageError('JOINT_CASH_ORIGINAL_CUT_UNVERIFIED')
            if previous['state'] == 'APPLIED':
                return JointCashReceipt(self.binding, command.operation_id, 'APPLIED', True)
            if not resume:
                raise StorageUnavailable('JOINT_CASH_RECONCILE_BEFORE_RETRY')
        elif resume:
            raise StorageError('JOINT_CASH_RECOVERY_NOT_STARTED')
        now = utc(self._clock())
        if (meta.ownership.token != owner or meta.ownership.mode not in (
                OwnerMode.ACTIVE, OwnerMode.DRAINING, OwnerMode.RECOVERY_ONLY) or now < owner.valid_from):
            raise StorageError('JOINT_CASH_OWNER_INVALID')
        if not resume:
            if command.spot_prepared_receipts and recovery_authority is None:
                raise StorageError('JOINT_PREPARED_RECEIPT_AUTHORITY_REQUIRED')
            if (command.spot_terminal_operations or command.spot_settlement_operations) and recovery_authority is None:
                raise StorageError('JOINT_PENDING_RECOVERY_AUTHORITY_REQUIRED')
            if recovery_authority is None:
                self._ack_guard(view, owner)
            else:
                self._joint_recovery_observation_guard(view, owner, recovery_authority, command.proof_id, command)
            if view.cut != cut:
                raise StorageConflict('SPOT_CUT_STALE')
            age_ms = (now - command.observed_at).total_seconds() * 1000
            max_age_ms = self._recovery_policy.max_fact_age_ms if self._recovery_policy else 0
            if not 0 <= age_ms <= max_age_ms:
                raise StorageError('JOINT_CASH_FACTS_STALE')
        if not command.order_scope_complete or not command.bill_scope_complete:
            raise StorageError('JOINT_CASH_SCOPE_INCOMPLETE')
        if (resume and (any(key != command.operation_id and row['state'] != 'APPLIED' for key, row in operations.items())
                        or any(row['state'] in ('PREPARING', 'ARMING', 'ACKING', 'FILLING', 'TERMINATING', 'ABORTING', 'CANCELING')
                               for row in self._joint_recovery_resources(meta.resources, command)['commands'].values())
                        or any(row['state'] != 'APPLIED' for name in ('cashflow_operations', 'session_operations',
                                                                      'lifecycle_operations', 'product_operations', 'recovery_batches')
                               for row in self._joint_recovery_resources(meta.resources, command).get(name, {}).values())
                        or any(row['state'] != 'SETTLED' for row in self._joint_recovery_resources(meta.resources, command).get('settlement_operations', {}).values()))):
            raise StorageError('JOINT_CASH_OTHER_RECOVERY_PENDING')
        old_events = dict(events)
        supplied = {event.event_id: event for event in command.events}
        if not set(old_events).issubset(supplied):
            raise StorageError('JOINT_CASH_HISTORY_INCOMPLETE')
        for key, event in supplied.items():
            if (event.at_ms < int(self._accounting_policy.opened_at.timestamp() * 1000)
                    or event.at_ms > int(command.observed_at.timestamp() * 1000)):
                raise StorageError('JOINT_CASH_EVENT_TIME_INVALID')
            if key in events and events[key]['event'] != event.json():
                raise StorageConflict('JOINT_CASH_EVENT_CONFLICT')
            events.setdefault(key, {'event': event.json(), 'operation_id': command.operation_id})
        fills, fill_operations = self._joint_fill_projection(command, meta.resources, owner)
        prepared_commands = self._joint_prepared_receipt_projection(command, meta.resources, owner)
        for operation in fill_operations.values():
            if operation['command']['intent_id'] in {row.intent_id for row in command.spot_fills}:
                target = prepared_commands[operation['command']['prepare_operation_id']]
                if target['state'] == 'FILLING':
                    target['state'] = operation['attempt_state_before_apply']
        cashflows, cashflow_operations = self._joint_cashflow_projection(
            command, meta.resources, owner, allow_new=recovery_authority is not None or resume)
        capital = self._capital_ledger(meta.resources['commands'], fills,
                                       cashflows, events)
        if (capital.life_balance, capital.usdt_balance) != (command.account_base_life, command.account_quote_usdt):
            raise StorageError('JOINT_CASH_ACCOUNT_MISMATCH')
        expected_loss = self._loss.verified_events()
        for row in command.spot_fills:
            for fill in row.fills:
                loss_key = 'fill:' + fill.trade_id
                loss = {'session_id': row.session_id,
                        'day': datetime.fromtimestamp(fill.fill_at_ms / 1000, timezone.utc).date().isoformat(),
                        'loss_quote': str(fill.loss(row.side))}
                if loss_key in expected_loss and expected_loss[loss_key] != loss:
                    raise StorageConflict('JOINT_CASH_LOSS_CONFLICT')
                expected_loss[loss_key] = loss
        for key, event in supplied.items():
            if event.session_id is None:
                continue
            loss_key = 'swap:' + key.split(':')[3]
            loss = {'session_id': event.session_id,
                    'day': datetime.fromtimestamp(event.at_ms / 1000, timezone.utc).date().isoformat(),
                    'loss_quote': str(event.execution_loss_usdt)}
            if loss_key in expected_loss and expected_loss[loss_key] != loss:
                raise StorageConflict('JOINT_CASH_LOSS_CONFLICT')
            expected_loss[loss_key] = loss
        if resume:
            before = {name: bytes.fromhex(value) for name, value in previous['before_native'].items()}
            current = self._raw()
            if any(current[name] != before[name] for name in ('session', 'wal')):
                raise StorageError('JOINT_CASH_RECOVERY_NATIVE_MISMATCH')
            loss_image, before_wallet = self._joint_cash_native_images(before)
            _, current_wallet = self._joint_cash_native_images(current)
            before_loss = loss_image['events']
            if (any(expected_loss.get(key) != value for key, value in before_loss.items())
                    or set(expected_loss) != set(before_loss) | {
                        'swap:' + key.split(':')[3] for key, v in supplied.items() if v.session_id is not None}
                    | {'fill:' + fill.trade_id for row in command.spot_fills for fill in row.fills}):
                raise StorageError('JOINT_CASH_RECOVERY_NATIVE_MISMATCH')
            # Monetary contents are checked below; all other native reservations,
            # fills and account-event provenance must remain the same.
            if not command.spot_fills:
                for name in ('reservations', 'trades'):
                    if before_wallet[name] != current_wallet[name]:
                        raise StorageError('JOINT_CASH_RECOVERY_NATIVE_MISMATCH')
                if ({key: v for key, v in before_wallet['account_events'].items() if v[0] != 'JOINT'}
                        != {key: v for key, v in current_wallet['account_events'].items() if v[0] != 'JOINT'}):
                    raise StorageError('JOINT_CASH_RECOVERY_NATIVE_MISMATCH')
            actual_cash = self._ledger.joint_cash_events
            before_cash = {key: Decimal(v['event']['signed_amount_usdt']) for key, v in old_events.items()}
            after_cash = {key: event.signed_amount_usdt for key, event in supplied.items()}
            if actual_cash not in (before_cash, after_cash):
                raise StorageError('JOINT_CASH_RECOVERY_NATIVE_MISMATCH')
            native_flows = self._ledger.cashflow_events
            if any(key not in cashflows or (cashflows[key]['currency'], Decimal(cashflows[key]['signed_amount'])) != value
                   for key, value in native_flows.items()):
                raise StorageError('JOINT_CASH_RECOVERY_NATIVE_MISMATCH')
            current_capital = self._capital_ledger(meta.resources['commands'],
                                                   {key: fills[key] for key in self._ledger.trade_ids},
                                                   {key: cashflows[key] for key in native_flows},
                                                   {key: events[key] for key in actual_cash})
            if (current_capital.life_balance, current_capital.usdt_balance) != (
                    self._ledger.life_balance, self._ledger.usdt_balance):
                raise StorageError('JOINT_CASH_RECOVERY_NATIVE_MISMATCH')
        else:
            raw = self._raw()
            self._joint_cash_native_images(raw)
            previous = {'command': command.json(), 'cut': cut.json(), 'owner': owner.json(), 'fingerprint': identity,
                        'state': 'APPLYING', 'before_native': {name: data.hex() for name, data in raw.items()}}
            operations[command.operation_id] = previous
        before = {name: bytes.fromhex(value) for name, value in previous['before_native'].items()}
        self._joint_fill_native_preflight(command, before, self._raw(), resume)
        try:
            if not resume:
                self._publish_joint_cash('joint-start:' + command.operation_id, owner, operations, old_events,
                                         dict(view.cut.components))
            self._ledger.apply_joint_snapshot(self._joint_fill_snapshots(command),
                                              {key: event.signed_amount_usdt for key, event in supplied.items()},
                                              self._joint_native_cashflows(command))
            if self._ledger.joint_cash_events != {key: event.signed_amount_usdt for key, event in supplied.items()}:
                raise StorageError('JOINT_CASH_PUBLICATION_INCOMPLETE')
            for key, event in supplied.items():
                if event.session_id is not None:
                    self._loss.record('swap:' + key.split(':')[3], event.execution_loss_usdt,
                                      session_id=event.session_id,
                                      at_utc=datetime.fromtimestamp(event.at_ms / 1000, timezone.utc))
            for row in command.spot_fills:
                for fill in row.fills:
                    self._loss.record('fill:' + fill.trade_id, fill.loss(row.side), session_id=row.session_id,
                                      at_utc=datetime.fromtimestamp(fill.fill_at_ms / 1000, timezone.utc))
                self._subsidy.record_fill_floor(row.intent_id, sum((f.loss(row.side) for f in row.fills), Decimal('0')))
            if self._loss.verified_events() != expected_loss:
                raise StorageError('JOINT_CASH_LOSS_PUBLICATION_INCOMPLETE')
            self._verify_fill_projection(meta.resources['commands'], fills)
            self._verify_capital_projection(meta.resources['commands'], fills,
                                            cashflows, events)
            previous['state'] = 'APPLIED'
            self._verify_joint_cash_projection({**thaw(meta.resources), 'joint_cash_operations': operations,
                                                'joint_cashflows': events, 'commands': prepared_commands, 'fills': fills, 'fill_operations': fill_operations,
                                                'cashflows': cashflows, 'cashflow_operations': cashflow_operations})
            self._publish_joint_cash('joint-done:' + command.operation_id, owner, operations, events,
                                     self._hashes(self._raw()),
                                     {'commands': prepared_commands, 'fills': fills, 'fill_operations': fill_operations, 'cashflows': cashflows,
                                      'cashflow_operations': cashflow_operations}
                                     if command.spot_fills or command.spot_cashflows or command.spot_prepared_receipts else None)
            return JointCashReceipt(self.binding, command.operation_id, 'APPLIED', False)
        except BaseException:
            self._uncertain = True
            raise CommitUnknown('JOINT_CASH_APPLICATION_UNKNOWN') from None

    async def _joint_cash_command(self, command, cut, owner, resume):
        if not isinstance(command, JointCashSpot) or not isinstance(cut, FinancialCut) or not isinstance(owner, OwnerToken):
            raise StorageError('JOINT_CASH_COMMAND_INVALID')
        self._assert_binding(cut.checkpoint.binding)
        try:
            return await self._call(self._apply_joint_cash, command, cut, owner, resume)
        except asyncio.CancelledError:
            self._uncertain = True
            raise

    async def apply_joint_cash(self, command, *, cut, owner):
        return await self._joint_cash_command(command, cut, owner, False)

    async def resume_joint_cash(self, command, *, cut, owner):
        return await self._joint_cash_command(command, cut, owner, True)
