"""Retained outer File SWAP facts; native original basis is never inferred.

Independent historical reference values are explicit signed target supplements.
They cannot change original fill quantities, prices, fees, PNL, times or IDs.
"""
import json
from datetime import datetime
from decimal import Decimal as D

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
from hummingbot.strategy_v2.life_liquidity.storage.joint_settlement import JointCashSpot
from hummingbot.strategy_v2.life_liquidity.storage.legacy_spot import LegacySpotStore


def joint_profile(source, command):
    envelope = json.loads(source.checkpoint_bytes)
    resources = envelope['state']['resources']
    raw = {name: json.loads(data) for name, _, data in source.ancillary_images}
    state, policy = raw['joint_recovery']['state'], raw['joint_recovery']['policy']
    basis = envelope['policy']['legacy_spot']['accounting_policy']
    bundle, anchor = state['bundle'], state['anchor']
    if (state['opening_cashflows'] or state['opening_joint'] or state['runner_fills'] or state['excluded'] or state['rearms']
            or any(resources.get(k) for k in ('recovery_batches', 'product_operations'))
            or any(resources['joint_repository_policy']['products'].values())
            or set(source.original_operations) - set(source.pending_operation_ids)
            or any(order['claim']['product'] not in ('SPOT', 'SWAP') for order in bundle['orders'])):
        raise StorageError('PG_LEGACY_APPLYING_PROFILE_UNSUPPORTED')
    if (D(anchor['spot_life_base']) != D(basis['opening_base_life']) or D(anchor['spot_cash_quote']) != D(basis['opening_quote_usdt'])
            or anchor['pending']
            or int(datetime.fromisoformat(basis['opened_at']).timestamp() * 1000) != anchor['observed_at_ms']):
        raise StorageError('PG_LEGACY_APPLYING_ORIGINAL_BASIS_REQUIRED')
    expected_cash = D(basis['opening_quote_usdt'])
    expected_base = D(basis['opening_base_life'])
    # Retain original historical valuation even before core cash begins.
    original_flows = [LegacySpotStore._cashflow_from_json(v) for v in bundle.get('repository_cashflows', ())]
    flows = {v.bill_id: v for v in original_flows}
    approved = {v['bill_id']: v for v in basis['approved_cashflows']}
    if (len(flows) != len(original_flows) or len(approved) != len(basis['approved_cashflows'])
            or len(command.batch.cashflows) != len(flows)
            or {v.bill_id for v in command.batch.cashflows} != set(flows)):
        raise StorageError('PG_LEGACY_APPLYING_FACTS_CHANGED')
    for v in command.batch.cashflows:
        old = flows[v.bill_id]
        a = approved.get(v.bill_id)
        if (a is None or any(v.json()[k] != old.json()[k] for k in old.json() if k != 'operation_id')
                or (a['currency'], D(a['signed_amount']), a['transfer_at_ms']) != (v.currency, v.signed_amount, v.transfer_at_ms)):
            raise StorageError('PG_LEGACY_APPLYING_FACTS_CHANGED')
        expected_cash += v.signed_amount if v.currency == 'USDT' else D('0')
        expected_base += v.signed_amount if v.currency == 'LIFE' else D('0')
    for v in resources.get('cashflow_operations', {}).values():
        fact = LegacySpotStore._cashflow_from_json(v['command'])
        if fact.bill_id not in flows or fact != flows[fact.bill_id] or v['state'] not in ('CASHFLOWING', 'APPLIED'):
            raise StorageError('PG_LEGACY_APPLYING_PROFILE_UNSUPPORTED')
    if any(k not in flows or v != flows[k].json() for k, v in resources.get('cashflows', {}).items()):
        raise StorageError('PG_LEGACY_APPLYING_FACTS_CHANGED')
    original_events, trades, claims = {}, {}, []
    supplied = {fill.intent_id: fill for fill in (*command.batch.swap_fills, *command.batch.spot_fills)}
    if (len(supplied) != len(command.batch.swap_fills) + len(command.batch.spot_fills)
            or set(supplied) != {o['claim']['intent_id'] for o in bundle['orders'] if o['trades']}):
        raise StorageError('PG_LEGACY_APPLYING_FACTS_CHANGED')
    if ({f.intent_id for f in command.batch.spot_fills} != {o['claim']['intent_id'] for o in bundle['orders'] if o['claim']['product'] == 'SPOT' and o['trades']}
            or {f.intent_id for f in command.batch.swap_fills} != {o['claim']['intent_id'] for o in bundle['orders'] if o['claim']['product'] == 'SWAP' and o['trades']}):
        raise StorageError('PG_LEGACY_APPLYING_FACTS_CHANGED')
    for order in bundle['orders']:
        claim = order['claim']
        if not order['trades']:
            # An original unarmed SPOT receipt has a principal hold but no
            # economic leaf. Its independent native PREPARED proof is checked
            # at the import cut; it never becomes an implied ARM or refund.
            prepared = [r['command'] for r in resources['commands'].values() if r['command']['intent_id'] == claim['intent_id']]
            if (claim['product'] != 'SPOT' or order['state'] != 'unsent' or order['exchange_order_id'] is not None
                    or len(prepared) != 1):
                raise StorageError('PG_LEGACY_APPLYING_PROFILE_UNSUPPORTED')
            p = prepared[0]
            if ((p['client_order_id'], p['session_id'], p['epoch'], p['side'], D(p['quantity_base']), D(p['limit_price_usdt']))
                    != (claim['wire_id'], claim['session_id'], claim['epoch'], claim['side'], D(claim['quantity_base']), D(claim['price_usdt']))):
                raise StorageError('PG_LEGACY_APPLYING_FACTS_CHANGED')
            claims.append(dict(claim=claim, prepare_operation_id=p['operation_id'], exchange_order_id=None))
            continue
        fill = supplied[claim['intent_id']]
        if ((fill.intent_id, fill.client_order_id, fill.side, fill.session_id, fill.epoch, fill.exchange_order_id, fill.market)
                != (claim['intent_id'], claim['wire_id'], claim['side'], claim['session_id'], claim['epoch'], order['exchange_order_id'], 'LIFE-USDT')
                or fill.cumulative_base_life != sum((D(t['quantity_base']) for t in order['trades']), D(0))
                or len(fill.fills) != len(order['trades'])
                or {f.trade_id for f in fill.fills} != {t['trade_id'] for t in order['trades']}):
            raise StorageError('PG_LEGACY_APPLYING_FACTS_CHANGED')
        provided = {f.trade_id: f for f in fill.fills}
        product = claim['product']
        for t in order['trades']:
            actual = provided[t['trade_id']]
            if (t['trade_id'] in trades or t['fee_currency'] not in (('USDT',) if product == 'SWAP' else ('LIFE', 'USDT'))
                    or (actual.quantity_base_life, actual.price_usdt, actual.fee_currency, actual.signed_fee,
                        actual.fill_at_ms, actual.realized_pnl_usdt if product == 'SWAP' else D('0'), actual.sequence if product == 'SWAP' else None)
                    != (D(t['quantity_base']), D(t['price_usdt']), t['fee_currency'], D(t['signed_fee']), t['at_ms'], D(t['realized_pnl_quote']), t['sequence'])):
                raise StorageError('PG_LEGACY_APPLYING_FACTS_CHANGED')
            trades[t['trade_id']] = t
            if product == 'SWAP':
                cash = D(t['realized_pnl_quote']) + D(t['signed_fee'])
                expected_cash += cash
                original_events[f"JOINT:{t['at_ms']}:TRADE:{t['trade_id']}"] = cash
            else:
                if t['sequence'] is not None or D(t['realized_pnl_quote']) != 0:
                    raise StorageError('PG_LEGACY_APPLYING_FACTS_CHANGED')
                quantity, price = D(t['quantity_base']), D(t['price_usdt'])
                expected_base += (quantity if claim['side'] == 'BUY' else -quantity) + (D(t['signed_fee']) if t['fee_currency'] == 'LIFE' else D('0'))
                expected_cash += (-quantity * price if claim['side'] == 'BUY' else quantity * price) + (D(t['signed_fee']) if t['fee_currency'] == 'USDT' else D('0'))
        claims.append(dict(claim=claim, prepare_operation_id=fill.prepare_operation_id, exchange_order_id=order['exchange_order_id']))
    payments = {p['bill_id']: (p['at_ms'], D(p['signed_amount_quote'])) for p in bundle['payments']}
    if (len(payments) != len(bundle['payments']) or len(payments) != len(command.batch.funding.bundle.funding.payments)
            or payments != {p.bill_id: (p.settled_at_ms, p.signed_payment_quote) for p in command.batch.funding.bundle.funding.payments}):
        raise StorageError('PG_LEGACY_APPLYING_FACTS_CHANGED')
    for bill, (at, amount) in payments.items():
        expected_cash += amount
        original_events[f'JOINT:{at}:FUNDING:{bill}'] = amount
    if (expected_cash != source.facts.account_quote_usdt or expected_base != source.facts.account_base_life
            or set(source.facts.account_trade_ids) != set(trades) or set(source.facts.account_cashflow_ids) != set(flows)):
        raise StorageError('PG_LEGACY_APPLYING_FACTS_CHANGED')
    # The complete core saga may not have started yet, or may already be
    # APPLIED. Its original pre-images are authoritative in both latter cases;
    # current wallet/claim images are never used as the opening target basis.
    row = source.original_operations.get(source.pending_operation_ids[0])
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.legacy_pending_ack import ack_view
    records = json.loads(dict(source.journal_images)['wal'])['records']
    old = None if row is None else JointCashSpot.from_json(row['command'])
    acknowledgements = (tuple(LegacySpotStore._ack_from_json(r['command']) for r in resources.get('acknowledgements', {}).values()
                              if r['state'] == 'ACKING') if old is None else old.spot_acks)
    resources, records, pending_acks = ack_view(resources, records, acknowledgements, observed_at=source.facts.observed_at)
    if row is not None:
        from hummingbot.strategy_v2.life_liquidity.storage.postgres.legacy_funding_history import _completed_acks
        _completed_acks(old, resources, row['owner'])
        if ({v.bill_id: v for v in old.spot_cashflows} != flows
                or {event.event_id: event.signed_amount_usdt for event in old.events} != original_events
                or (old.account_base_life, old.account_quote_usdt) != (source.facts.account_base_life, source.facts.account_quote_usdt)):
            raise StorageError('PG_LEGACY_APPLYING_FACTS_CHANGED')
        before = {k: bytes.fromhex(v) for k, v in row['before_native'].items()}
    else:
        before = dict(source.journal_images)
    wallet, loss, subsidy = (json.loads(before[k]) for k in ('reservations', 'loss', 'subsidy'))
    before_base, before_cash = D(basis['opening_base_life']), D(basis['opening_quote_usdt'])
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.legacy_spot_basis import spot_basis

    # An APPLIED core has published fill metadata. Its current verified wallet
    # explains that metadata; the original before-wallet is replayed separately.
    original_fills = command.batch.spot_fills if row is None else old.spot_fills
    images = dict(source.journal_images) if row is not None and row['state'] == 'APPLIED' else before
    current_wallet, current_loss, current_subsidy = (json.loads(images[k]) for k in ('reservations', 'loss', 'subsidy'))
    other_losses = {}
    if row is not None and row['state'] == 'APPLIED':
        for order in bundle['orders']:
            if order['claim']['product'] != 'SWAP':
                continue
            from datetime import timezone
            for trade in order['trades']:
                other_losses['swap:' + trade['trade_id']] = dict(session_id=order['claim']['session_id'],
                                                                 day=datetime.fromtimestamp(trade['at_ms'] / 1000, timezone.utc).date().isoformat(),
                                                                 loss_quote=str(max(D('0'), -D(trade['signed_fee'])) + max(D('0'), -D(trade['realized_pnl_quote']))))
    spot_ids = {t['trade_id'] for order in bundle['orders'] if order['claim']['product'] == 'SPOT' for t in order['trades']}
    from hummingbot.strategy_v2.life_liquidity.storage.financial import PrepareSpot
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.legacy_pending_basis import pending_view
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.legacy_prepared_basis import prepared_view
    preparations = (tuple(PrepareSpot.from_json(r['command']) for r in resources['commands'].values() if r['state'] == 'PREPARING')
                    if row is None else old.spot_prepared_receipts)
    resources, _ = prepared_view(
        resources, current_wallet, current_subsidy, records, preparations,
        envelope['state']['ownership']['token'], source.cut.checkpoint.binding.json(),
        source.facts.observed_at if row is None else old.observed_at,
        batch_id=None if row is None else old.operation_id,
        applied=row is not None and row['state'] == 'APPLIED')
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.legacy_pending_lifecycle import lifecycle_view
    resources, current_wallet, current_subsidy, records, _ = lifecycle_view(resources, current_wallet, current_subsidy, records)
    resources, current_wallet, current_subsidy, records, pending = pending_view(
        resources, current_wallet, current_subsidy, records,
        terminal_ids=tuple(resources.get('terminal_operations', {})) if row is None else old.spot_terminal_operations,
        settlement_ids=tuple(resources.get('settlement_operations', {})) if row is None else old.spot_settlement_operations)
    spot, _, _, prefix_base, prefix_cash = spot_basis(source, None, command.batch, resources, current_wallet, current_loss,
                                                      current_subsidy, original_fills=original_fills, account_trade_ids=spot_ids, other_losses=other_losses, records_override=records)
    # Recompute the original pre-core wallet from the original target fills.
    # An observed trade never becomes an opening-balance adjustment.
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.legacy_funding_history import _wallet_money
    if row is not None:
        before_base, before_cash, paid = _wallet_money(wallet, old, command.batch, flows, basis, {})
        if paid:
            raise StorageError('PG_LEGACY_APPLYING_ORIGINAL_BASIS_REQUIRED')
    else:
        from datetime import timezone
        before_base, before_cash, paid = _wallet_money(wallet, None, command.batch, flows, basis, {},
                                                       observed_at=datetime.fromtimestamp(bundle['observed_at_ms'] / 1000, timezone.utc))
        if paid:
            raise StorageError('PG_LEGACY_APPLYING_ORIGINAL_BASIS_REQUIRED')
    if (D(wallet['life_balance']) != before_base or D(wallet['usdt_balance']) != before_cash):
        raise StorageError('PG_LEGACY_APPLYING_ORIGINAL_BASIS_REQUIRED')
    return {**basis, **spot, **pending, **({'spot_required_ack_ids': sorted(pending_acks)} if pending_acks else {}),
            'swap_opening': dict(opening_long_base=anchor['long_base'], opening_short_base=anchor['short_base'],
                                 opening_long_entry=policy['opening_long_entry'], opening_short_entry=policy['opening_short_entry']),
            'claims': sorted(claims, key=lambda row: row['claim']['intent_id'])}
