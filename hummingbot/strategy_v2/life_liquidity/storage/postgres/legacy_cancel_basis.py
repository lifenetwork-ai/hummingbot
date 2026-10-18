"""Original cancel diagnostics retain their holds and retry identities."""
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError, fingerprint
from hummingbot.strategy_v2.life_liquidity.storage.spot_lifecycle import SpotLifecycleCommand


def cancel_basis(resources, records):
    commands = resources.get('commands', {})
    requests, latest, rows, states = set(), {}, [], {}
    for key, row in resources.get('lifecycle_operations', {}).items():
        command = SpotLifecycleCommand.from_json(row['command'])
        prepared = commands.get(command.prepare_operation_id)
        record = records.get(command.intent_id)
        if (key != command.operation_id or command.action not in ('REQUEST_CANCEL', 'RECORD_CANCEL_ATTEMPT')
                or row['state'] != 'APPLIED' or row['result'] not in ('SEND_UNKNOWN', 'ACKED')
                or row['identity'] != fingerprint({'command': command.json(), 'owner': row['owner']})
                or prepared is None or prepared['original_owner'] != row['owner']
                or prepared['state'] not in (('SEND_UNKNOWN', 'ACKED') if row['result'] == 'SEND_UNKNOWN' else ('ACKED',))
                or row['prior_state'] != row['result'] or row['phase'] != 'CANCELING'
                or (command.intent_id, command.client_order_id) != (prepared['command']['intent_id'], prepared['command']['client_order_id'])
                or record is None or record['state'] != prepared['state'] or not record['cancel_requested']):
            raise StorageError('PG_LEGACY_APPLYING_ORIGINAL_CANCEL_BASIS_REQUIRED')
        if command.action == 'REQUEST_CANCEL':
            requests.add(command.intent_id)
        else:
            prior = latest.setdefault(command.intent_id, {})
            if command.expected_cancel_attempts in prior:
                raise StorageError('PG_LEGACY_APPLYING_ORIGINAL_CANCEL_BASIS_REQUIRED')
            prior[command.expected_cancel_attempts] = command
        rows.append(command.json())
        states[key] = row['result']
    for intent, record in records.items():
        attempts = latest.get(intent, {})
        if (record['cancel_requested'] != (intent in requests or bool(attempts))
                or record['cancel_attempts'] != len(attempts) or set(attempts) != set(range(len(attempts)))):
            raise StorageError('PG_LEGACY_APPLYING_ORIGINAL_CANCEL_BASIS_REQUIRED')
        if attempts:
            ordered = [attempts[i] for i in range(len(attempts))]
            if (record['last_cancel_attempt_at'] != ordered[-1].attempted_at.isoformat()
                    or len({v.retry_interval_ms for v in ordered}) != 1
                    or any((b.attempted_at - a.attempted_at).total_seconds() * 1000 < b.retry_interval_ms
                           for a, b in zip(ordered, ordered[1:]))):
                raise StorageError('PG_LEGACY_APPLYING_ORIGINAL_CANCEL_BASIS_REQUIRED')
        elif record['last_cancel_attempt_at'] is not None:
            raise StorageError('PG_LEGACY_APPLYING_ORIGINAL_CANCEL_BASIS_REQUIRED')
    return {'spot_cancellations': sorted(rows, key=lambda v: v['operation_id']), 'spot_cancel_states': states} if rows else {}
