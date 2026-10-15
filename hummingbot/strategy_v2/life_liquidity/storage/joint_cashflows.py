"""Approved external transfers in a joint wallet replay; never settlement income."""
from datetime import datetime, timezone
from decimal import Decimal

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageConflict, StorageError, fingerprint, thaw


class LegacyJointCashflowAdapter:
    def _joint_cashflow_projection(self, command, resources, owner):
        flows = thaw(resources.get('cashflows', {}))
        operations = thaw(resources.get('cashflow_operations', {}))
        for value in command.spot_cashflows:
            self._validate_cashflow_value(value)
            original = operations.get(value.operation_id)
            if (original is None or original['command'] != value.json()
                    or original['state'] not in ('CASHFLOWING', 'APPLIED')
                    or original['fingerprint'] != fingerprint({'command': value.json(), 'owner': original['owner']})
                    or original['state'] != 'APPLIED' and original['owner'] != owner.json()):
                raise StorageError('JOINT_CASHFLOW_ORIGIN_REQUIRED')
            prior = flows.get(value.bill_id)
            if prior is not None and prior != value.json():
                raise StorageConflict('JOINT_CASHFLOW_HISTORY_CHANGED')
            flows[value.bill_id] = value.json()
            original['state'] = 'APPLIED'
        supplied = {v.bill_id: v.json() for v in command.spot_cashflows}
        for original in operations.values():
            if original['state'] == 'APPLIED':
                continue
            value = original['command']
            expected = supplied.get(value['bill_id'])
            if (expected is None or original['owner'] != owner.json()
                    or original['fingerprint'] != fingerprint({'command': value, 'owner': original['owner']})
                    or any(expected[k] != v for k, v in value.items() if k not in ('operation_id', 'observed_at'))):
                raise StorageError('JOINT_CASHFLOW_ORIGIN_REQUIRED')
            original['state'] = 'APPLIED'
        return flows, operations

    def _joint_partial_cashflow_cut(self, view, resources, command):
        if command is None or not command.spot_cashflows:
            return False
        actual, original = dict(view.cut.components), dict(resources['journal_hashes'])
        if any(actual[k] != value for k, value in original.items() if k != 'reservations'):
            return False
        values = dict(resources.get('cashflows', {}))
        candidates = {v.bill_id: v for v in command.spot_cashflows}
        for key, native in self._ledger.cashflow_events.items():
            row = candidates.get(key)
            if row is None or native != (row.currency, row.signed_amount):
                return False
            values[key] = row.json()
        self._verify_fill_projection(resources['commands'], resources.get('fills', {}))
        self._verify_capital_projection(resources['commands'], resources.get('fills', {}), values)
        return True

    def _joint_partial_fill_cut(self, view, resources, command):
        if command is None or not command.spot_fills:
            return False
        actual, original = dict(view.cut.components), dict(resources['journal_hashes'])
        if any(actual[k] != value for k, value in original.items() if k in ('session', 'wal')):
            return False
        fills, _ = self._joint_fill_projection(command, resources, self._read().ownership.token)
        flows, _ = self._joint_cashflow_projection(command, resources, self._read().ownership.token)
        # The final account comparison is performed against the qualified joint
        # command. This helper verifies only the actual native monetary subset.

        class Scope:
            accepted_wire_ids = tuple(row.client_order_id for row in command.spot_fills)
            runner_wire_ids = ()
        self._verify_native_financial_subset(resources['commands'], fills, flows, Scope())
        for row in command.spot_fills:
            expected = sum((v.loss(row.side) for v in row.fills), Decimal('0'))
            actual_floor = self._subsidy.verified_fill_floors().get(row.intent_id, Decimal('0'))
            if actual_floor > expected:
                raise StorageError('JOINT_PENDING_FILL_FLOOR_CHANGED')
            for fill in row.fills:
                native = self._loss.verified_events().get('fill:' + fill.trade_id)
                if native is not None and not self._loss.matches_event('fill:' + fill.trade_id, fill.loss(row.side),
                                                                       session_id=row.session_id, at_utc=datetime.fromtimestamp(fill.fill_at_ms / 1000, timezone.utc)):
                    raise StorageError('JOINT_PENDING_FILL_LOSS_CHANGED')
        return True

    def _joint_partial_ack_cut(self, view, resources, command):
        if command is None or not command.spot_acks:
            return False
        actual, original = dict(view.cut.components), dict(resources['journal_hashes'])
        if any(actual[k] != value for k, value in original.items() if k != 'wal'):
            return False
        for ack in command.spot_acks:
            operation = resources.get('acknowledgements', {}).get(ack.operation_id)
            if (operation is None or operation['command'] != ack.json()
                    or operation['fingerprint'] != fingerprint({'command': ack.json(), 'owner': operation['owner']})
                    or operation['state'] != 'ACKED' and operation['owner'] != self._read().ownership.token.json()):
                raise StorageError('JOINT_ACK_ORIGIN_INVALID')
            if operation['state'] == 'ACKING':
                self._ack_evidence(ack, resources['commands'][ack.prepare_operation_id])
        self._verify_fill_projection(resources['commands'], resources.get('fills', {}))
        self._verify_capital_projection(resources['commands'], resources.get('fills', {}), resources.get('cashflows', {}))
        return True

    @staticmethod
    def _joint_native_cashflows(command):
        return tuple((row.bill_id, row.currency, row.signed_amount, row.transfer_at_ms) for row in command.spot_cashflows)

    @staticmethod
    def _joint_recovery_resources(resources, command):
        values = thaw(resources)
        covered = {row.bill_id for row in command.spot_cashflows}
        values['cashflow_operations'] = {k: v for k, v in values.get('cashflow_operations', {}).items()
                                         if v['command']['bill_id'] not in covered}
        for ack in command.spot_acks:
            operation = values.get('acknowledgements', {}).get(ack.operation_id)
            if operation is not None and operation['state'] == 'ACKING':
                values['commands'][ack.prepare_operation_id]['state'] = 'SEND_UNKNOWN'
        intents = {row.intent_id for row in command.spot_fills}
        for operation in values.get('fill_operations', {}).values():
            if operation['state'] == 'FILLING' and operation['command']['intent_id'] in intents:
                values['commands'][operation['command']['prepare_operation_id']]['state'] = operation['attempt_state_before_apply']
        return values
