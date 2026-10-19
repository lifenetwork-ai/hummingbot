"""Read-only explanation of an original interrupted File lifecycle command.

Final admission still requires the independently recorded native original
completion. The view never executes an abort, cancel, budget write or release.
"""
import copy
from decimal import Decimal as D

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
from hummingbot.strategy_v2.life_liquidity.storage.spot_lifecycle import SpotLifecycleCommand


def lifecycle_view(resources, wallet, subsidy, records):
    resources, wallet, subsidy, records = (copy.deepcopy(v) for v in (resources, wallet, subsidy, records))
    pending = []
    for key, row in resources.get('lifecycle_operations', {}).items():
        if row['state'] == 'APPLIED':
            continue

        def refuse():
            raise StorageError('PG_LEGACY_APPLYING_PENDING_LIFECYCLE_BASIS_REQUIRED')
        command = SpotLifecycleCommand.from_json(row['command'])
        prepared = resources['commands'].get(command.prepare_operation_id)
        wal, held, cost = (records.get(command.intent_id), wallet['reservations'].get(command.intent_id), subsidy['entries'].get(command.intent_id))
        if (row['state'] != 'APPLYING' or key != command.operation_id or prepared is None or wal is None or held is None or cost is None
                or prepared['state'] != row['phase']):
            refuse()
        if command.action == 'ABORT_PREPARED':
            if (row['phase'] != 'ABORTING' or row['prior_state'] != 'PREPARED' or row['result'] != 'ABORTED_BEFORE_SEND'
                    or wal['state'] not in ('PREPARED', 'ABORTED_BEFORE_SEND')
                    or held['state'] not in ('OPEN', 'TERMINAL') or D(held['filled_base']) != 0
                    or D(held['remaining_base']) != (D('0') if held['state'] == 'TERMINAL' else D(prepared['command']['quantity_base']))
                    or wal['state'] == 'PREPARED' and held['state'] != 'OPEN'
                    or cost['actual'] is not None and (D(cost['actual']) != 0 or held['state'] != 'TERMINAL')):
                refuse()
            wal['state'] = 'ABORTED_BEFORE_SEND'
            held.update(state='TERMINAL', remaining_base='0')
            cost['actual'] = '0'
        else:
            if (row['phase'] != 'CANCELING' or row['prior_state'] not in ('SEND_UNKNOWN', 'ACKED')
                    or row['result'] != row['prior_state'] or wal['state'] != row['prior_state']
                    or held['state'] not in ('OPEN', 'UNKNOWN', 'CANCEL_PENDING')
                    or held['state'] == 'CANCEL_PENDING' and not wal['cancel_requested']):
                refuse()
            if command.action == 'RECORD_CANCEL_ATTEMPT':
                if wal['cancel_attempts'] == command.expected_cancel_attempts:
                    if wal['last_cancel_attempt_at'] == command.attempted_at.isoformat():
                        refuse()
                elif wal['cancel_attempts'] != command.expected_cancel_attempts + 1 or wal['last_cancel_attempt_at'] != command.attempted_at.isoformat():
                    refuse()
                wal.update(cancel_attempts=command.expected_cancel_attempts + 1, last_cancel_attempt_at=command.attempted_at.isoformat())
            wal['cancel_requested'] = True
            held['state'] = 'CANCEL_PENDING'
        prepared['state'], row['state'] = row['result'], 'APPLIED'
        pending.append(key)
    return resources, wallet, subsidy, records, pending
