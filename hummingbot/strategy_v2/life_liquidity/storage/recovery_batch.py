"""Qualified account recovery, including activity received after a pending write.

This is an accounting saga, never a send/release/ownership command. Original
operation rows and fill-time prices remain immutable. One durable batch barrier
holds admission closed until all native postconditions and the account agree.
"""
import asyncio
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
    fingerprint,
    identifier,
    thaw,
    utc,
)
from hummingbot.strategy_v2.life_liquidity.storage.file import FileCheckpointStore, encode
from hummingbot.strategy_v2.life_liquidity.storage.financial import AckSpot, FillSpot, FinancialCut, PrepareSpot
from hummingbot.strategy_v2.life_liquidity.storage.recovery import SpotAckRecoveryFacts, SpotFinancialRecoveryFacts


@dataclass(frozen=True)
class SpotRecoveryBatch:
    operation_id: str
    fills: tuple[FillSpot, ...]
    cashflows: tuple[CashflowSpot, ...]
    acknowledgements: tuple[AckSpot, ...]
    supersedes: tuple[str, ...] = ()

    def __post_init__(self):
        identifier(self.operation_id)
        if (type(self.supersedes) is not tuple or len(set(self.supersedes)) != len(self.supersedes)
                or self.operation_id in self.supersedes):
            raise StorageError('SPOT_RECOVERY_BATCH_INVALID')
        for operation in self.supersedes:
            identifier(operation)
        for name, expected, key in (('fills', FillSpot, 'intent_id'), ('cashflows', CashflowSpot, 'bill_id'),
                                    ('acknowledgements', AckSpot, 'intent_id')):
            rows = getattr(self, name)
            if (type(rows) is not tuple or any(not isinstance(row, expected) for row in rows)
                    or len({getattr(row, key) for row in rows}) != len(rows)):
                raise StorageError('SPOT_RECOVERY_BATCH_INVALID')
        operations = [row.operation_id for rows in (self.fills, self.cashflows, self.acknowledgements) for row in rows]
        if not operations or len(set(operations)) != len(operations):
            raise StorageError('SPOT_RECOVERY_BATCH_INVALID')

    def json(self):
        return {'operation_id': self.operation_id, 'supersedes': list(self.supersedes), **{name: [row.json() for row in getattr(self, name)]
                for name in ('fills', 'cashflows', 'acknowledgements')}}

    @classmethod
    def from_json(cls, data):
        from hummingbot.strategy_v2.life_liquidity.storage.legacy_spot import LegacySpotStore
        from hummingbot.strategy_v2.life_liquidity.storage.pending import LegacyPendingAdapter
        return cls(data['operation_id'], tuple(FillSpot.from_json(row) for row in data['fills']),
                   tuple(LegacySpotStore._cashflow_from_json(row) for row in data['cashflows']),
                   tuple(LegacyPendingAdapter._ack_from_json(row) for row in data['acknowledgements']),
                   tuple(data.get('supersedes', ())))


@dataclass(frozen=True)
class SpotRecoveryBatchReceipt:
    binding: StateBinding
    operation_id: str
    state: str
    replayed: bool
    atomic_scope: AtomicScope = AtomicScope.RECOVERABLE_SAGA


class SpotRecoveryBatchRepository(Protocol):
    async def recover_spot_batch(self, command: SpotRecoveryBatch, *, facts: SpotFinancialRecoveryFacts,
                                 acknowledgements: tuple[SpotAckRecoveryFacts, ...], cut: FinancialCut,
                                 owner: OwnerToken) -> SpotRecoveryBatchReceipt:
        ...

    async def pending_recovery_batches(self, binding: StateBinding) -> tuple[SpotRecoveryBatch, ...]:
        ...


