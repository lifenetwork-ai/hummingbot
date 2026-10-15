"""Closed, terminal, fully accounted inventory cycles on the owning file worker."""
import asyncio
from datetime import datetime
from decimal import Decimal

from hummingbot.strategy_v2.life_liquidity.storage.contracts import (
    CommitUnknown,
    OwnerMode,
    OwnerToken,
    StorageConflict,
    StorageError,
    StorageUnavailable,
    UnsupportedBackend,
    encode,
    fingerprint,
    identifier,
    thaw,
    utc,
)
from hummingbot.strategy_v2.life_liquidity.storage.financial import FinancialCut, PrepareSpot, SpotFillObservation
from hummingbot.strategy_v2.life_liquidity.storage.recovery import SpotFinancialRecoveryFacts
from hummingbot.strategy_v2.life_liquidity.storage.settlement import SettleSpotInventory, SpotSettlementReceipt


class LegacySettlementAdapter:
    def _inventory_evidence(self, command, resources):
        by_intent = {row['command']['intent_id']: row for row in resources['commands'].values()}
        fills = resources.get('fills', {})
        actuals, identities, selected = {}, [], []
        for key in command.intent_ids:
            row = by_intent.get(key)
            if row is None or row['state'] != 'TERMINAL':
                raise StorageError('SPOT_SETTLEMENT_TERMINAL_REQUIRED')
            original = PrepareSpot.from_json(row['command'])
            if original.budget_scope == 'EXIT':
                raise StorageError('EXIT_BUDGET_INVENTORY_CYCLE_FORBIDDEN')
            wal, native = self._wal.get(key), self._ledger.reservation_state(key)
            rows = [(tid, fill) for tid, fill in fills.items() if fill['intent_id'] == key]
            if (wal.state != 'TERMINAL' or not wal.exchange_terminal_observed or wal.exchange_order_id is None
                    or native is None or native.state != 'TERMINAL' or native.remaining_base != 0 or native.filled_base <= 0
                    or native.intent != self._intent(original)
                    or (wal.client_order_id, wal.reservation_id, wal.session_id, wal.epoch, wal.slot_market, wal.slot_side,
                        wal.slot_level) != (original.client_order_id, key, original.session_id, original.epoch,
                                            original.market, original.side, original.quote_level)
                    or not self._subsidy.matches_intent_session(key, original.session_id)
                    or sum((Decimal(value['fill']['quantity_base_life']) for _, value in rows), Decimal('0')) != native.filled_base
                    or any(value['exchange_order_id'] != wal.exchange_order_id for _, value in rows)):
                raise StorageError('SPOT_SETTLEMENT_MEMBER_UNVERIFIED')
            actuals[key] = Decimal('0')
            identities.append([key, wal.client_order_id, wal.exchange_order_id, original.session_id, original.epoch])
            selected.extend((tid, key, original.side, SpotFillObservation.from_json(value['fill'])) for tid, value in rows)
        selected.sort(key=lambda value: (value[3].fill_at_ms, value[0]))
        base = quote = Decimal('0')
        for _, key, side, fill in selected:
            sign = Decimal('1') if side == 'BUY' else Decimal('-1')
            base += sign * fill.quantity_base_life + (fill.signed_fee if fill.fee_currency == 'LIFE' else Decimal('0'))
            quote += -sign * fill.quantity_base_life * fill.price_usdt + (
                fill.signed_fee if fill.fee_currency == 'USDT' else Decimal('0'))
            actuals[key] += fill.loss(side)
        if base != 0:
            raise StorageError('SPOT_SETTLEMENT_INVENTORY_NOT_FLAT')
        actuals[selected[-1][1]] += max(Decimal('0'), -quote - sum(actuals.values(), Decimal('0')))
        proof = fingerprint({'identities': identities, 'fills': [
            [tid, key, side, fill.json()] for tid, key, side, fill in selected]})
        return proof, actuals

    @staticmethod
    def _native_cycle(row):
        return {'id': row['command']['cycle_id'], 'members': list(row['command']['intent_ids']),
                'proof': row['proof'], 'actuals': dict(row['actuals'])}

    def _verify_inventory_projection(self, resources):
        cycles = {row['id']: row for row in self._subsidy.verified_inventory_cycles()}
        expected = {}
        for row in resources.get('settlement_operations', {}).values():
            command = SettleSpotInventory(row['command']['operation_id'], row['command']['cycle_id'],
                                          tuple(row['command']['intent_ids']))
            proof, actuals = self._inventory_evidence(command, resources)
            if row['proof'] != proof or dict(row['actuals']) != {key: str(value) for key, value in actuals.items()}:
                raise StorageUnavailable('SPOT_SETTLEMENT_PROJECTION_UNVERIFIED')
            native = self._native_cycle(row)
            if row['state'] == 'SETTLED' or command.cycle_id in cycles:
                expected[command.cycle_id] = native
                if cycles.get(command.cycle_id) != native:
                    raise StorageUnavailable('SPOT_SETTLEMENT_NATIVE_UNVERIFIED')
        if cycles != expected:
            raise StorageUnavailable('SPOT_SETTLEMENT_NATIVE_SCOPE_MISMATCH')

    def _settled_inventory_member(self, resources, intent_id):
        cycles = {row['id']: row for row in self._subsidy.verified_inventory_cycles()}
        for row in resources.get('settlement_operations', {}).values():
            if intent_id in row['command']['intent_ids'] and row['state'] == 'SETTLED':
                return cycles.get(row['command']['cycle_id']) == self._native_cycle(row)
        return False

    def _pending_inventory_subsidy(self, resources, session_id, now):
        session = daily = campaign = Decimal('0')
        cycles = {row['id']: row for row in self._subsidy.verified_inventory_cycles()}
        by_intent = {row['command']['intent_id']: row for row in resources['commands'].values()}
        for row in resources.get('settlement_operations', {}).values():
            if row['state'] == 'SETTLED' or row['command']['cycle_id'] not in cycles:
                continue
            if cycles[row['command']['cycle_id']] != self._native_cycle(row):
                raise StorageUnavailable('SPOT_SETTLEMENT_NATIVE_UNVERIFIED')
            for key, before in row['committed_before'].items():
                extra = max(Decimal('0'), Decimal(before) - Decimal(row['actuals'][key]))
                original = by_intent[key]
                campaign += extra
                session += extra if original['command']['session_id'] == session_id else Decimal('0')
                daily += extra if datetime.fromisoformat(original['prepared_at']).date() == now.date() else Decimal('0')
        return session, daily, campaign

    def _settlement_receipt(self, row, state, replayed):
        return SpotSettlementReceipt(self.binding, row['command']['operation_id'], row['command']['cycle_id'],
                                     {key: Decimal(value) for key, value in row['actuals'].items()},
                                     row['proof'], state, replayed, self.atomic_scope)

    def _publish_settlement(self, key, owner, operations, hashes):
        meta = self._read()
        return self._commit(meta.cut, owner, key, (
            ('settlement_operations', encode(operations)), ('journal_hashes', encode(hashes))), observation=True)

    def _settle_inventory(self, command, facts, cut, owner, resume):
        if self._settlement_policy is None:
            raise UnsupportedBackend('SPOT_SETTLEMENT_POLICY_REQUIRED')
        view, meta = self._snapshot(), self._read()
        now = utc(self._clock())
        operations = thaw(meta.resources.get('settlement_operations', {}))
        prior = operations.get(command.operation_id)
        expected = fingerprint({'command': command.json(), 'owner': owner.json()})
        if (meta.ownership.token != owner or meta.ownership.mode not in (
                OwnerMode.ACTIVE, OwnerMode.DRAINING, OwnerMode.RECOVERY_ONLY) or now < owner.valid_from):
            raise StorageError('SPOT_SETTLEMENT_OWNER_INVALID')
        if prior is not None:
            if prior['fingerprint'] != expected:
                raise StorageConflict('SPOT_SETTLEMENT_OPERATION_CONFLICT')
            if prior['state'] == 'SETTLED':
                return self._settlement_receipt(prior, 'SETTLED', True)
            if not resume:
                raise StorageError('SPOT_SETTLEMENT_RESUME_REQUIRED')
        elif resume:
            raise StorageError('SPOT_SETTLEMENT_RECOVERY_NOT_STARTED')
        elif self._cold_unarmed:
            raise StorageError('SPOT_SETTLEMENT_COLD_UNARMED')
        if view.cut != cut:
            raise StorageConflict('SPOT_CUT_STALE')
        if (self._ancillary_recovery_pending(meta.resources)
                or any(row['state'] in ('PREPARING', 'ARMING', 'ACKING', 'FILLING', 'TERMINATING')
                       for row in meta.resources['commands'].values())
                or any(row['state'] != 'APPLIED' for row in meta.resources.get('cashflow_operations', {}).values())
                or any(op != command.operation_id and row['state'] != 'SETTLED' for op, row in operations.items())):
            raise StorageError('SPOT_SETTLEMENT_OTHER_RECOVERY_PENDING')
        if (not 0 <= (now - facts.observed_at).total_seconds() * 1000 <= self._settlement_policy.max_fact_age_ms
                or not facts.order_scope_complete or not facts.bill_scope_complete or not facts.runner_scope_complete
                or facts.liability_base_life != 0 or facts.liability_quote_usdt != 0):
            raise StorageError('SPOT_SETTLEMENT_FACTS_UNQUALIFIED')
        latest_fill = max((datetime.fromisoformat(row['command']['observed_at'])
                           for row in meta.resources.get('fill_operations', {}).values()), default=owner.valid_from)
        known = {row['command']['intent_id'] for row in meta.resources['commands'].values()}
        if (facts.observed_at < latest_fill or not self._ledger.reservation_ids.issubset(known)
                or not {row.intent_id for row in self._wal.all_records()}.issubset(known)
                or not self._subsidy.verified_intent_ids().issubset(known)):
            raise StorageError('SPOT_SETTLEMENT_ACCOUNT_CUT_UNPROVEN')
        self._verify_recovery_projection(meta.resources['commands'], meta.resources.get('fills', {}),
                                         meta.resources.get('cashflows', {}), facts)
        self._verify_fill_projection(meta.resources['commands'], meta.resources.get('fills', {}))
        self._verify_capital_projection(meta.resources['commands'], meta.resources.get('fills', {}),
                                        meta.resources.get('cashflows', {}))
        proof, actuals = self._inventory_evidence(command, meta.resources)
        if prior is None:
            if any(set(row['command']['intent_ids']) & set(command.intent_ids)
                   or row['command']['cycle_id'] == command.cycle_id for row in operations.values()):
                raise StorageConflict('SPOT_SETTLEMENT_MEMBER_OR_CYCLE_REUSED')
            by_intent = {row['command']['intent_id']: row for row in meta.resources['commands'].values()}
            before = {}
            for key in command.intent_ids:
                original = by_intent[key]
                prepared = PrepareSpot.from_json(original['command'])
                if self._subsidy.reserved_for(key, session_id=prepared.session_id) != prepared.subsidy_quote_usdt:
                    raise StorageError('SPOT_SETTLEMENT_HOLD_UNPROVEN')
                before[key] = str(max(prepared.subsidy_quote_usdt, sum((
                    SpotFillObservation.from_json(row['fill']).loss(prepared.side)
                    for row in meta.resources.get('fills', {}).values() if row['intent_id'] == key), Decimal('0'))))
            operations[command.operation_id] = {'command': command.json(), 'fingerprint': expected, 'state': 'SETTLING',
                                                'proof': proof, 'actuals': {key: str(v) for key, v in actuals.items()},
                                                'committed_before': before, 'facts': facts.json()}
        else:
            if prior['proof'] != proof or prior['actuals'] != {key: str(v) for key, v in actuals.items()}:
                raise StorageConflict('SPOT_SETTLEMENT_RECOVERY_CONFLICT')
            prior['recovery_facts'] = facts.json()
        proposed = {**meta.resources, 'settlement_operations': operations}
        self._verify_inventory_projection(proposed)
        stage = 'settle-resume:' + fingerprint({'operation': command.operation_id, 'facts': facts.json(), 'cut': cut.json()})
        try:
            self._publish_settlement(stage, owner, operations, dict(view.cut.components))
            self._subsidy._settle_inventory_cycle(command.cycle_id, proof=proof, actuals=actuals)
            if self._native_cycle(operations[command.operation_id]) not in self._subsidy.verified_inventory_cycles():
                raise StorageError('SPOT_SETTLEMENT_PUBLICATION_INCOMPLETE')
            verified = self._snapshot()
            operations[command.operation_id]['state'] = 'SETTLED'
            self._verify_inventory_projection({**meta.resources, 'settlement_operations': operations})
            self._publish_settlement('settle-done:' + command.operation_id, owner, operations, dict(verified.cut.components))
            return self._settlement_receipt(operations[command.operation_id], 'SETTLED', False)
        except BaseException:
            self._uncertain = True
            raise CommitUnknown('SPOT_SETTLEMENT_UNKNOWN') from None

    async def _settlement_command(self, command, facts, cut, owner, resume):
        if (not isinstance(command, SettleSpotInventory) or not isinstance(facts, SpotFinancialRecoveryFacts)
                or not isinstance(cut, FinancialCut) or not isinstance(owner, OwnerToken)):
            raise StorageError('SPOT_SETTLEMENT_COMMAND_INVALID')
        self._assert_binding(cut.checkpoint.binding)
        self._assert_binding(facts.binding)
        try:
            return await self._call(self._settle_inventory, command, facts, cut, owner, resume)
        except asyncio.CancelledError:
            self._uncertain = True
            raise

    async def settle_inventory(self, command, *, facts, cut, owner):
        return await self._settlement_command(command, facts, cut, owner, False)

    async def resume_inventory_settlement(self, command, *, facts, cut, owner):
        return await self._settlement_command(command, facts, cut, owner, True)

    def _lookup_inventory_settlement(self, operation_id):
        self._snapshot()
        row = self._read().resources.get('settlement_operations', {}).get(operation_id)
        return None if row is None else self._settlement_receipt(
            row, 'SETTLED' if row['state'] == 'SETTLED' else 'RECOVERY_REQUIRED', True)

    async def lookup_inventory_settlement(self, operation_id, binding):
        identifier(operation_id)
        self._assert_binding(binding)
        return await self._call(self._lookup_inventory_settlement, operation_id)
