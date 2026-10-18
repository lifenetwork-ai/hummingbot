"""Exact original SPOT command/fills and the retained pre-application holds.

This validates evidence, never provisions orders, money or permissions. Native
preparations are checked independently in SQL before IMPORT and on cold reads.
"""
from decimal import Decimal as D

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError, fingerprint
from hummingbot.strategy_v2.life_liquidity.storage.financial import PrepareSpot


def spot_basis(source, old, batch, resources, wallet, loss, subsidy):
    commands = resources.get('commands', {})
    if not old.spot_fills:
        if (batch.spot_fills or commands or resources.get('fills') or source.facts.account_trade_ids
                or wallet['trades'] or wallet['reservations'] or subsidy['entries']
                or any(D(v['loss_quote']) for v in loss['events'].values())):
            raise StorageError('PG_LEGACY_APPLYING_PROFILE_UNSUPPORTED')
        return {}, D('0'), D('0')
    supplied = {v.prepare_operation_id: v for v in batch.spot_fills}
    original = {v.prepare_operation_id: v for v in old.spot_fills}
    if (set(supplied) != set(original) or len(supplied) != len(batch.spot_fills)
            or any({k: v for k, v in value.json().items() if k != 'operation_id'} !=
                   {k: v for k, v in original[key].json().items() if k != 'operation_id'} for key, value in supplied.items())):
        raise StorageError('PG_LEGACY_APPLYING_FACTS_CHANGED')
    trades = {f.trade_id for value in original.values() for f in value.fills}
    if trades != set(source.facts.account_trade_ids):
        raise StorageError('PG_LEGACY_APPLYING_FACTS_CHANGED')
    # A prior accounted SPOT prefix requires its own full historical basis;
    # this first financial command cannot silently adopt that wallet/cost.
    if (set(commands) != set(original) or resources.get('fills') or wallet['trades']
            or any(D(v['loss_quote']) for v in loss['events'].values())):
        raise StorageError('PG_LEGACY_APPLYING_PROFILE_UNSUPPORTED')
    base, cash, preparations, intents = D('0'), D('0'), [], set()
    for key, value in original.items():
        row = commands[key]
        prepared = PrepareSpot.from_json(row['command'])
        if (row['fingerprint'] != fingerprint({'command': prepared.json(), 'cut': row['original_cut'], 'owner': row['original_owner']})
                or row['state'] not in ('SEND_UNKNOWN', 'ACKED')
                or (prepared.operation_id, prepared.intent_id, prepared.client_order_id, prepared.market,
                    prepared.side, prepared.session_id, prepared.epoch) !=
                (value.prepare_operation_id, value.intent_id, value.client_order_id, value.market,
                 value.side, value.session_id, value.epoch)):
            raise StorageError('PG_LEGACY_APPLYING_FACTS_CHANGED')
        intents.add(value.intent_id)
        held = wallet['reservations'].get(value.intent_id)
        entry = subsidy['entries'].get(value.intent_id)
        if (held is None or held['intent'] != dict(intent_id=prepared.intent_id, side=prepared.side,
                                                   quantity_base=str(prepared.quantity_base), limit_price_usdt=str(prepared.limit_price_usdt),
                                                   session_id=prepared.session_id, epoch=prepared.epoch)
                or D(held['remaining_base']) != prepared.quantity_base or D(held['filled_base']) != 0
                or held['state'] not in ('OPEN', 'UNKNOWN')
                or entry is None or entry['session_id'] != prepared.session_id
                or D(entry['reserved']) != prepared.subsidy_quote_usdt or entry['actual'] is not None
                or D(entry.get('fill_floor', '0')) != 0 or entry.get('inventory_cycle') is not None):
            raise StorageError('PG_LEGACY_APPLYING_ORIGINAL_BASIS_REQUIRED')
        for f in value.fills:
            base += f.quantity_base_life if value.side == 'BUY' else -f.quantity_base_life
            cash += -f.quantity_base_life * f.price_usdt if value.side == 'BUY' else f.quantity_base_life * f.price_usdt
            base += f.signed_fee if f.fee_currency == 'LIFE' else D('0')
            cash += f.signed_fee if f.fee_currency == 'USDT' else D('0')
        preparations.append(prepared.json())
    if set(wallet['reservations']) != intents or set(subsidy['entries']) != intents:
        raise StorageError('PG_LEGACY_APPLYING_ORIGINAL_BASIS_REQUIRED')
    return {'spot_preparations': sorted(preparations, key=lambda v: v['operation_id'])}, base, cash
