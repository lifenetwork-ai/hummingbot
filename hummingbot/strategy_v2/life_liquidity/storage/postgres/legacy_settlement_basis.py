"""Explain original settled cost from immutable cycle members and fills."""
from decimal import Decimal as D

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError, fingerprint
from hummingbot.strategy_v2.life_liquidity.storage.financial import SpotFillObservation
from hummingbot.strategy_v2.life_liquidity.storage.settlement import SettleSpotInventory


def settlement_basis(resources, records, subsidy):
    orders = {v['command']['intent_id']: v for v in resources.get('commands', {}).values()}
    result, members, cycles = [], {}, set()

    def refuse():
        raise StorageError('PG_LEGACY_APPLYING_ORIGINAL_SETTLEMENT_BASIS_REQUIRED')

    for key, row in resources.get('settlement_operations', {}).items():
        command = SettleSpotInventory(**{**row['command'], 'intent_ids': tuple(row['command']['intent_ids'])})
        actuals, held, identities, selected = {}, {}, [], []
        owners = []
        for intent in command.intent_ids:
            original, wal = orders.get(intent), records.get(intent)
            if original is None or wal is None or original['state'] != 'TERMINAL' or intent in members:
                refuse()
            prepared = original['command']
            if prepared.get('budget_scope', 'QUOTE') == 'EXIT':
                refuse()
            owners.append(original['original_owner'])
            fills = [(tid, SpotFillObservation.from_json(v['fill'])) for tid, v in resources.get('fills', {}).items()
                     if v['intent_id'] == intent]
            if not fills:
                refuse()
            actuals[intent] = sum((f.loss(prepared['side']) for _, f in fills), D('0'))
            held[intent] = max(D(prepared['subsidy_quote_usdt']), actuals[intent])
            identities.append([intent, prepared['client_order_id'], wal['exchange_order_id'], prepared['session_id'], prepared['epoch']])
            selected.extend((tid, intent, prepared['side'], f) for tid, f in fills)
        selected.sort(key=lambda v: (v[3].fill_at_ms, v[0]))
        base = cash = D('0')
        for _, _, side, fill in selected:
            sign = D('1') if side == 'BUY' else D('-1')
            base += sign * fill.quantity_base_life + (fill.signed_fee if fill.fee_currency == 'LIFE' else D('0'))
            cash += -sign * fill.quantity_base_life * fill.price_usdt + (fill.signed_fee if fill.fee_currency == 'USDT' else D('0'))
        actuals[selected[-1][1]] += max(D('0'), -cash - sum(actuals.values(), D('0')))
        proof = fingerprint({'identities': identities, 'fills': [[tid, intent, side, f.json()] for tid, intent, side, f in selected]})
        if (key != command.operation_id or row['state'] != 'SETTLED' or base != 0 or command.cycle_id in cycles
                or any(v != owners[0] for v in owners)
                or row['facts']['binding'] != orders[command.intent_ids[0]]['original_cut']['checkpoint']['binding']
                or row['fingerprint'] != fingerprint({'command': command.json(), 'owner': owners[0]})
                or row['proof'] != proof or row['actuals'] != {k: str(v) for k, v in actuals.items()}
                or row['committed_before'] != {k: str(v) for k, v in held.items()}):
            refuse()
        cycle = dict(id=command.cycle_id, members=list(command.intent_ids), proof=proof, actuals=row['actuals'])
        for intent, actual in actuals.items():
            entry = subsidy['entries'].get(intent)
            if (entry is None or entry.get('inventory_cycle') != cycle
                    or entry['actual'] is None or D(entry['actual']) != actual):
                refuse()
            members[intent] = actual
        cycles.add(command.cycle_id)
        result.append({k: row[k] for k in ('command', 'proof', 'actuals', 'committed_before', 'facts')})
    return ({'spot_settlements': sorted(result, key=lambda v: v['command']['operation_id'])} if result else {}), members
