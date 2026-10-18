"""Exact original SPOT command/fills and the retained pre-application holds.

This validates evidence, never provisions orders, money or permissions. Native
preparations are checked independently in SQL before IMPORT and on cold reads.
"""
import json
from datetime import datetime, timezone
from decimal import Decimal as D

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError, fingerprint
from hummingbot.strategy_v2.life_liquidity.storage.financial import FillSpot, PrepareSpot


def spot_basis(source, old, batch, resources, wallet, loss, subsidy):
    commands = resources.get('commands', {})
    supplied = {v.prepare_operation_id: v for v in batch.spot_fills}
    original = {v.prepare_operation_id: v for v in old.spot_fills}
    if (set(supplied) != set(original) or len(supplied) != len(batch.spot_fills)
            or any({k: v for k, v in value.json().items() if k != 'operation_id'} !=
                   {k: v for k, v in original[key].json().items() if k != 'operation_id'} for key, value in supplied.items())):
        raise StorageError('PG_LEGACY_APPLYING_FACTS_CHANGED')
    trades = {f.trade_id for value in original.values() for f in value.fills}
    if trades != set(source.facts.account_trade_ids):
        raise StorageError('PG_LEGACY_APPLYING_FACTS_CHANGED')
    # Independently explain every original prefix trade, fee, hold and gross
    # loss from the unchanged cumulative snapshots. Never adopt a partial
    # wallet balance or infer a missing trade from its monetary delta.
    known = resources.get('fills', {})
    if not set(original) <= set(commands):
        raise StorageError('PG_LEGACY_APPLYING_PROFILE_UNSUPPORTED')
    if set(known) != set(wallet['trades']):
        raise StorageError('PG_LEGACY_APPLYING_ORIGINAL_BASIS_REQUIRED')
    by_trade = {f.trade_id: (value, f) for value in original.values() for f in value.fills}
    if len(by_trade) != sum(len(v.fills) for v in original.values()):
        raise StorageError('PG_LEGACY_APPLYING_FACTS_CHANGED')
    receipts = set()
    for key, row in resources.get('fill_operations', {}).items():
        prior = FillSpot.from_json(row['command'])
        full = original.get(prior.prepare_operation_id)
        if (key != prior.operation_id or row['state'] != 'APPLIED' or full is None
                or row['fingerprint'] != fingerprint({'command': prior.json(), 'owner': row['owner']})
                or {k: v for k, v in prior.json().items() if k not in ('operation_id', 'fills', 'cumulative_base_life', 'observed_at')}
                != {k: v for k, v in full.json().items() if k not in ('operation_id', 'fills', 'cumulative_base_life', 'observed_at')}
                or D(row['execution_loss_usdt']) != sum((f.loss(prior.side) for f in prior.fills), D('0'))
                or any(f.trade_id not in known or f.trade_id not in by_trade or f != by_trade[f.trade_id][1] for f in prior.fills)):
            raise StorageError('PG_LEGACY_APPLYING_ORIGINAL_BASIS_REQUIRED')
        receipts.update(f.trade_id for f in prior.fills)
    if receipts != set(known):
        raise StorageError('PG_LEGACY_APPLYING_ORIGINAL_BASIS_REQUIRED')
    prefix_base, prefix_cash, losses, fee_events = D('0'), D('0'), {}, {}
    quantities, floors = {}, {}
    for trade, retained in known.items():
        if trade not in by_trade:
            raise StorageError('PG_LEGACY_APPLYING_FACTS_CHANGED')
        value, fill = by_trade[trade]
        expected = dict(prepare_operation_id=value.prepare_operation_id, intent_id=value.intent_id,
                        exchange_order_id=value.exchange_order_id, fill=fill.json())
        held_trade = wallet['trades'][trade]
        if (retained != expected or len(held_trade) != 3
                or (held_trade[0], D(held_trade[1]), D(held_trade[2])) !=
                (value.intent_id, fill.quantity_base_life, fill.price_usdt)):
            raise StorageError('PG_LEGACY_APPLYING_ORIGINAL_BASIS_REQUIRED')
        quantities[value.intent_id] = quantities.get(value.intent_id, D('0')) + fill.quantity_base_life
        floors[value.intent_id] = floors.get(value.intent_id, D('0')) + fill.loss(value.side)
        losses['fill:' + trade] = dict(session_id=value.session_id,
                                       day=datetime.fromtimestamp(fill.fill_at_ms / 1000, timezone.utc).date().isoformat(),
                                       loss_quote=str(fill.loss(value.side)))
        fee_events['FEE:' + trade] = ['FEE', fill.fee_currency, str(fill.signed_fee)]
        prefix_base += fill.quantity_base_life if value.side == 'BUY' else -fill.quantity_base_life
        prefix_cash += -fill.quantity_base_life * fill.price_usdt if value.side == 'BUY' else fill.quantity_base_life * fill.price_usdt
        prefix_base += fill.signed_fee if fill.fee_currency == 'LIFE' else D('0')
        prefix_cash += fill.signed_fee if fill.fee_currency == 'USDT' else D('0')
    # Existing File ledgers retain a zero-valued opening marker. Keep that
    # historical profile; every actual known fill still needs its exact event
    # and an unexplained nonzero cost is never discarded.
    if (any(loss['events'].get(key) != value for key, value in losses.items())
            or any(key not in losses and D(value['loss_quote']) != 0 for key, value in loss['events'].items())):
        raise StorageError('PG_LEGACY_APPLYING_ORIGINAL_BASIS_REQUIRED')
    if {k: v for k, v in wallet['account_events'].items() if k.startswith('FEE:')} != fee_events:
        raise StorageError('PG_LEGACY_APPLYING_ORIGINAL_BASIS_REQUIRED')
    base, cash, preparations, intents, unsent = D('0'), D('0'), [], set(), []
    for key, row in commands.items():
        value = original.get(key)
        prepared = PrepareSpot.from_json(row['command'])
        matches_fill = value is None or (
            prepared.operation_id, prepared.intent_id, prepared.client_order_id, prepared.market, prepared.side, prepared.session_id, prepared.epoch
        ) == (value.prepare_operation_id, value.intent_id, value.client_order_id, value.market, value.side, value.session_id, value.epoch)
        if (key != prepared.operation_id
                or row['fingerprint'] != fingerprint({'command': prepared.json(), 'cut': row['original_cut'], 'owner': row['original_owner']})
                or row['state'] not in ('PREPARED', 'SEND_UNKNOWN', 'ACKED') or not matches_fill):
            raise StorageError('PG_LEGACY_APPLYING_FACTS_CHANGED')
        if row['state'] == 'PREPARED':
            record = json.loads(dict(source.journal_images)['wal'])['records'].get(prepared.intent_id)
            if (value is not None or any(row.get(k) is not None for k in ('arm_owner', 'arm_cut', 'arm_fingerprint'))
                    or record is None or record['state'] != 'PREPARED' or record['exchange_order_id'] is not None
                    or record['cancel_requested'] or record['cancel_attempts'] or record['exchange_terminal_observed']
                    or (record['client_order_id'], record['session_id'], record['epoch'], record['reservation_id'],
                        record['slot_market'], record['slot_side'], record['slot_level'])
                    != (prepared.client_order_id, prepared.session_id, prepared.epoch, prepared.intent_id,
                        prepared.market, prepared.side, prepared.quote_level)
                    or prepared.client_order_id in (*source.facts.accepted_wire_ids, *source.facts.runner_wire_ids)):
                raise StorageError('PG_LEGACY_APPLYING_ORIGINAL_BASIS_REQUIRED')
            unsent.append(prepared.operation_id)
        if prepared.intent_id in intents:
            raise StorageError('PG_LEGACY_APPLYING_FACTS_CHANGED')
        intents.add(prepared.intent_id)
        held = wallet['reservations'].get(prepared.intent_id)
        entry = subsidy['entries'].get(prepared.intent_id)
        if (held is None or held['intent'] != dict(intent_id=prepared.intent_id, side=prepared.side,
                                                   quantity_base=str(prepared.quantity_base), limit_price_usdt=str(prepared.limit_price_usdt),
                                                   session_id=prepared.session_id, epoch=prepared.epoch)
                or D(held['filled_base']) != quantities.get(prepared.intent_id, D('0'))
                or D(held['remaining_base']) != prepared.quantity_base - D(held['filled_base'])
                or held['state'] not in ('OPEN', 'UNKNOWN')
                or entry is None or entry['session_id'] != prepared.session_id
                or D(entry['reserved']) != prepared.subsidy_quote_usdt or entry['actual'] is not None
                or D(entry.get('fill_floor', '0')) != floors.get(prepared.intent_id, D('0')) or entry.get('inventory_cycle') is not None):
            raise StorageError('PG_LEGACY_APPLYING_ORIGINAL_BASIS_REQUIRED')
        for f in (() if value is None else value.fills):
            base += f.quantity_base_life if value.side == 'BUY' else -f.quantity_base_life
            cash += -f.quantity_base_life * f.price_usdt if value.side == 'BUY' else f.quantity_base_life * f.price_usdt
            base += f.signed_fee if f.fee_currency == 'LIFE' else D('0')
            cash += f.signed_fee if f.fee_currency == 'USDT' else D('0')
        preparations.append(prepared.json())
    if set(wallet['reservations']) != intents or set(subsidy['entries']) != intents:
        raise StorageError('PG_LEGACY_APPLYING_ORIGINAL_BASIS_REQUIRED')
    basis = {'spot_preparations': sorted(preparations, key=lambda v: v['operation_id'])} if preparations else {}
    if unsent:
        basis['spot_prepared_only_ids'] = sorted(unsent)
    return basis, base, cash, prefix_base, prefix_cash
