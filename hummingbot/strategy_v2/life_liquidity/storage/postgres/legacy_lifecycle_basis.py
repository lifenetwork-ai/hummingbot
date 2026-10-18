"""Original unsent abort/NAV evidence; this never releases or values capital."""
from datetime import datetime
from decimal import Decimal as D

from hummingbot.strategy_v2.life_liquidity.storage.capital import ObserveSpotNAV
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError, fingerprint
from hummingbot.strategy_v2.life_liquidity.storage.spot_lifecycle import SpotLifecycleCommand


def abort_basis(resources, records, wallet, subsidy, facts):
    aborted, intents = [], set()
    for key, row in resources.get('lifecycle_operations', {}).items():
        command = SpotLifecycleCommand.from_json(row['command'])
        if command.action != 'ABORT_PREPARED':
            continue
        prepared = resources.get('commands', {}).get(command.prepare_operation_id)
        wal, held, cost = (records.get(command.intent_id), wallet['reservations'].get(command.intent_id),
                           subsidy['entries'].get(command.intent_id))
        if (prepared is None or prepared['state'] != 'ABORTED_BEFORE_SEND' or row['state'] != 'APPLIED'
                or row['phase'] != 'ABORTING' or row['prior_state'] != 'PREPARED' or row['result'] != 'ABORTED_BEFORE_SEND'
                or key != command.operation_id or prepared['original_owner'] != row['owner']
                or row['identity'] != fingerprint({'command': command.json(), 'owner': row['owner']})
                or prepared['command']['intent_id'] != command.intent_id or prepared['command']['client_order_id'] != command.client_order_id
                or any(prepared.get(k) is not None for k in ('arm_owner', 'arm_cut', 'arm_fingerprint'))
                or wal is None or wal['state'] != 'ABORTED_BEFORE_SEND' or wal['exchange_order_id'] is not None
                or wal['exchange_terminal_observed'] or wal['cancel_requested'] or wal['cancel_attempts'] or wal['last_cancel_attempt_at'] is not None
                or (wal['client_order_id'], wal['session_id'], wal['epoch'], wal['reservation_id'], wal['slot_market'], wal['slot_side'], wal['slot_level'])
                != (command.client_order_id, prepared['command']['session_id'], prepared['command']['epoch'], command.intent_id,
                    prepared['command']['market'], prepared['command']['side'], prepared['command']['quote_level'])
                or held is None or held['state'] != 'TERMINAL' or D(held['remaining_base']) != 0 or D(held['filled_base']) != 0
                or cost is None or cost['actual'] is None or D(cost['actual']) != 0 or D(cost.get('fill_floor', '0')) != 0
                or command.client_order_id in (*facts.accepted_wire_ids, *facts.runner_wire_ids) or command.intent_id in intents):
            raise StorageError('PG_LEGACY_APPLYING_ORIGINAL_ABORT_BASIS_REQUIRED')
        aborted.append(command.json())
        intents.add(command.intent_id)
    if intents != {row['command']['intent_id'] for row in resources.get('commands', {}).values() if row['state'] == 'ABORTED_BEFORE_SEND'}:
        raise StorageError('PG_LEGACY_APPLYING_ORIGINAL_ABORT_BASIS_REQUIRED')
    return {'spot_aborts': sorted(aborted, key=lambda v: v['operation_id'])} if aborted else {}


def nav_basis(resources, owner, observed_at):
    rows = []
    for key, row in resources.get('nav_operations', {}).items():
        command = ObserveSpotNAV(**{**row['command'], 'independent_price_usdt': D(row['command']['independent_price_usdt']),
                                    'observed_at': datetime.fromisoformat(row['command']['observed_at'])})
        if (key != command.operation_id or command.observation_grant_id is not None
                or command.observed_at > observed_at
                or row['fingerprint'] != fingerprint({'command': command.json(), 'owner': owner})
                or not isinstance(row['financial_fingerprint'], str) or len(row['financial_fingerprint']) != 64
                or any(v not in '0123456789abcdef' for v in row['financial_fingerprint'])):
            raise StorageError('PG_LEGACY_APPLYING_ORIGINAL_NAV_BASIS_REQUIRED')
        rows.append(dict(command=command.json(), measurement=row['measurement'], financial_fingerprint=row['financial_fingerprint']))
    capital = resources.get('capital', {})
    if bool(rows) != bool(capital) or rows and capital['latest_operation_id'] not in resources['nav_operations']:
        raise StorageError('PG_LEGACY_APPLYING_ORIGINAL_NAV_BASIS_REQUIRED')
    return {'spot_navs': sorted(rows, key=lambda v: v['command']['operation_id']), 'spot_nav_capital': capital} if rows else {}
