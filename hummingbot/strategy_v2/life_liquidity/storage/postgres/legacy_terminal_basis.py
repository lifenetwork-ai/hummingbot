"""Retain completed terminal evidence without performing another release."""
from decimal import Decimal as D

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError, fingerprint


def terminal_basis(resources, records, wallet, quantities):
    rows, intents = [], set()
    for key, row in resources.get('terminal_operations', {}).items():
        command = row['command']
        prepared = resources.get('commands', {}).get(command['prepare_operation_id'])
        record = records.get(command['intent_id'])
        held = wallet['reservations'].get(command['intent_id'])
        if prepared is None:
            raise StorageError('PG_LEGACY_APPLYING_ORIGINAL_TERMINAL_BASIS_REQUIRED')
        facts, order, owner = command['facts'], prepared['command'], prepared['original_owner']
        core = {**command, 'facts': {k: facts[k] for k in (
            'exchange_state', 'cumulative_base_life', 'quantity_base_life', 'limit_price_usdt', 'fills')}}
        fills = {v['fill']['trade_id']: v['fill'] for v in resources.get('fills', {}).values()
                 if v['intent_id'] == command['intent_id']}
        if (key != command['operation_id'] or row['state'] != 'TERMINAL' or prepared['state'] != 'TERMINAL'
                or facts['binding'] != prepared['original_cut']['checkpoint']['binding']
                or row['fingerprint'] != fingerprint({'command': command, 'owner': owner})
                or row['core_fingerprint'] != fingerprint({'command': core, 'owner': owner})
                or any(command[k] != order[k] for k in ('intent_id', 'client_order_id', 'market', 'side', 'session_id', 'epoch'))
                or (D(facts['quantity_base_life']), D(facts['limit_price_usdt'])) != (D(order['quantity_base']), D(order['limit_price_usdt']))
                or facts['fills'] != sorted(fills.values(), key=lambda f: (f['fill_at_ms'], f['trade_id']))
                or D(facts['cumulative_base_life']) != quantities.get(command['intent_id'], D('0'))
                or D(row['principal_remaining_base']) != D(order['quantity_base']) - D(facts['cumulative_base_life'])
                or record is None or record['state'] != 'TERMINAL' or not record['exchange_terminal_observed']
                or record['exchange_order_id'] != command['exchange_order_id']
                or held is None or held['state'] != 'TERMINAL' or D(held['remaining_base']) != 0
                or command['intent_id'] in intents):
            raise StorageError('PG_LEGACY_APPLYING_ORIGINAL_TERMINAL_BASIS_REQUIRED')
        intents.add(command['intent_id'])
        rows.append(command)
    if intents != {v['command']['intent_id'] for v in resources.get('commands', {}).values() if v['state'] == 'TERMINAL'}:
        raise StorageError('PG_LEGACY_APPLYING_ORIGINAL_TERMINAL_BASIS_REQUIRED')
    return {'spot_terminals': sorted(rows, key=lambda v: v['operation_id'])} if rows else {}
