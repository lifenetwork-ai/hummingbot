"""Validate pending side effects before a joint cash batch; release follows cash.

Operation IDs route existing immutable requests, never admit a new terminal or
inventory cycle. Native before/after subsets must agree with the original
request and the independently qualified full account projection.
"""
from decimal import Decimal

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError, fingerprint, thaw
from hummingbot.strategy_v2.life_liquidity.storage.financial import PrepareSpot
from hummingbot.strategy_v2.life_liquidity.storage.settlement import SettleSpotInventory


class LegacyJointPendingAdapter:
    def _joint_pending_resources(self, resources, command, *, owner=None):
        values = thaw(resources)
        owner = self._read().ownership.token if owner is None else owner
        for key in command.spot_terminal_operations:
            row = resources.get('terminal_operations', {}).get(key)
            if row is None or row['state'] not in ('TERMINATING', 'TERMINAL'):
                raise StorageError('JOINT_TERMINAL_ORIGIN_REQUIRED')
            original = row['command']
            prepared = resources['commands'].get(original['prepare_operation_id'])
            if (original['operation_id'] != key or prepared is None or prepared.get('original_owner') != owner.json()
                    or row['fingerprint'] != fingerprint({'command': original, 'owner': owner.json()})
                    or row['core_fingerprint'] != fingerprint({'command': {
                        **thaw(original), 'facts': {k: thaw(original['facts'][k]) for k in (
                            'exchange_state', 'cumulative_base_life', 'quantity_base_life', 'limit_price_usdt', 'fills')}},
                        'owner': owner.json()})
                    or prepared['state'] != row['state']):
                raise StorageError('JOINT_TERMINAL_ORIGIN_INVALID')
            if row['state'] == 'TERMINATING':
                # Admission-only view. The real receipt/overlay stays pending
                # throughout monetary application and a failed terminal resume.
                values['commands'][original['prepare_operation_id']]['state'] = 'SEND_UNKNOWN'
        for key in command.spot_settlement_operations:
            row = resources.get('settlement_operations', {}).get(key)
            if row is None or row['state'] not in ('SETTLING', 'SETTLED'):
                raise StorageError('JOINT_SETTLEMENT_ORIGIN_REQUIRED')
            original = row['command']
            if original['operation_id'] != key or row['fingerprint'] != fingerprint({'command': original, 'owner': owner.json()}):
                raise StorageError('JOINT_SETTLEMENT_ORIGIN_INVALID')
            selected = SettleSpotInventory(original['operation_id'], original['cycle_id'], tuple(original['intent_ids']))
            proof, actuals = self._inventory_evidence(selected, resources)
            if row['proof'] != proof or dict(row['actuals']) != {k: str(v) for k, v in actuals.items()}:
                raise StorageError('JOINT_SETTLEMENT_ORIGIN_INVALID')
            values['settlement_operations'][key]['state'] = 'SETTLED'
        return values

    def _joint_partial_side_effect_cut(self, view, resources, command):
        if command is None or not (command.spot_terminal_operations or command.spot_settlement_operations):
            return False
        self._joint_pending_resources(resources, command)
        actual = dict(view.cut.components)
        # Neither terminal release nor cycle settlement changes the session or
        # execution-loss journal. The cash saga resumes its own partial writes
        # separately; side-effect coverage cannot adopt an unrelated debit.
        if any(actual[key] != resources['journal_hashes'][key] for key in ('session', 'loss')):
            raise StorageError('JOINT_PENDING_UNRELATED_JOURNAL_CHANGED')
        if not command.spot_terminal_operations and actual['wal'] != resources['journal_hashes']['wal']:
            raise StorageError('JOINT_PENDING_UNRELATED_JOURNAL_CHANGED')
        self._verify_fill_projection(resources['commands'], resources.get('fills', {}))
        self._verify_lifecycle_projection(resources)
        owner = self._read().ownership.token
        fills, _ = self._joint_fill_projection(command, resources, owner)
        flows, _ = self._joint_cashflow_projection(command, resources, owner, allow_new=True)

        class Scope:
            accepted_wire_ids = tuple(v.client_order_id for v in command.spot_fills)
            runner_wire_ids = ()
        terminal_intents = frozenset(resources['terminal_operations'][key]['command']['intent_id']
                                     for key in command.spot_terminal_operations)
        self._verify_native_financial_subset(resources['commands'], fills, flows, Scope(), terminal_intents=terminal_intents)
        by_intent = {r['command']['intent_id']: r for r in resources['commands'].values()}
        if set(by_intent) != {r.intent_id for r in self._wal.all_records()} or set(by_intent) != self._ledger.reservation_ids:
            raise StorageError('JOINT_PENDING_NATIVE_SCOPE_CHANGED')
        for key, prepared in by_intent.items():
            if key in terminal_intents:
                continue
            wal = self._wal.get(key)
            if wal.state != prepared['state'] or wal.exchange_terminal_observed != (prepared['state'] == 'TERMINAL'):
                raise StorageError('JOINT_PENDING_UNRELATED_WAL_CHANGED')
        for key in command.spot_terminal_operations:
            row = resources['terminal_operations'][key]
            original = row['command']
            prepared = PrepareSpot.from_json(by_intent[original['intent_id']]['command'])
            wal = self._wal.get(prepared.intent_id)
            native = self._ledger.reservation_state(prepared.intent_id)
            quantity = Decimal(original['facts']['cumulative_base_life'])
            expected = {v['trade_id']: thaw(v) for v in original['facts']['fills']}
            known = {tid: thaw(v['fill']) for tid, v in resources.get('fills', {}).items()
                     if v['intent_id'] == prepared.intent_id}
            if (known != expected or native.intent != self._intent(prepared) or native.filled_base != quantity
                    or native.state not in ('OPEN', 'CANCEL_PENDING', 'TERMINAL')
                    or native.remaining_base != (0 if native.state == 'TERMINAL' else prepared.quantity_base - quantity)
                    or Decimal(row['principal_remaining_base']) != prepared.quantity_base - quantity
                    or (wal.client_order_id, wal.reservation_id, wal.session_id, wal.epoch, wal.slot_market, wal.slot_side, wal.slot_level)
                    != (prepared.client_order_id, prepared.intent_id, prepared.session_id, prepared.epoch,
                        prepared.market, prepared.side, prepared.quote_level)
                    or wal.state not in ('SEND_UNKNOWN', 'ACKED', 'TERMINAL')
                    or wal.exchange_order_id not in (None, original['exchange_order_id'])
                    or wal.exchange_terminal_observed and wal.exchange_order_id != original['exchange_order_id']
                    or wal.state == 'TERMINAL' and (not wal.exchange_terminal_observed or native.state != 'TERMINAL')):
                raise StorageError('JOINT_TERMINAL_NATIVE_SUBSET_CHANGED')
            reserved = self._subsidy.reserved_for(prepared.intent_id, session_id=prepared.session_id)
            if reserved != prepared.subsidy_quote_usdt:
                cost = self._exit_cost(resources, prepared) if prepared.budget_scope == 'EXIT' else Decimal('0')
                if (quantity != 0 and prepared.budget_scope != 'EXIT' or wal.state != 'TERMINAL'
                        or native.state != 'TERMINAL'
                        or not self._subsidy.matches_settled(prepared.intent_id, cost, session_id=prepared.session_id)):
                    raise StorageError('JOINT_TERMINAL_SUBSIDY_SUBSET_CHANGED')
        self._verify_inventory_projection(resources)
        return True
