"""Exact-order terminal saga on legacy journals; cold resume never rearms.

Used only by LegacySpotStore's bounded owning worker. All external facts require
the pending authenticated account/order/bill/runner bridge; these methods do not
turn caller-supplied completeness flags into exchange authentication.
"""
import asyncio
from datetime import datetime
from decimal import Decimal

from hummingbot.strategy_v2.life_liquidity.storage.contracts import (
    CommitUnknown,
    OwnerMode,
    OwnerToken,
    StorageConflict,
    StorageError,
    UnsupportedBackend,
    encode,
    fingerprint,
    identifier,
    thaw,
    utc,
)
from hummingbot.strategy_v2.life_liquidity.storage.file import FileCheckpointStore
from hummingbot.strategy_v2.life_liquidity.storage.financial import FinancialCut, PrepareSpot, SpotFillObservation
from hummingbot.strategy_v2.life_liquidity.storage.terminal import TerminalReceipt, TerminalSpot


class LegacyTerminalAdapter:
    @staticmethod
    def _exit_cost(resources, prepared):
        return sum((SpotFillObservation.from_json(r['fill']).loss(prepared.side)
                    for r in resources.get('fills', {}).values() if r['intent_id'] == prepared.intent_id), Decimal('0'))

    @staticmethod
    def _joint_known_order(row, authority):
        if authority is None:
            return False
        return any((source['claim']['intent_id'], source['claim']['wire_id'], source['claim']['session_id'],
                    source['claim']['epoch'], source['claim']['product']) == (
                        row.intent_id, row.client_order_id, row.session_id, row.epoch, 'SPOT')
                   and source['state'] in ('live', 'partially_filled', 'filled', 'canceled')
                   for source in authority.journal._state['bundle']['orders'])

    @staticmethod
    def _terminal_core(command):
        data = command.json()
        data['facts'] = {key: data['facts'][key] for key in (
            'exchange_state', 'cumulative_base_life', 'quantity_base_life', 'limit_price_usdt', 'fills')}
        return data

    def _terminal_guard(self, view, owner, *, resume=False, operation_id=None):
        if self._terminal_policy is None:
            raise UnsupportedBackend('SPOT_TERMINAL_POLICY_REQUIRED')
        if not resume:
            return self._ack_guard(view, owner)
        meta = self._read()
        if any(row['state'] != 'APPLIED' for row in meta.resources.get('joint_cash_operations', {}).values()):
            raise StorageError('SPOT_TERMINAL_OTHER_RECOVERY_PENDING')
        now = utc(self._clock())
        if (meta.ownership.token != owner or meta.ownership.mode not in (
                OwnerMode.ACTIVE, OwnerMode.DRAINING, OwnerMode.RECOVERY_ONLY)
                or now < owner.valid_from):
            raise StorageError('SPOT_TERMINAL_RECOVERY_OWNER_INVALID')
        operations = meta.resources.get('terminal_operations', {})
        if operation_id not in operations:
            raise StorageError('SPOT_TERMINAL_RECOVERY_NOT_STARTED')
        for op, row in operations.items():
            if op != operation_id and row['state'] != 'TERMINAL':
                raise StorageError('SPOT_TERMINAL_OTHER_RECOVERY_PENDING')
        target = operations[operation_id]['command']['prepare_operation_id']
        if (any(row['state'] in ('PREPARING', 'ARMING', 'ACKING', 'FILLING', 'ABORTING', 'CANCELING')
                or key != target and row['state'] == 'TERMINATING'
                for key, row in meta.resources['commands'].items())
                or any(row['state'] != 'APPLIED' for row in meta.resources.get('cashflow_operations', {}).values())
                or any(row['state'] != 'APPLIED' for row in meta.resources.get('lifecycle_operations', {}).values())
                or any(row['state'] != 'APPLIED' for row in meta.resources.get('product_operations', {}).values())
                or any(row['state'] != 'APPLIED' for row in meta.resources.get('session_operations', {}).values())
                or any(row['state'] != 'SETTLED' for row in meta.resources.get('settlement_operations', {}).values())):
            raise StorageError('SPOT_TERMINAL_OTHER_RECOVERY_PENDING')
        return meta

    def _terminal_evidence(self, command, view, meta, *, resume=False, joint_authority=None):
        facts = command.facts
        self._assert_binding(facts.binding)
        if (not facts.order_scope_complete or not facts.bill_scope_complete or not facts.runner_scope_complete
                or facts.liability_base_life != 0 or facts.liability_quote_usdt != 0):
            raise StorageError('SPOT_TERMINAL_SCOPE_INCOMPLETE')
        age_ms = (utc(self._clock()) - facts.observed_at).total_seconds() * 1000
        if not 0 <= age_ms <= self._terminal_policy.max_fact_age_ms:
            raise StorageError('SPOT_TERMINAL_FACTS_STALE')
        record = meta.resources['commands'].get(command.prepare_operation_id)
        states = ('SEND_UNKNOWN', 'ACKED', 'TERMINATING', 'TERMINAL') if resume else ('SEND_UNKNOWN', 'ACKED')
        if record is None or record['state'] not in states:
            raise StorageError('SPOT_TERMINAL_ATTEMPT_NOT_SENT')
        prepared = PrepareSpot.from_json(record['command'])
        if (command.prepare_operation_id, command.intent_id, command.client_order_id, command.market,
                command.side, command.session_id, command.epoch) != (
                prepared.operation_id, prepared.intent_id, prepared.client_order_id, prepared.market,
                prepared.side, prepared.session_id, prepared.epoch):
            raise StorageError('SPOT_TERMINAL_IDENTITY_MISMATCH')
        if (facts.quantity_base_life, facts.limit_price_usdt) != (prepared.quantity_base, prepared.limit_price_usdt):
            raise StorageError('SPOT_TERMINAL_ORDER_PAYLOAD_MISMATCH')
        if facts.observed_at < datetime.fromisoformat(record['prepared_at']):
            raise StorageError('SPOT_TERMINAL_OBSERVATION_TIME_INVALID')
        self._verify_fill_projection(meta.resources['commands'], meta.resources.get('fills', {}))
        self._verify_capital_projection(meta.resources['commands'], meta.resources.get('fills', {}),
                                        meta.resources.get('cashflows', {}))
        if (facts.account_base_life != self._ledger.life_balance or facts.account_quote_usdt != self._ledger.usdt_balance
                or set(facts.account_trade_ids) != self._ledger.trade_ids
                or set(facts.account_cashflow_ids) != set(self._ledger.cashflow_events)):
            raise StorageError('SPOT_TERMINAL_ACCOUNT_MISMATCH')
        expected_fills = {key: value for key, value in meta.resources.get('fills', {}).items()
                          if value['intent_id'] == prepared.intent_id}
        if (set(expected_fills) != {row.trade_id for row in facts.fills}
                or any(expected_fills[row.trade_id]['fill'] != row.json()
                       or expected_fills[row.trade_id]['exchange_order_id'] != command.exchange_order_id for row in facts.fills)):
            raise StorageError('SPOT_TERMINAL_FILLS_INCOMPLETE')
        rows = self._wal.all_records()
        known_wires = {row.client_order_id for row in rows}
        pending_wires = {row.client_order_id for row in rows if row.intent_id != prepared.intent_id
                         and row.state in ('PREPARED', 'SEND_UNKNOWN', 'ACKED')}
        known_exchange = {row.exchange_order_id for row in rows if row.intent_id != prepared.intent_id
                          and row.exchange_order_id is not None and row.state in ('SEND_UNKNOWN', 'ACKED')}
        if (command.client_order_id in facts.pending_wire_ids or command.exchange_order_id in facts.pending_exchange_order_ids
                or not set(facts.pending_wire_ids).issubset(pending_wires)
                or not set(facts.pending_exchange_order_ids).issubset(known_exchange)
                or not set(facts.runner_wire_ids).issubset(known_wires)
                or any(row.intent_id != prepared.intent_id and row.state == 'SEND_UNKNOWN'
                       and not self._joint_known_order(row, joint_authority) for row in rows)):
            raise StorageError('SPOT_TERMINAL_ORDER_OR_RUNNER_SCOPE_INVALID')
        self._verify_lifecycle_projection(meta.resources)
        for value in meta.resources['commands'].values():
            original = PrepareSpot.from_json(value['command'])
            wal = self._wal.get(original.intent_id)
            reservation = self._ledger.reservation_state(original.intent_id)
            if (reservation is None or reservation.intent != self._intent(original)
                    or (wal.client_order_id, wal.reservation_id, wal.session_id, wal.epoch,
                        wal.slot_market, wal.slot_side, wal.slot_level) != (
                        original.client_order_id, original.intent_id, original.session_id, original.epoch,
                        original.market, original.side, original.quote_level)):
                raise StorageError('SPOT_TERMINAL_NATIVE_IDENTITY_MISMATCH')
            if value['state'] == 'ABORTED_BEFORE_SEND':
                # The original lifecycle receipt and exact native zero-fill
                # postconditions must verify before treating this sibling as closed.
                if (wal.state != 'ABORTED_BEFORE_SEND' or reservation.state != 'TERMINAL'
                        or reservation.remaining_base != 0 or reservation.filled_base != 0
                        or any(row['intent_id'] == original.intent_id for row in meta.resources.get('fills', {}).values())):
                    raise StorageError('SPOT_TERMINAL_NATIVE_HOLD_MISMATCH')
                continue
            expected_quantity = sum((Decimal(row['fill']['quantity_base_life']) for row in meta.resources.get('fills', {}).values()
                                     if row['intent_id'] == original.intent_id), Decimal('0'))
            if (reservation.filled_base != expected_quantity
                    or reservation.state != 'TERMINAL' and reservation.remaining_base != original.quantity_base - expected_quantity
                    or original.intent_id != prepared.intent_id and value['state'] != 'TERMINAL'
                    and (wal.state == 'TERMINAL' or reservation.state == 'TERMINAL')):
                raise StorageError('SPOT_TERMINAL_NATIVE_HOLD_MISMATCH')
            if value['state'] == 'TERMINAL' and (wal.state != 'TERMINAL' or reservation.state != 'TERMINAL'):
                raise StorageError('SPOT_TERMINAL_NATIVE_HOLD_MISMATCH')
            if value['state'] != 'TERMINAL':
                settled_target = (resume and original.intent_id == prepared.intent_id
                                  and (expected_quantity == 0 or original.budget_scope == 'EXIT')
                                  and wal.state == 'TERMINAL' and reservation.state == 'TERMINAL'
                                  and self._subsidy.matches_settled(original.intent_id, self._exit_cost(meta.resources, original), session_id=original.session_id))
                if (not settled_target and self._subsidy.reserved_for(original.intent_id, session_id=original.session_id)
                        != original.subsidy_quote_usdt):
                    raise StorageError('SPOT_TERMINAL_SUBSIDY_HOLD_MISMATCH')
        target = self._wal.get(prepared.intent_id)
        reservation = self._ledger.reservation_state(prepared.intent_id)
        if (target.state not in ('SEND_UNKNOWN', 'ACKED', 'TERMINAL')
                or target.exchange_order_id not in (None, command.exchange_order_id)
                or any(row.intent_id != prepared.intent_id and row.exchange_order_id == command.exchange_order_id for row in rows)
                or reservation.filled_base != facts.cumulative_base_life
                or not resume and (target.state == 'TERMINAL' or reservation.state == 'TERMINAL')):
            raise StorageError('SPOT_TERMINAL_NATIVE_STATE_MISMATCH')
        return prepared, reservation

    def _pending_terminal_principal(self, resources):
        base = quote = Decimal('0')
        for row in resources.get('terminal_operations', {}).values():
            if row['state'] == 'TERMINAL':
                continue
            command = row['command']
            reservation = self._ledger.reservation_state(command['intent_id'])
            if reservation is not None and reservation.state == 'TERMINAL':
                remaining = Decimal(row['principal_remaining_base'])
                if command['side'] == 'BUY':
                    quote += remaining * Decimal(command['facts']['limit_price_usdt'])
                else:
                    base += remaining
        return base, quote

    def _verify_terminal_projection(self, resources):
        completed = set()
        for row in resources.get('terminal_operations', {}).values():
            if row['state'] != 'TERMINAL':
                continue
            command = row['command']
            record = resources['commands'][command['prepare_operation_id']]
            original = PrepareSpot.from_json(record['command'])
            wal = self._wal.get(command['intent_id'])
            reservation = self._ledger.reservation_state(command['intent_id'])
            if (record['state'] != 'TERMINAL' or original.intent_id != command['intent_id']
                    or wal.state != 'TERMINAL' or not wal.exchange_terminal_observed
                    or wal.exchange_order_id != command['exchange_order_id']
                    or reservation is None or reservation.state != 'TERMINAL' or reservation.remaining_base != 0
                    or reservation.intent != self._intent(original)
                    or reservation.filled_base != Decimal(command['facts']['cumulative_base_life'])):
                raise StorageError('SPOT_TERMINAL_PROJECTION_UNVERIFIED')
            if reservation.filled_base == 0:
                if not self._subsidy.matches_settled(original.intent_id, Decimal('0'), session_id=original.session_id):
                    raise StorageError('SPOT_TERMINAL_PROJECTION_UNVERIFIED')
            elif original.budget_scope == 'EXIT':
                if not self._subsidy.matches_settled(original.intent_id, self._exit_cost(resources, original), session_id=original.session_id):
                    raise StorageError('EXIT_TERMINAL_BUDGET_UNVERIFIED')
            elif (not self._settled_inventory_member(resources, original.intent_id)
                  and self._subsidy.reserved_for(original.intent_id, session_id=original.session_id) != original.subsidy_quote_usdt):
                raise StorageError('SPOT_TERMINAL_PROJECTION_UNVERIFIED')
            completed.add(command['prepare_operation_id'])
        if completed != {key for key, row in resources['commands'].items() if row['state'] == 'TERMINAL'}:
            raise StorageError('SPOT_TERMINAL_PROJECTION_UNVERIFIED')

    def _terminal_receipt(self, row, state, replayed):
        command = row['command']
        original = self._read().resources['commands'][command['prepare_operation_id']]['command']
        return TerminalReceipt(self.binding, command['operation_id'], command['intent_id'], command['exchange_order_id'],
                               state, state == 'TERMINAL' and (Decimal(command['facts']['cumulative_base_life']) == 0
                                                               or original.get('budget_scope', 'QUOTE') == 'EXIT'),
                               replayed, self.atomic_scope)

    def _lookup_terminal(self, operation_id):
        view = self._snapshot()
        meta = self._read()
        row = meta.resources.get('terminal_operations', {}).get(operation_id)
        if row is None:
            return None
        partial = row['state'] != 'TERMINAL' or dict(view.cut.components) != dict(meta.resources['journal_hashes'])
        return self._terminal_receipt(row, 'RECOVERY_REQUIRED' if partial else 'TERMINAL', True)

    async def lookup_terminal(self, operation_id, binding):
        self._assert_binding(binding)
        identifier(operation_id)
        return await self._call(self._lookup_terminal, operation_id)

    def _publish_terminal(self, key, owner, commands, operations, hashes):
        meta = self._read()
        return FileCheckpointStore._commit(self, meta.cut, owner, key, (
            ('commands', encode(commands)), ('terminal_operations', encode(operations)),
            ('journal_hashes', encode(hashes))), observation=True)

    def _complete_terminal(self, command, owner, commands, operations):
        row = operations[command.operation_id]
        original = PrepareSpot.from_json(commands[command.prepare_operation_id]['command'])
        # Persist the exchange observation before changing native reservations.
        self._wal.mark_exchange_terminal_observed(command.intent_id, command.exchange_order_id)
        observed = self._wal.get(command.intent_id)
        if not observed.exchange_terminal_observed or observed.exchange_order_id != command.exchange_order_id:
            raise StorageError('SPOT_TERMINAL_OBSERVATION_PUBLICATION_INCOMPLETE')
        current = self._ledger.reservation_state(command.intent_id)
        if current.state != 'TERMINAL':
            self._ledger.confirm_terminal(command.intent_id, cumulative_filled=command.facts.cumulative_base_life,
                                          fills_reconciled=True, exchange_state=command.facts.exchange_state)
        current = self._ledger.reservation_state(command.intent_id)
        if current.state != 'TERMINAL' or current.remaining_base != 0 or current.filled_base != command.facts.cumulative_base_life:
            raise StorageError('SPOT_TERMINAL_RESERVATION_PUBLICATION_INCOMPLETE')
        self._wal.mark_terminal(command.intent_id, command.exchange_order_id)
        wal = self._wal.get(command.intent_id)
        if wal.state != 'TERMINAL' or wal.exchange_order_id != command.exchange_order_id or not wal.exchange_terminal_observed:
            raise StorageError('SPOT_TERMINAL_WAL_PUBLICATION_INCOMPLETE')
        if command.facts.cumulative_base_life == 0:
            self._subsidy.settle_zero_fill_terminal(command.intent_id, wal=self._wal, reservations=self._ledger)
            if not self._subsidy.matches_settled(command.intent_id, Decimal('0'), session_id=original.session_id):
                raise StorageError('SPOT_TERMINAL_SUBSIDY_PUBLICATION_INCOMPLETE')
        elif original.budget_scope == 'EXIT':
            # Complete independently valued terminal fills qualify this exact
            # reducing order's actual cost; principal/WAL are already terminal.
            actual = self._exit_cost(self._read().resources, original)
            self._subsidy._reconcile(command.intent_id, actual_cost_quote=actual, release_proven=True)
            if not self._subsidy.matches_settled(command.intent_id, actual, session_id=original.session_id):
                raise StorageError('EXIT_TERMINAL_BUDGET_UNVERIFIED')
        else:
            # Quote termination alone cannot settle inventory/exit economic cost.
            if self._subsidy.reserved_for(command.intent_id, session_id=original.session_id) != original.subsidy_quote_usdt:
                raise StorageError('SPOT_TERMINAL_FILLED_SUBSIDY_UNVERIFIED')
        self._wal.assert_verified()
        self._ledger.verify_memory()
        self._verify_fill_projection(commands, self._read().resources.get('fills', {}))
        self._verify_capital_projection(commands, self._read().resources.get('fills', {}), self._read().resources.get('cashflows', {}))
        commands[command.prepare_operation_id]['state'] = 'TERMINAL'
        row['state'] = 'TERMINAL'
        self._publish_terminal('term-done:' + command.operation_id, owner, commands, operations, self._hashes(self._raw()))
        return self._terminal_receipt(row, 'TERMINAL', False)

    def _confirm_terminal(self, command, cut, owner, resume, *, joint_authority=None):
        view = self._snapshot()
        if not resume and joint_authority is not None:
            if self._terminal_policy is None:
                raise UnsupportedBackend('SPOT_TERMINAL_POLICY_REQUIRED')
            meta = self._joint_recovery_observation_guard(view, owner, joint_authority,
                                                          joint_authority.journal._state['proof_id'])
        else:
            meta = self._terminal_guard(view, owner, resume=resume, operation_id=command.operation_id)
        operations = thaw(meta.resources.get('terminal_operations', {}))
        commands = thaw(meta.resources['commands'])
        payload_hash = fingerprint({'command': command.json(), 'owner': owner.json()})
        core_hash = fingerprint({'command': self._terminal_core(command), 'owner': owner.json()})
        previous = operations.get(command.operation_id)
        if previous is not None:
            if previous['core_fingerprint'] != core_hash or not resume and previous['fingerprint'] != payload_hash:
                raise StorageConflict('SPOT_TERMINAL_OPERATION_CONFLICT')
            if previous['state'] == 'TERMINAL':
                # Exact readback; no release write, rearm or new qualification.
                return self._terminal_receipt(previous, 'TERMINAL', True)
            if not resume:
                raise StorageError('SPOT_TERMINAL_RESUME_REQUIRED')
        elif resume:
            raise StorageError('SPOT_TERMINAL_RECOVERY_NOT_STARTED')
        if view.cut != cut:
            raise StorageConflict('SPOT_CUT_STALE')
        prepared, reservation = self._terminal_evidence(command, view, meta, resume=resume, joint_authority=joint_authority)
        if previous is None:
            operations[command.operation_id] = {
                'command': command.json(), 'fingerprint': payload_hash, 'core_fingerprint': core_hash,
                'state': 'TERMINATING', 'principal_remaining_base': str(reservation.remaining_base)}
            commands[prepared.operation_id]['state'] = 'TERMINATING'
            stage = 'term-start:' + command.operation_id
        else:
            # Fresh recovery observations are recorded separately, preserving
            # the original immutable operation and never adopting a new owner.
            previous['recovery_facts'] = command.facts.json()
            stage = 'term-resume:' + fingerprint({'operation': command.operation_id, 'cut': cut.json(), 'facts': command.facts.json()})
        try:
            self._publish_terminal(stage, owner, commands, operations, dict(view.cut.components))
            return self._complete_terminal(command, owner, commands, operations)
        except BaseException:
            self._uncertain = True
            raise CommitUnknown('SPOT_TERMINAL_APPLICATION_UNKNOWN') from None

    async def _terminal_command(self, command, cut, owner, resume):
        if not isinstance(command, TerminalSpot) or not isinstance(cut, FinancialCut) or not isinstance(owner, OwnerToken):
            raise StorageError('SPOT_COMMAND_INVALID')
        self._assert_binding(cut.checkpoint.binding)
        self._assert_binding(command.facts.binding)
        try:
            return await self._call(self._confirm_terminal, command, cut, owner, resume)
        except asyncio.CancelledError:
            self._uncertain = True
            raise

    async def confirm_terminal(self, command, *, cut, owner):
        return await self._terminal_command(command, cut, owner, False)

    async def resume_terminal(self, command, *, cut, owner):
        return await self._terminal_command(command, cut, owner, True)
