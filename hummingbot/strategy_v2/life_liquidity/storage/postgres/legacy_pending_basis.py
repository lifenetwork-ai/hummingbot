"""Read-only terminal/settlement admission view over exact interrupted File facts.

The actual signed source bytes never change. A view can explain an interrupted
File side effect only when the native original completion already independently
exists at the import cut. No request, monetary write or release is executed here.
"""
import copy
from decimal import Decimal as D

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError


def pending_view(resources, wallet, subsidy, records, *, terminal_ids=(), settlement_ids=()):
    resources, wallet, subsidy, records = (copy.deepcopy(v) for v in (resources, wallet, subsidy, records))

    def refuse():
        raise StorageError('PG_LEGACY_APPLYING_PENDING_SIDE_EFFECT_BASIS_REQUIRED')

    if (len(terminal_ids) != len(set(terminal_ids)) or len(settlement_ids) != len(set(settlement_ids))
            or not set(terminal_ids) <= set(resources.get('terminal_operations', {}))
            or not set(settlement_ids) <= set(resources.get('settlement_operations', {}))):
        refuse()
    pending_terminals, pending_settlements = [], []
    for key, row in resources.get('terminal_operations', {}).items():
        if row['state'] == 'TERMINAL':
            continue
        if row['state'] != 'TERMINATING' or key not in terminal_ids:
            refuse()
        command = row['command']
        prepared = resources['commands'].get(command['prepare_operation_id'])
        intent = command['intent_id']
        wal, held, cost = records.get(intent), wallet['reservations'].get(intent), subsidy['entries'].get(intent)
        if prepared is None or wal is None or held is None or cost is None:
            refuse()
        order = prepared['command']
        quantity, remaining = D(command['facts']['cumulative_base_life']), D(order['quantity_base']) - D(command['facts']['cumulative_base_life'])
        if (prepared['state'] != 'TERMINATING' or quantity < 0 or remaining < 0
                or wal['state'] not in ('SEND_UNKNOWN', 'ACKED', 'TERMINAL')
                or wal['exchange_order_id'] not in (None, command['exchange_order_id'])
                or wal['exchange_terminal_observed'] and wal['exchange_order_id'] != command['exchange_order_id']
                or (wal['client_order_id'], wal['session_id'], wal['epoch'], wal['reservation_id'], wal['slot_market'], wal['slot_side'], wal['slot_level'])
                != (order['client_order_id'], order['session_id'], order['epoch'], intent, order['market'], order['side'], order['quote_level'])
                or D(held['filled_base']) != quantity or D(held['remaining_base']) != (D('0') if held['state'] == 'TERMINAL' else remaining)
                or held['state'] not in ('OPEN', 'UNKNOWN', 'CANCEL_PENDING', 'TERMINAL')
                or wal['state'] == 'TERMINAL' and (not wal['exchange_terminal_observed'] or held['state'] != 'TERMINAL')
                or held['state'] == 'TERMINAL' and not wal['exchange_terminal_observed']
                or cost.get('inventory_cycle') is not None
                or cost['actual'] is not None and (quantity != 0 or D(cost['actual']) != 0 or wal['state'] != 'TERMINAL')
                or order.get('budget_scope', 'QUOTE') != 'QUOTE'):
            refuse()
        prepared['state'], row['state'] = 'TERMINAL', 'TERMINAL'
        wal.update(state='TERMINAL', exchange_order_id=command['exchange_order_id'], exchange_terminal_observed=True)
        held.update(state='TERMINAL', remaining_base='0')
        if quantity == 0:
            cost['actual'] = '0'
        pending_terminals.append(key)
    for key, row in resources.get('settlement_operations', {}).items():
        if row['state'] == 'SETTLED':
            continue
        if row['state'] != 'SETTLING' or key not in settlement_ids:
            refuse()
        command = row['command']
        cycle = dict(id=command['cycle_id'], members=command['intent_ids'], proof=row['proof'], actuals=row['actuals'])
        before = after = True
        for intent in command['intent_ids']:
            entry = subsidy['entries'].get(intent)
            if entry is None:
                refuse()
            before &= entry['actual'] is None and entry.get('inventory_cycle') is None
            after &= entry['actual'] is not None and D(entry['actual']) == D(row['actuals'][intent]) and entry.get('inventory_cycle') == cycle
        # One File budget write is atomic. Mixing before/after members cannot
        # be explained by this original cycle's crash boundary.
        if not (before or after):
            refuse()
        for intent in command['intent_ids']:
            subsidy['entries'][intent].update(actual=row['actuals'][intent], inventory_cycle=cycle)
        row['state'] = 'SETTLED'
        pending_settlements.append(key)
    return resources, wallet, subsidy, records, {
        **({'spot_pending_terminal_ids': sorted(pending_terminals)} if pending_terminals else {}),
        **({'spot_pending_settlement_ids': sorted(pending_settlements)} if pending_settlements else {}),
    }
