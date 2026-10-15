"""Spot accounting within a joint settlement; no terminal or send authority."""
import json
from datetime import datetime
from decimal import Decimal

from hummingbot.strategy_v2.life_liquidity.risk import ReservationLedger
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageConflict, StorageError, fingerprint, thaw
from hummingbot.strategy_v2.life_liquidity.storage.financial import AckSpot, PrepareSpot


class LegacyJointFillAdapter:
    def _verify_joint_fill_operations(self, command, resources):
        for row in command.spot_fills:
            operation = resources.get('fill_operations', {}).get(row.operation_id)
            if (operation is None or operation['state'] != 'APPLIED' or thaw(operation['command']) != row.json()
                    or operation['fingerprint'] != fingerprint({'command': row.json(), 'owner': operation['owner']})
                    or Decimal(operation['execution_loss_usdt']) != sum((f.loss(row.side) for f in row.fills), Decimal('0'))):
                raise StorageError('JOINT_SPOT_FILL_PROJECTION_UNVERIFIED')

    def _joint_fill_projection(self, command, resources, owner):
        fills = thaw(resources.get('fills', {}))
        operations = thaw(resources.get('fill_operations', {}))
        for row in command.spot_fills:
            record = resources['commands'].get(row.prepare_operation_id)
            if record is None or record['state'] not in ('SEND_UNKNOWN', 'ACKED', 'TERMINAL', 'TERMINATING', 'FILLING', 'ACKING'):
                raise StorageError('JOINT_SPOT_FILL_ATTEMPT_INVALID')
            prepared = PrepareSpot.from_json(record['command'])
            if record['state'] not in ('TERMINAL', 'TERMINATING'):
                self._ack_evidence(AckSpot(row.operation_id, row.prepare_operation_id, row.intent_id,
                                           row.client_order_id, row.market, row.side, row.session_id, row.epoch,
                                           row.exchange_order_id, row.observed_at), record)
            elif ((row.intent_id, row.client_order_id, row.market, row.side, row.session_id, row.epoch) != (
                    prepared.intent_id, prepared.client_order_id, prepared.market, prepared.side,
                    prepared.session_id, prepared.epoch)
                    or any(f.trade_id not in fills for f in row.fills)
                    or self._wal.get(row.intent_id).exchange_order_id != row.exchange_order_id):
                raise StorageError('JOINT_SPOT_TERMINAL_NEW_FILL')
            current = self._ledger.reservation_state(row.intent_id)
            if (row.observed_at > command.observed_at
                    or not current.filled_base <= row.cumulative_base_life <= prepared.quantity_base):
                raise StorageError('JOINT_SPOT_FILL_CUMULATIVE_INVALID')
            original_ids = {key for key, value in fills.items() if value['intent_id'] == row.intent_id}
            if not original_ids.issubset({f.trade_id for f in row.fills}):
                raise StorageError('JOINT_SPOT_FILL_HISTORY_INCOMPLETE')
            started = int(datetime.fromisoformat(record['prepared_at']).timestamp() * 1000)
            observed = int(row.observed_at.timestamp() * 1000)
            for fill in row.fills:
                if (not started <= fill.fill_at_ms <= observed
                        or fill.independent_observed_at_ms > observed
                        or abs(fill.independent_value_at_ms - fill.fill_at_ms) > self._fill_policy.max_reference_skew_ms
                        or abs(fill.independent_observed_at_ms - fill.fill_at_ms) > self._fill_policy.max_reference_skew_ms
                        or row.side == 'BUY' and fill.price_usdt > prepared.limit_price_usdt
                        or row.side == 'SELL' and fill.price_usdt < prepared.limit_price_usdt):
                    raise StorageError('JOINT_SPOT_FILL_REFERENCE_INVALID')
                stable = {'prepare_operation_id': row.prepare_operation_id, 'intent_id': row.intent_id,
                          'exchange_order_id': row.exchange_order_id, 'fill': fill.json()}
                if fill.trade_id in fills and fills[fill.trade_id] != stable:
                    raise StorageConflict('JOINT_SPOT_FILL_IDENTITY_CONFLICT')
                fills[fill.trade_id] = stable
            identity = fingerprint({'command': row.json(), 'owner': owner.json()})
            previous = operations.get(row.operation_id)
            if previous is not None and (previous['fingerprint'] != identity or previous['state'] not in ('APPLIED', 'FILLING')):
                raise StorageConflict('JOINT_SPOT_FILL_OPERATION_CONFLICT')
            operations.setdefault(row.operation_id, {
                'command': row.json(), 'fingerprint': identity, 'owner': owner.json(), 'state': 'APPLIED',
                'execution_loss_usdt': str(sum((f.loss(row.side) for f in row.fills), Decimal('0'))),
                'attempt_state_before_apply': record['state'],
            })
        by_intent = {row.intent_id: row for row in command.spot_fills}
        for operation in operations.values():
            if operation['state'] == 'APPLIED':
                continue
            original = operation['command']
            row = by_intent.get(original['intent_id'])
            current = {} if row is None else {v.trade_id: v.json() for v in row.fills}
            if (row is None or operation['state'] != 'FILLING' or operation['owner'] != owner.json()
                    or operation['fingerprint'] != fingerprint({'command': original, 'owner': operation['owner']})
                    or any(current.get(v['trade_id']) != v for v in original['fills'])
                    or operation['attempt_state_before_apply'] not in ('SEND_UNKNOWN', 'ACKED')):
                raise StorageError('JOINT_PENDING_FILL_ORIGIN_INVALID')
            operation['state'] = 'APPLIED'
        return fills, operations

    @staticmethod
    def _joint_fill_snapshots(command):
        return tuple((row.intent_id, tuple((f.trade_id, f.quantity_base_life, f.price_usdt,
                                           f.fee_currency, f.signed_fee, f.fill_at_ms) for f in row.fills))
                     for row in command.spot_fills)

    def _joint_fill_native_preflight(self, command, before, current, resume):
        # Detached decoding and causal simulation perform no I/O. Only the
        # existing owning ledger later publishes money. This is not a writer.
        staged = ReservationLedger._restore_snapshot(before['reservations'], path=None, limits=self._risk_limits)
        try:
            staged.apply_joint_snapshot(self._joint_fill_snapshots(command),
                                        {e.event_id: e.signed_amount_usdt for e in command.events},
                                        self._joint_native_cashflows(command))
        except (ValueError, ArithmeticError):
            raise StorageError('JOINT_SPOT_NATIVE_REPLAY_INVALID') from None
        after_wallet = staged._payload(staged.life_balance, staged.usdt_balance, staged._reservations,
                                       staged._trades, staged._account_events)
        if (staged.life_balance, staged.usdt_balance) != (command.account_base_life, command.account_quote_usdt):
            raise StorageError('JOINT_SPOT_NATIVE_ACCOUNT_MISMATCH')
        if not resume:
            return
        if json.loads(current['reservations']) not in (json.loads(before['reservations']), after_wallet):
            raise StorageError('JOINT_CASH_RECOVERY_NATIVE_MISMATCH')
        for pool in ('subsidy', 'exit_subsidy'):
            if pool not in before:
                continue
            original = json.loads(before[pool])
            actual = json.loads(current[pool])
            if ({key: value for key, value in original.items() if key != 'entries'}
                    != {key: value for key, value in actual.items() if key != 'entries'}
                    or set(original['entries']) != set(actual['entries'])):
                raise StorageError('JOINT_SPOT_SUBSIDY_RECOVERY_MISMATCH')
            floors = {row.intent_id: sum((f.loss(row.side) for f in row.fills), Decimal('0')) for row in command.spot_fills}
            for key, entry in original['entries'].items():
                final = dict(entry)
                if key in floors and floors[key] != Decimal(entry.get('fill_floor', '0')):
                    if entry['actual'] is not None or floors[key] < Decimal(entry.get('fill_floor', '0')):
                        raise StorageError('JOINT_SPOT_SUBSIDY_RECOVERY_MISMATCH')
                    final['fill_floor'] = str(floors[key])
                if actual['entries'][key] not in (entry, final):
                    raise StorageError('JOINT_SPOT_SUBSIDY_RECOVERY_MISMATCH')
