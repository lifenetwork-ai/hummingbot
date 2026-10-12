"""Complete a durable ACK/fill/cashflow saga without rearm, replacement or transport.

All live journal access stays on LegacySpotStore's owning bounded worker. Current
account facts must come from the pending authenticated bridge; flags are not
authentication. Unrelated partial work and unmatched native state remain blocked.
"""
import asyncio
from datetime import datetime, timezone
from decimal import Decimal

from hummingbot.strategy_v2.life_liquidity.storage.capital import CashflowSpot
from hummingbot.strategy_v2.life_liquidity.storage.contracts import (
    CommitUnknown,
    OwnerMode,
    OwnerToken,
    StorageConflict,
    StorageError,
    UnsupportedBackend,
    fingerprint,
    thaw,
    utc,
)
from hummingbot.strategy_v2.life_liquidity.storage.financial import (
    AckSpot,
    FillSpot,
    FinancialCut,
    PrepareSpot,
    SpotFillObservation,
)
from hummingbot.strategy_v2.life_liquidity.storage.recovery import SpotAckRecoveryFacts, SpotFinancialRecoveryFacts


class LegacyFinancialRecoveryAdapter:
    def _financial_recovery_guard(self, command, facts, cut, owner, kind):
        if self._recovery_policy is None:
            raise UnsupportedBackend('SPOT_FINANCIAL_RECOVERY_POLICY_REQUIRED')
        view = self._snapshot()
        meta = self._read()
        now = utc(self._clock())
        if (meta.ownership.token != owner or meta.ownership.mode not in (
                OwnerMode.ACTIVE, OwnerMode.DRAINING, OwnerMode.RECOVERY_ONLY) or now < owner.valid_from):
            raise StorageError('SPOT_FINANCIAL_RECOVERY_OWNER_INVALID')
        resource = {'fill': 'fill_operations', 'cash': 'cashflow_operations', 'ack': 'acknowledgements'}[kind]
        previous = meta.resources.get(resource, {}).get(command.operation_id)
        if previous is None:
            raise StorageError('SPOT_FINANCIAL_RECOVERY_NOT_STARTED')
        if previous['fingerprint'] != fingerprint({'command': command.json(), 'owner': owner.json()}):
            raise StorageConflict('SPOT_FINANCIAL_RECOVERY_OPERATION_CONFLICT')
        # A completed operation is readback only. It cannot trigger a new write
        # even when fresh facts would describe a different account cut.
        if previous['state'] == ('ACKED' if kind == 'ack' else 'APPLIED'):
            return view, meta, previous
        if view.cut != cut:
            raise StorageConflict('SPOT_CUT_STALE')
        expected_phase = {'fill': 'FILLING', 'cash': 'CASHFLOWING', 'ack': 'ACKING'}[kind]
        if previous['state'] != expected_phase:
            raise StorageError('SPOT_FINANCIAL_RECOVERY_PHASE_INVALID')
        target = command.prepare_operation_id if kind in ('fill', 'ack') else None
        for key, record in meta.resources['commands'].items():
            if record['state'] in ('PREPARING', 'ARMING', 'ACKING', 'FILLING', 'TERMINATING'):
                if kind not in ('fill', 'ack') or key != target or record['state'] != expected_phase:
                    raise StorageError('SPOT_FINANCIAL_OTHER_RECOVERY_PENDING')
        for name in ('fill_operations', 'cashflow_operations', 'acknowledgements'):
            for op, row in meta.resources.get(name, {}).items():
                complete = 'ACKED' if name == 'acknowledgements' else 'APPLIED'
                if row['state'] != complete and (name != resource or op != command.operation_id):
                    raise StorageError('SPOT_FINANCIAL_OTHER_RECOVERY_PENDING')
        if any(row['state'] != 'TERMINAL' for row in meta.resources.get('terminal_operations', {}).values()):
            raise StorageError('SPOT_FINANCIAL_OTHER_RECOVERY_PENDING')
        if any(row['state'] != 'SETTLED' for row in meta.resources.get('settlement_operations', {}).values()):
            raise StorageError('SPOT_FINANCIAL_OTHER_RECOVERY_PENDING')
        if kind in ('fill', 'ack') and meta.resources['commands'][target]['state'] != expected_phase:
            raise StorageError('SPOT_FINANCIAL_RECOVERY_PHASE_INVALID')
        age_ms = (now - facts.observed_at).total_seconds() * 1000
        if (not 0 <= age_ms <= self._recovery_policy.max_fact_age_ms
                or facts.observed_at < command.observed_at
                or not facts.order_scope_complete or not facts.bill_scope_complete or not facts.runner_scope_complete
                or facts.liability_base_life != 0 or facts.liability_quote_usdt != 0):
            raise StorageError('SPOT_FINANCIAL_RECOVERY_FACTS_UNQUALIFIED')
        return view, meta, previous

    def _verify_recovery_projection(self, commands, fills, cashflows, facts):
        # Rebuild both final and actually applied intermediate accounting cuts;
        # a hash mismatch alone never licenses adoption of arbitrary native money.
        capital = self._capital_ledger(commands, fills, cashflows)
        if (capital.life_balance != facts.account_base_life or capital.usdt_balance != facts.account_quote_usdt
                or set(facts.account_trade_ids) != set(fills) or set(facts.account_cashflow_ids) != set(cashflows)):
            raise StorageError('SPOT_FINANCIAL_RECOVERY_ACCOUNT_MISMATCH')
        native_trades = self._ledger.trade_ids
        native_cash = self._ledger.cashflow_events
        baseline = self._read().resources
        if (not set(baseline.get('fills', {})).issubset(native_trades) or not native_trades.issubset(fills)
                or not set(baseline.get('cashflows', {})).issubset(native_cash) or not set(native_cash).issubset(cashflows)
                or self._ledger.joint_cash_events):
            raise StorageError('SPOT_FINANCIAL_RECOVERY_NATIVE_SCOPE_MISMATCH')
        for trade_id in native_trades:
            data = fills[trade_id]
            row = SpotFillObservation.from_json(data['fill'])
            if not self._ledger.matches_recorded_fill(data['intent_id'], trade_id, row.quantity_base_life,
                                                      row.price_usdt, row.fee_currency, row.signed_fee):
                raise StorageError('SPOT_FINANCIAL_RECOVERY_NATIVE_FILL_MISMATCH')
        for bill_id, native in native_cash.items():
            row = cashflows[bill_id]
            if native != (row['currency'], Decimal(row['signed_amount'])):
                raise StorageError('SPOT_FINANCIAL_RECOVERY_NATIVE_CASHFLOW_MISMATCH')
        intermediate = self._capital_ledger(commands, {key: fills[key] for key in native_trades},
                                            {key: cashflows[key] for key in native_cash})
        if intermediate.life_balance != self._ledger.life_balance or intermediate.usdt_balance != self._ledger.usdt_balance:
            raise StorageError('SPOT_FINANCIAL_RECOVERY_NATIVE_BALANCE_MISMATCH')
        rows = self._wal.all_records()
        known_wires = {row.client_order_id for row in rows}
        sent_wires = {row.client_order_id for row in rows if row.state in ('SEND_UNKNOWN', 'ACKED', 'TERMINAL')}
        if not set(facts.accepted_wire_ids).issubset(sent_wires) or not set(facts.runner_wire_ids).issubset(known_wires):
            raise StorageError('SPOT_FINANCIAL_RECOVERY_ORDER_SCOPE_MISMATCH')
        for record in commands.values():
            original = PrepareSpot.from_json(record['command'])
            reservation = self._ledger.reservation_state(original.intent_id)
            wal = self._wal.get(original.intent_id)
            if (reservation is None or reservation.intent != self._intent(original)
                    or (wal.client_order_id, wal.reservation_id, wal.session_id, wal.epoch,
                        wal.slot_market, wal.slot_side, wal.slot_level) != (
                        original.client_order_id, original.intent_id, original.session_id, original.epoch,
                        original.market, original.side, original.quote_level)):
                raise StorageError('SPOT_FINANCIAL_RECOVERY_NATIVE_IDENTITY_MISMATCH')
            native_quantity = sum((Decimal(fills[key]['fill']['quantity_base_life']) for key in native_trades
                                   if fills[key]['intent_id'] == original.intent_id), Decimal('0'))
            if (reservation.filled_base != native_quantity or native_quantity > original.quantity_base
                    or record['state'] == 'TERMINAL' and (reservation.state != 'TERMINAL' or wal.state != 'TERMINAL')
                    or record['state'] != 'TERMINAL' and (reservation.state == 'TERMINAL' or wal.state == 'TERMINAL'
                                                          or reservation.remaining_base != original.quantity_base - native_quantity)):
                raise StorageError('SPOT_FINANCIAL_RECOVERY_NATIVE_HOLD_MISMATCH')

    def _resume_ack(self, command, facts, cut, owner):
        view, meta, previous = self._financial_recovery_guard(command, facts.account, cut, owner, 'ack')
        if previous['state'] == 'ACKED':
            return self._ack_receipt(self.binding, previous['command'], 'ACKED', True)
        commands = thaw(meta.resources['commands'])
        acknowledgements = thaw(meta.resources['acknowledgements'])
        record = commands[command.prepare_operation_id]
        prepared = PrepareSpot.from_json(record['command'])
        if ((facts.client_order_id, facts.exchange_order_id, facts.market, facts.side,
             facts.quantity_base_life, facts.limit_price_usdt) != (
                command.client_order_id, command.exchange_order_id, command.market, command.side,
                prepared.quantity_base, prepared.limit_price_usdt)
                or facts.cumulative_base_life != self._ledger.reservation_state(command.intent_id).filled_base
                or command.client_order_id not in facts.account.accepted_wire_ids):
            raise StorageError('SPOT_ACK_RECOVERY_ORDER_MISMATCH')
        self._ack_evidence(command, record)
        fills, cashflows = meta.resources.get('fills', {}), meta.resources.get('cashflows', {})
        self._verify_recovery_projection(commands, fills, cashflows, facts.account)
        self._verify_fill_projection(commands, fills)
        self._verify_capital_projection(commands, fills, cashflows)
        acknowledgements[command.operation_id]['recovery_facts'] = facts.json()
        stage = 'ack-resume:' + fingerprint({'operation': command.operation_id, 'facts': facts.json(), 'cut': cut.json()})
        try:
            self._publish_ack(stage, owner, commands, acknowledgements, dict(view.cut.components))
            self._wal.acknowledge(command.intent_id, command.exchange_order_id)
            wal = self._wal.get(command.intent_id)
            if wal.state != 'ACKED' or wal.exchange_order_id != command.exchange_order_id:
                raise StorageError('SPOT_ACK_PUBLICATION_INCOMPLETE')
            verified = self._snapshot()
            record['state'] = 'ACKED'
            acknowledgements[command.operation_id]['state'] = 'ACKED'
            self._publish_ack('ack-done:' + command.operation_id, owner, commands, acknowledgements,
                              dict(verified.cut.components))
            return self._ack_receipt(self.binding, command.json(), 'ACKED', False)
        except BaseException:
            self._uncertain = True
            raise CommitUnknown('SPOT_ACK_RESUME_UNKNOWN') from None

    def _resume_fill(self, command, facts, cut, owner):
        view, meta, previous = self._financial_recovery_guard(command, facts, cut, owner, 'fill')
        if previous['state'] == 'APPLIED':
            return self._fill_receipt(self.binding, previous, 'APPLIED', True)
        commands = thaw(meta.resources['commands'])
        operations = thaw(meta.resources['fill_operations'])
        fills = thaw(meta.resources.get('fills', {}))
        record = commands[command.prepare_operation_id]
        original_state = previous.get('attempt_state_before_apply')
        wal = self._wal.get(command.intent_id)
        if original_state not in ('SEND_UNKNOWN', 'ACKED') or wal.state != original_state:
            raise StorageError('SPOT_FINANCIAL_RECOVERY_WAL_STATE_MISMATCH')
        if command.client_order_id not in facts.accepted_wire_ids:
            raise StorageError('SPOT_FINANCIAL_RECOVERY_ORDER_NOT_OBSERVED')
        self._ack_evidence(AckSpot(command.operation_id, command.prepare_operation_id, command.intent_id,
                                   command.client_order_id, command.market, command.side, command.session_id,
                                   command.epoch, command.exchange_order_id, command.observed_at), record)
        prepared = PrepareSpot.from_json(record['command'])
        for row in command.fills:
            stable = {'prepare_operation_id': command.prepare_operation_id, 'intent_id': command.intent_id,
                      'exchange_order_id': command.exchange_order_id, 'fill': row.json()}
            if row.trade_id in fills and fills[row.trade_id] != stable:
                raise StorageConflict('SPOT_FINANCIAL_RECOVERY_FILL_CONFLICT')
            fills[row.trade_id] = stable
        prior_ids = {key for key, row in meta.resources.get('fills', {}).items() if row['intent_id'] == command.intent_id}
        if not prior_ids.issubset({row.trade_id for row in command.fills}) or command.cumulative_base_life > prepared.quantity_base:
            raise StorageError('SPOT_FINANCIAL_RECOVERY_SNAPSHOT_INCOMPLETE')
        if Decimal(previous['execution_loss_usdt']) != sum((row.loss(command.side) for row in command.fills), Decimal('0')):
            raise StorageError('SPOT_FINANCIAL_RECOVERY_LOSS_CONFLICT')
        self._verify_recovery_projection(commands, fills, meta.resources.get('cashflows', {}), facts)
        operations[command.operation_id]['recovery_facts'] = facts.json()
        stage = 'fill-resume:' + fingerprint({'operation': command.operation_id, 'facts': facts.json(), 'cut': cut.json()})
        try:
            self._publish_fills(stage, owner, commands, operations, thaw(meta.resources.get('fills', {})), dict(view.cut.components))
            self._ledger.apply_fills_snapshot(command.intent_id, tuple(
                (row.trade_id, row.quantity_base_life, row.price_usdt, row.fee_currency, row.signed_fee)
                for row in command.fills), command.cumulative_base_life, require_fees=True)
            if (self._ledger.reservation_state(command.intent_id).filled_base != command.cumulative_base_life
                    or any(not self._ledger.matches_recorded_fill(command.intent_id, row.trade_id, row.quantity_base_life,
                                                                  row.price_usdt, row.fee_currency, row.signed_fee)
                           for row in command.fills)):
                raise StorageError('SPOT_FILL_PUBLICATION_INCOMPLETE')
            for row in command.fills:
                self._loss.record('fill:' + row.trade_id, row.loss(command.side), session_id=command.session_id,
                                  at_utc=datetime.fromtimestamp(row.fill_at_ms / 1000, timezone.utc))
            self._subsidy.record_fill_floor(command.intent_id, Decimal(previous['execution_loss_usdt']))
            self._verify_fill_projection(commands, fills)
            self._verify_capital_projection(commands, fills, meta.resources.get('cashflows', {}))
            verified = self._snapshot()
            record['state'] = original_state
            operations[command.operation_id]['state'] = 'APPLIED'
            self._publish_fills('fill-done:' + command.operation_id, owner, commands, operations, fills, dict(verified.cut.components))
            return self._fill_receipt(self.binding, operations[command.operation_id], 'APPLIED', False)
        except BaseException:
            self._uncertain = True
            raise CommitUnknown('SPOT_FILL_RESUME_UNKNOWN') from None

    def _resume_cashflow(self, command, facts, cut, owner):
        view, meta, previous = self._financial_recovery_guard(command, facts, cut, owner, 'cash')
        if previous['state'] == 'APPLIED':
            return self._cashflow_receipt(self.binding, previous, 'APPLIED', True)
        self._validate_cashflow_value(command)
        operations = thaw(meta.resources['cashflow_operations'])
        cashflows = thaw(meta.resources.get('cashflows', {}))
        stable = command.json()
        if command.bill_id in cashflows and any(cashflows[command.bill_id][key] != value for key, value in stable.items()
                                                if key not in ('operation_id', 'observed_at')):
            raise StorageConflict('SPOT_FINANCIAL_RECOVERY_CASHFLOW_CONFLICT')
        cashflows.setdefault(command.bill_id, stable)
        self._verify_recovery_projection(meta.resources['commands'], meta.resources.get('fills', {}), cashflows, facts)
        operations[command.operation_id]['recovery_facts'] = facts.json()
        stage = 'cash-resume:' + fingerprint({'operation': command.operation_id, 'facts': facts.json(), 'cut': cut.json()})
        try:
            self._publish_cashflow(stage, owner, operations, thaw(meta.resources.get('cashflows', {})), dict(view.cut.components))
            self._ledger.record_cashflow(command.bill_id, command.currency, command.signed_amount)
            self._verify_capital_projection(meta.resources['commands'], meta.resources.get('fills', {}), cashflows)
            verified = self._snapshot()
            operations[command.operation_id]['state'] = 'APPLIED'
            self._publish_cashflow('cash-done:' + command.operation_id, owner, operations, cashflows, dict(verified.cut.components))
            return self._cashflow_receipt(self.binding, operations[command.operation_id], 'APPLIED', False)
        except BaseException:
            self._uncertain = True
            raise CommitUnknown('SPOT_CASHFLOW_RESUME_UNKNOWN') from None

    async def _financial_resume_command(self, command, facts, cut, owner, kind):
        expected = {'fill': FillSpot, 'cash': CashflowSpot, 'ack': AckSpot}[kind]
        fact_type = SpotAckRecoveryFacts if kind == 'ack' else SpotFinancialRecoveryFacts
        if (not isinstance(command, expected) or not isinstance(facts, fact_type)
                or not isinstance(cut, FinancialCut) or not isinstance(owner, OwnerToken)):
            raise StorageError('SPOT_FINANCIAL_RECOVERY_COMMAND_INVALID')
        self._assert_binding(cut.checkpoint.binding)
        self._assert_binding(facts.account.binding if kind == 'ack' else facts.binding)
        try:
            function = {'fill': self._resume_fill, 'cash': self._resume_cashflow, 'ack': self._resume_ack}[kind]
            return await self._call(function, command, facts, cut, owner)
        except asyncio.CancelledError:
            self._uncertain = True
            raise

    async def resume_fill(self, command, *, facts, cut, owner):
        return await self._financial_resume_command(command, facts, cut, owner, 'fill')

    async def resume_cashflow(self, command, *, facts, cut, owner):
        return await self._financial_resume_command(command, facts, cut, owner, 'cash')

    async def resume_ack(self, command, *, facts, cut, owner):
        return await self._financial_resume_command(command, facts, cut, owner, 'ack')