class LegacyRecoveryBatchAdapter:
    def _verify_recovery_batches(self, resources):
        for operation, row in resources.get('recovery_batches', {}).items():
            command = SpotRecoveryBatch.from_json(row['command'])
            if (command.operation_id != operation or row['state'] not in ('APPLYING', 'APPLIED')
                    or row['fingerprint'] != fingerprint({'command': command.json(), 'owner': row['owner']})):
                raise StorageError('SPOT_RECOVERY_BATCH_ORIGIN_UNVERIFIED')
            if row['state'] == 'APPLIED':
                for name, commands, complete in (('fill_operations', command.fills, 'APPLIED'),
                                                 ('cashflow_operations', command.cashflows, 'APPLIED'),
                                                 ('acknowledgements', command.acknowledgements, 'ACKED')):
                    for item in commands:
                        actual = resources.get(name, {}).get(item.operation_id)
                        if actual is None or actual['state'] != complete or thaw(actual['command']) != item.json():
                            raise StorageError('SPOT_RECOVERY_BATCH_PROJECTION_UNVERIFIED')

    def _pending_recovery_batches(self):
        self._snapshot()
        return tuple(SpotRecoveryBatch.from_json(row['command']) for row in
                     self._read().resources.get('recovery_batches', {}).values() if row['state'] != 'APPLIED')

    async def pending_recovery_batches(self, binding):
        self._assert_binding(binding)
        return await self._call(self._pending_recovery_batches)

    def _publish_recovery_batch(self, key, owner, resources):
        meta = self._read()
        return FileCheckpointStore._commit(self, meta.cut, owner, key,
                                           tuple((name, encode(value)) for name, value in resources.items()),
                                           observation=True)

    def _batch_projection(self, command, facts, ack_facts, resources):
        commands = resources['commands']
        fills, cashflows = thaw(resources.get('fills', {})), thaw(resources.get('cashflows', {}))
        full_by_intent = {row.intent_id: row for row in command.fills}
        cash_by_bill = {row.bill_id: row for row in command.cashflows}
        for row in command.fills:
            record = commands.get(row.prepare_operation_id)
            if record is None or record['state'] not in ('SEND_UNKNOWN', 'ACKED', 'ACKING', 'FILLING', 'TERMINAL'):
                raise StorageError('SPOT_RECOVERY_BATCH_ORDER_INVALID')
            prepared = PrepareSpot.from_json(record['command'])
            if row.client_order_id not in facts.accepted_wire_ids:
                raise StorageError('SPOT_RECOVERY_BATCH_ORDER_NOT_OBSERVED')
            # Reuse the full native identity/hold checks. Terminal snapshots may
            # contain only the exact already recorded fills, never new trades.
            if record['state'] != 'TERMINAL':
                self._ack_evidence(AckSpot(row.operation_id, row.prepare_operation_id, row.intent_id, row.client_order_id,
                                           row.market, row.side, row.session_id, row.epoch, row.exchange_order_id,
                                           row.observed_at), record)
            elif any(fill.trade_id not in fills for fill in row.fills):
                raise StorageError('SPOT_RECOVERY_BATCH_TERMINAL_NEW_FILL')
            if row.cumulative_base_life > prepared.quantity_base or row.observed_at > facts.observed_at:
                raise StorageError('SPOT_RECOVERY_BATCH_CUMULATIVE_INVALID')
            received_ms = int(row.observed_at.timestamp() * 1000)
            started_ms = int(datetime.fromisoformat(record['prepared_at']).timestamp() * 1000)
            prior_ids = {key for key, value in fills.items() if value['intent_id'] == row.intent_id}
            if not prior_ids.issubset({fill.trade_id for fill in row.fills}):
                raise StorageError('SPOT_RECOVERY_BATCH_FILL_OMITTED')
            for fill in row.fills:
                if (not started_ms <= fill.fill_at_ms <= received_ms
                        or fill.independent_observed_at_ms > received_ms
                        or abs(fill.independent_value_at_ms - fill.fill_at_ms) > self._fill_policy.max_reference_skew_ms
                        or abs(fill.independent_observed_at_ms - fill.fill_at_ms) > self._fill_policy.max_reference_skew_ms
                        or row.side == 'BUY' and fill.price_usdt > prepared.limit_price_usdt
                        or row.side == 'SELL' and fill.price_usdt < prepared.limit_price_usdt):
                    raise StorageError('SPOT_RECOVERY_BATCH_FILL_UNQUALIFIED')
                stable = {'prepare_operation_id': row.prepare_operation_id, 'intent_id': row.intent_id,
                          'exchange_order_id': row.exchange_order_id, 'fill': fill.json()}
                if fill.trade_id in fills and fills[fill.trade_id] != stable:
                    raise StorageConflict('SPOT_RECOVERY_BATCH_FILL_CONFLICT')
                fills[fill.trade_id] = stable
        for row in command.cashflows:
            self._validate_cashflow_value(row)
            stable = row.json()
            if row.observed_at > facts.observed_at:
                raise StorageError('SPOT_RECOVERY_BATCH_CASHFLOW_FUTURE')
            if row.bill_id in cashflows and any(cashflows[row.bill_id][key] != value for key, value in stable.items()
                                                if key not in ('operation_id', 'observed_at')):
                raise StorageConflict('SPOT_RECOVERY_BATCH_CASHFLOW_CONFLICT')
            cashflows.setdefault(row.bill_id, stable)
        # Conflicting permanent operations are rejected before any native write.
        for name, rows in (('fill_operations', command.fills), ('cashflow_operations', command.cashflows)):
            for row in rows:
                prior = resources.get(name, {}).get(row.operation_id)
                if prior is not None and prior['command'] != row.json():
                    raise StorageConflict('SPOT_RECOVERY_BATCH_OPERATION_CONFLICT')
        pending = self._pending_spot_commands()
        if not pending:
            raise StorageError('SPOT_RECOVERY_BATCH_NO_PENDING_ORIGIN')
        acknowledgements = {row.operation_id: row for row in command.acknowledgements}
        evidence = {row.client_order_id: row for row in ack_facts}
        if len(evidence) != len(ack_facts) or len(ack_facts) != len(command.acknowledgements):
            raise StorageError('SPOT_RECOVERY_BATCH_ACK_SCOPE_INVALID')
        expected_acks = set()
        for step in pending:
            original = step.command
            if step.kind == 'FILL':
                current = full_by_intent.get(original.intent_id)
                if (current is None or current.prepare_operation_id != original.prepare_operation_id
                        or current.exchange_order_id != original.exchange_order_id
                        or any(fill not in current.fills for fill in original.fills)):
                    raise StorageConflict('SPOT_RECOVERY_BATCH_ORIGINAL_FILL_CHANGED')
            elif step.kind == 'CASHFLOW':
                if cash_by_bill.get(original.bill_id) != original:
                    raise StorageConflict('SPOT_RECOVERY_BATCH_ORIGINAL_CASHFLOW_CHANGED')
            elif step.kind == 'ACK':
                expected_acks.add(original.operation_id)
                order = evidence.get(original.client_order_id)
                prepared = PrepareSpot.from_json(commands[original.prepare_operation_id]['command'])
                cumulative = sum((Decimal(value['fill']['quantity_base_life']) for value in fills.values()
                                  if value['intent_id'] == original.intent_id), Decimal('0'))
                if (acknowledgements.get(original.operation_id) != original or order is None or order.account != facts
                        or (order.exchange_order_id, order.market, order.side, order.quantity_base_life,
                            order.limit_price_usdt, order.cumulative_base_life) != (
                            original.exchange_order_id, original.market, original.side, prepared.quantity_base,
                            prepared.limit_price_usdt, cumulative)
                        or original.client_order_id not in facts.accepted_wire_ids):
                    raise StorageError('SPOT_RECOVERY_BATCH_ACK_MISMATCH')
                self._ack_evidence(original, commands[original.prepare_operation_id])
            else:
                raise StorageError('SPOT_RECOVERY_BATCH_UNSUPPORTED_PARTIAL')
        if set(acknowledgements) != expected_acks:
            raise StorageError('SPOT_RECOVERY_BATCH_ACK_SCOPE_INVALID')
        for operation in command.supersedes:
            prior = resources.get('recovery_batches', {}).get(operation)
            if prior is None or prior['state'] != 'APPLYING':
                raise StorageError('SPOT_RECOVERY_BATCH_PREDECESSOR_INVALID')
            original = SpotRecoveryBatch.from_json(prior['command'])
            if (any(row.intent_id not in full_by_intent or any(fill not in full_by_intent[row.intent_id].fills
                                                               for fill in row.fills) for row in original.fills)
                    or any(cash_by_bill.get(row.bill_id) != row for row in original.cashflows)
                    or any(row not in command.acknowledgements for row in original.acknowledgements)):
                raise StorageConflict('SPOT_RECOVERY_BATCH_PREDECESSOR_CHANGED')
        self._verify_recovery_projection(commands, fills, cashflows, facts)
        return fills, cashflows, pending

    def _recover_spot_batch(self, command, facts, ack_facts, cut, owner):
        view, meta = self._snapshot(), self._read()
        resources = thaw(meta.resources)
        self._verify_recovery_batches(resources)
        batches = resources.setdefault('recovery_batches', {})
        identity = fingerprint({'command': command.json(), 'owner': owner.json()})
        previous = batches.get(command.operation_id)
        if previous is not None:
            if previous['fingerprint'] != identity:
                raise StorageConflict('SPOT_RECOVERY_BATCH_OPERATION_CONFLICT')
            if previous['state'] == 'APPLIED':
                return SpotRecoveryBatchReceipt(self.binding, command.operation_id, 'APPLIED', True)
        now = utc(self._clock())
        if (self._recovery_policy is None or self._fill_policy is None or meta.ownership.token != owner
                or meta.ownership.mode not in (OwnerMode.ACTIVE, OwnerMode.DRAINING, OwnerMode.RECOVERY_ONLY)
                or now < owner.valid_from):
            raise StorageError('SPOT_RECOVERY_BATCH_OWNER_INVALID')
        if cut != view.cut:
            raise StorageConflict('SPOT_CUT_STALE')
        if (not 0 <= (now - facts.observed_at).total_seconds() * 1000 <= self._recovery_policy.max_fact_age_ms
                or not facts.order_scope_complete or not facts.bill_scope_complete or not facts.runner_scope_complete
                or facts.liability_base_life != 0 or facts.liability_quote_usdt != 0):
            raise StorageError('SPOT_RECOVERY_BATCH_FACTS_UNQUALIFIED')
        if (any(row['state'] != 'APPLIED' for name in ('session_operations', 'lifecycle_operations', 'product_operations')
                for row in resources.get(name, {}).values())
                or any(row['state'] != 'TERMINAL' for row in resources.get('terminal_operations', {}).values())
                or any(row['state'] != 'SETTLED' for row in resources.get('settlement_operations', {}).values())
                or {key for key, row in batches.items() if key != command.operation_id and row['state'] != 'APPLIED'}
                != set(command.supersedes)):
            raise StorageError('SPOT_RECOVERY_BATCH_OTHER_RECOVERY_PENDING')
        if any(batches[key]['owner'] != owner.json() for key in command.supersedes):
            raise StorageError('SPOT_RECOVERY_BATCH_ORIGINAL_OWNER_INVALID')
        fills, cashflows, pending = self._batch_projection(command, facts, ack_facts, resources)
        if any(step.owner != owner or facts.observed_at < step.command.observed_at for step in pending):
            raise StorageError('SPOT_RECOVERY_BATCH_ORIGINAL_OWNER_INVALID')
        batches.setdefault(command.operation_id, {'command': command.json(), 'fingerprint': identity,
                                                  'owner': owner.json(), 'state': 'APPLYING'})
        batches[command.operation_id]['facts'] = facts.json()
        stage = fingerprint({'batch': identity, 'facts': facts.json(), 'cut': cut.json()})
        try:
            self._publish_recovery_batch('batch-start:' + stage, owner, {'recovery_batches': batches})
            timeline = [(row.transfer_at_ms, 0, row.bill_id, row) for row in command.cashflows]
            timeline += [(fill.fill_at_ms, 1, fill.trade_id, (row, fill)) for row in command.fills for fill in row.fills]
            for _, kind, _, event in sorted(timeline, key=lambda row: row[:3]):
                if kind == 0:
                    self._ledger.record_cashflow(event.bill_id, event.currency, event.signed_amount)
                else:
                    row, fill = event
                    if fill.trade_id in self._ledger.trade_ids:
                        continue
                    prefix = tuple(item for item in row.fills if item.trade_id in self._ledger.trade_ids or item == fill)
                    self._ledger.apply_fills_snapshot(row.intent_id, tuple(
                        (item.trade_id, item.quantity_base_life, item.price_usdt, item.fee_currency, item.signed_fee)
                        for item in prefix), sum((item.quantity_base_life for item in prefix), Decimal('0')), require_fees=True)
            # A fault after native money is still an incomplete batch. Never
            # publish final accounting hashes before loss/floor/WAL verification.
            self._publish_recovery_batch('batch-native:' + stage, owner, {'recovery_batches': batches})
            recorded_batches = [SpotRecoveryBatch.from_json(batches[key]['command']) for key in command.supersedes] + [command]
            for row in command.fills:
                for fill in row.fills:
                    self._loss.record('fill:' + fill.trade_id, fill.loss(row.side), session_id=row.session_id,
                                      at_utc=datetime.fromtimestamp(fill.fill_at_ms / 1000, timezone.utc))
                self._subsidy.record_fill_floor(row.intent_id, sum((fill.loss(row.side) for fill in row.fills), Decimal('0')))
            for step in pending:
                origin = step.command
                if step.kind == 'ACK':
                    self._wal.acknowledge(origin.intent_id, origin.exchange_order_id)
                    resources['commands'][origin.prepare_operation_id]['state'] = 'ACKED'
                    resources['acknowledgements'][origin.operation_id]['state'] = 'ACKED'
                elif step.kind == 'FILL':
                    previous_fill = resources['fill_operations'][origin.operation_id]
                    resources['commands'][origin.prepare_operation_id]['state'] = previous_fill['attempt_state_before_apply']
                    previous_fill['state'] = 'APPLIED'
                else:
                    resources['cashflow_operations'][origin.operation_id]['state'] = 'APPLIED'
            for row in (row for batch in recorded_batches for row in batch.fills):
                identity_row = {'command': row.json(), 'fingerprint': fingerprint({'command': row.json(), 'owner': owner.json()}),
                                'owner': owner.json(), 'state': 'APPLIED',
                                'execution_loss_usdt': str(sum((fill.loss(row.side) for fill in row.fills), Decimal('0'))),
                                'attempt_state_before_apply': resources['commands'][row.prepare_operation_id]['state']}
                prior = resources.setdefault('fill_operations', {}).get(row.operation_id)
                if prior is not None and prior['fingerprint'] != identity_row['fingerprint']:
                    raise StorageConflict('SPOT_RECOVERY_BATCH_FILL_OPERATION_CONFLICT')
                resources['fill_operations'].setdefault(row.operation_id, identity_row)
            for row in (row for batch in recorded_batches for row in batch.cashflows):
                identity_row = {'command': row.json(), 'fingerprint': fingerprint({'command': row.json(), 'owner': owner.json()}),
                                'owner': owner.json(), 'state': 'APPLIED'}
                prior = resources.setdefault('cashflow_operations', {}).get(row.operation_id)
                if prior is not None and prior['fingerprint'] != identity_row['fingerprint']:
                    raise StorageConflict('SPOT_RECOVERY_BATCH_CASHFLOW_OPERATION_CONFLICT')
                resources['cashflow_operations'].setdefault(row.operation_id, identity_row)
            self._verify_recovery_projection(resources['commands'], fills, cashflows, facts)
            self._verify_fill_projection(resources['commands'], fills)
            self._verify_capital_projection(resources['commands'], fills, cashflows)
            for row in command.acknowledgements:
                attempt = self._wal.get(row.intent_id)
                if attempt.state != 'ACKED' or attempt.exchange_order_id != row.exchange_order_id:
                    raise StorageError('SPOT_RECOVERY_BATCH_ACK_PUBLICATION_INCOMPLETE')
            verified = self._snapshot()
            for key in command.supersedes:
                batches[key].update(state='APPLIED', completed_by=command.operation_id)
            batches[command.operation_id]['state'] = 'APPLIED'
            updates = {key: resources.get(key, {}) for key in ('commands', 'acknowledgements', 'fill_operations', 'cashflow_operations')}
            updates.update(fills=fills, cashflows=cashflows, recovery_batches=batches, journal_hashes=dict(verified.cut.components))
            self._publish_recovery_batch('batch-done:' + stage, owner, updates)
            self._snapshot()
            return SpotRecoveryBatchReceipt(self.binding, command.operation_id, 'APPLIED', False)
        except BaseException:
            self._uncertain = True
            raise CommitUnknown('SPOT_RECOVERY_BATCH_UNKNOWN') from None

    async def recover_spot_batch(self, command, *, facts, acknowledgements, cut, owner):
        if (not isinstance(command, SpotRecoveryBatch) or not isinstance(facts, SpotFinancialRecoveryFacts)
                or type(acknowledgements) is not tuple or any(not isinstance(row, SpotAckRecoveryFacts) for row in acknowledgements)
                or not isinstance(cut, FinancialCut) or not isinstance(owner, OwnerToken)):
            raise StorageError('SPOT_RECOVERY_BATCH_INVALID')
        self._assert_binding(cut.checkpoint.binding)
        self._assert_binding(facts.binding)
        try:
            return await self._call(self._recover_spot_batch, command, facts, acknowledgements, cut, owner)
        except asyncio.CancelledError:
            self._uncertain = True
            raise
