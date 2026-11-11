"""Read-only remaining-cost proposal from independently reconciled maintenance evidence.

This never contacts an exchange, edits a checkpoint or grants trading authority.
Application requires a stopped writer, fresh venue revalidation, matching live
financial/binding hashes and a scoped audited maintenance transaction.
"""
import argparse
import hashlib
import json
import os
import sys
from decimal import Decimal, localcontext
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hummingbot.core.data_type.common import PositionAction, TradeType  # noqa: E402
from hummingbot.core.recovery import recovery_blockers, replay_positions  # noqa: E402
from hummingbot.strategy_v2.utils.perpetual_reduction import HedgeCostBasis, basis_checkpoint  # noqa: E402


def _require(condition, message):
    # Evidence validation remains active under python -O.
    if not condition:
        raise ValueError(message)


def prepare(snapshot, venue, metadata):
    state = snapshot['state']
    recovery = state['recovery_state']
    _require(state['desired_state'] == 'stopped' and state['control_version'] == state['acknowledged_version'], 'Stop must be acknowledged')
    _require(not recovery.get('runtime_active') and (not recovery.get('pending_mutations')), 'Unresolved runtime effects')
    _require(all((not c.get('_is_stop_triggered') and (not c.get('mm_drawdown_breach')) and (not c.get('drawdown_exited_controllers')) for c in recovery.get('strategy_checkpoints', {}).values())), 'Risk stop must be preserved')
    _require(all(r.get('applied') is True for r in snapshot.get('runtime_journal', []))
             and all(r.get('state') == 'terminal' for r in snapshot.get('submission_attempts', [])),
             'Unapplied financial work or unresolved attempts')
    _require(not (set(recovery_blockers(state)) - {'recovery_not_halted', 'recovery_reason_unsupported'}),
             'Risk or invalid checkpoint blocks baseline activation')
    _require(venue['all_local_fills_verified'] == len(snapshot['fills']) and venue['complete_scans_per_market'] >= 2, 'Complete independent fill coverage required')
    _require(bool(venue['account_matches']) and all(v is True for v in venue['account_matches'].values()) and (not any(venue['pending'].values())), 'Account/open-order evidence differs')
    active_demo = all(b['environment'] == 'demo' and b['state'] == 'active' for b in snapshot['bindings'])
    _require(bool(snapshot['bindings']) and active_demo, 'Only active Demo bindings are supported')
    checkpoints = snapshot['runtime_checkpoints']
    _require(len(checkpoints) == 1 and checkpoints[0]['financial_hash'] == snapshot['financial_hash'], 'Financial checkpoint differs')
    _require(checkpoints[0]['bindings_hash'] == snapshot['bindings_hash'], 'Account binding checkpoint differs')
    _require(metadata['code'] == '0' and len(metadata['data']) == 1, 'Exact instrument metadata required')
    instrument = metadata['data'][0]
    _require(instrument['ctType'] == 'linear' and instrument['settleCcy'] == 'USDT' and (instrument['state'] == 'live'), 'Unsupported contract')
    pair = instrument['instId'].removesuffix('-SWAP')
    _require(instrument['ctValCcy'] == pair.removesuffix('-USDT'), 'Contract unit differs')
    contract = Decimal(instrument['ctVal'])
    _require(contract.is_finite() and contract > 0, 'Baseline evidence is invalid')
    fills = [f for f in snapshot['fills'] if f['market'] == 'okx_perpetual']
    _require(fills and len(fills) <= 2000, 'Bounded complete perpetual fill history required')
    ids = {f['order_id'] for f in fills}
    orders = {o['id']: o for o in snapshot['orders'] if o['id'] in ids}
    _require(len(orders) == len(ids) and all((not o['creation_pending'] for o in orders.values())), 'Baseline evidence is invalid')
    positions = [p for p in snapshot['positions'] if p['connector_name'] == 'okx_perpetual']
    _require(positions and all((p['trading_pair'] == pair for p in positions)), 'Position scope differs')
    _require(not replay_positions({**snapshot, 'positions': positions, 'fills': fills, 'orders': list(orders.values())}), 'Native financial replay differs')
    actual = {}
    for f in venue['native_fills']['okx_perpetual']:
        key = (f['clOrdId'], f['tradeId'])
        _require(key not in actual, 'Duplicate venue fill')
        actual[key] = f
    expected = {(f['order_id'], f['exchange_trade_id']): f for f in fills}
    _require(len(expected) == len(fills) and expected.keys() == actual.keys(), 'Missing or duplicate fill coverage')
    for key, f in expected.items():
        row = actual[key]
        leg = f['trade_type'] if f['position'] == 'OPEN' else 'BUY' if f['trade_type'] == 'SELL' else 'SELL'
        _require(f['symbol'] == pair and f['position'] in ('OPEN', 'CLOSE') and (f['trade_type'] in ('BUY', 'SELL')), 'Baseline evidence is invalid')
        _require(row['ordId'] == orders[f['order_id']]['exchange_order_id'] and row['instId'] == instrument['instId'], 'Baseline evidence is invalid')
        _require(row['side'] == f['trade_type'].lower() and row['posSide'] == ('long' if leg == 'BUY' else 'short'), 'Baseline evidence is invalid')
        _require(Decimal(row['fillSz']) * contract == Decimal(f['amount']) and Decimal(row['fillPx']) == Decimal(f['price']), 'Fill economics differ')
        _require(Decimal(f['trade_fee']['percent']) == 0, 'Baseline evidence is invalid')
        fees = {}
        for fee in f['trade_fee']['flat_fees']:
            _require(fee['token'] == 'USDT', 'Unknown original opening fee currency')
            fees['USDT'] = fees.get('USDT', Decimal(0)) + Decimal(fee['amount'])
        cost = sum(fees.values(), Decimal(0))
        _require(row['feeCcy'] == 'USDT' and -Decimal(row['fee']) == cost == Decimal(f['trade_fee_in_quote']), 'Original fees differ')
    sorted_fills = sorted(fills, key=lambda f: (int(actual[f['order_id'], f['exchange_trade_id']]['ts']), int(f['exchange_trade_id'])))
    values, anchors = ({}, {})
    for p in positions:
        side = p['side']
        basis = HedgeCostBasis(TradeType[side], Decimal(0), Decimal(0), Decimal(0))
        last_flat, later_closes = (None, 0)
        for f in sorted_fills:
            leg = f['trade_type'] if f['position'] == 'OPEN' else 'BUY' if f['trade_type'] == 'SELL' else 'SELL'
            if leg != side:
                continue
            basis = basis.apply(PositionAction[f['position']], Decimal(f['amount']), Decimal(f['amount']) * Decimal(f['price']), Decimal(f['trade_fee_in_quote']))
            if f['position'] == 'CLOSE':
                later_closes += 1
            if not basis.amount:
                last_flat, later_closes = ((f['order_id'], f['exchange_trade_id']), 0)
        _require(last_flat is not None and later_closes == 0, 'Verified flat anchor and all-opening tail required')
        _require(basis.amount == abs(Decimal(p['amount'])), 'Owned quantity differs')
        if basis.amount:
            with localcontext() as context:
                context.prec = 28
                _require(basis.principal_quote / basis.amount == Decimal(p['breakeven_price']), 'Native entry basis differs')
        key = (p['controller_id'], p['connector_name'], p['trading_pair'], side)
        _require(key not in values, 'Duplicate owned leg')
        values[key] = basis
        anchors[side] = dict(order_id=last_flat[0], trade_id=last_flat[1])
    native_positions = [p for p in venue['native_positions'] if Decimal(p['pos'])]
    _require(all(p['instId'] == instrument['instId'] and p['posSide'] in ('long', 'short')
                 and p.get('mgnMode') == 'cross' for p in native_positions), 'Unreviewed venue position scope')
    _require(len({p['posSide'] for p in native_positions}) == len(native_positions), 'Duplicate venue leg')
    venue_positions = {'BUY' if p['posSide'] == 'long' else 'SELL': Decimal(p['pos']) * contract for p in native_positions}
    _require(venue_positions == {p['side']: abs(Decimal(p['amount'])) for p in positions if Decimal(p['amount'])}, 'Venue position differs')
    return dict(applied=False, requires_operator_review=True, expected_control_version=state['control_version'], expected_financial_hash=snapshot['financial_hash'], expected_bindings_hash=snapshot['bindings_hash'], verified_perpetual_fills=len(fills), flat_anchors=anchors, proposed_reduction_basis=basis_checkpoint(snapshot['financial_hash'], values))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('snapshot', 'venue-proof', 'instrument-metadata', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    paths = (args.snapshot, args.venue_proof, args.instrument_metadata)
    _require(all((p.stat().st_size <= 64 * 1024 ** 2 for p in paths)), 'Evidence exceeds bounded maintenance scope')
    result = prepare(*(json.loads(p.read_text()) for p in paths))
    result['source_sha256'] = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    _require(not args.output.exists(), 'Refusing to overwrite a reviewed proposal')
    result['preparer_sha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    with os.fdopen(os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as output:
        output.write(json.dumps(result, indent=2) + '\n')
    print(json.dumps({k: result[k] for k in ('applied', 'requires_operator_review', 'verified_perpetual_fills')}))


if __name__ == '__main__':
    main()
