"""A legacy remaining-cost proposal cannot invent old costs or clear risk state."""
import copy
from decimal import Decimal as D
from types import SimpleNamespace

import pytest

from hummingbot.core.data_type.common import TradeType
from hummingbot.core.recovery import FINANCIAL_FIELDS, RecoveryBlocked
from hummingbot.strategy_v2.executors.executor_orchestrator import PositionHold
from scripts.mm_reduction_baseline import prepare


def evidence(*, flat_anchor=True, later_close=False):
    source = [('BUY', 'OPEN', '1', '100', '1'), ('SELL', 'CLOSE', '1', '110', '.2'),
              ('SELL', 'OPEN', '1', '100', '.1'), ('BUY', 'CLOSE', '1', '90', '.2')] if flat_anchor else []
    source += [('BUY', 'OPEN', '2', '90', '.3'), ('SELL', 'OPEN', '4', '110', '.4')]
    if later_close:
        source += [('SELL', 'CLOSE', '.5', '95', '.05')]
    fills, orders, native = [], [], []
    holds = {s: PositionHold('okx_perpetual', 'BTC-USDT', TradeType[s]) for s in ['BUY', 'SELL']}
    for i, (side, effect, amount, price, fee) in enumerate(source):
        identity = str(i + 1)
        leg = side if effect == 'OPEN' else ('BUY' if side == 'SELL' else 'SELL')
        fill = dict(market='okx_perpetual', symbol='BTC-USDT', order_id=identity, exchange_trade_id=identity,
                    timestamp=1700000000000 + i * 1000, trade_type=side, position=effect, amount=amount, price=price,
                    trade_fee=dict(percent='0', flat_fees=[dict(token='USDT', amount=fee)]), trade_fee_in_quote=fee)
        fills.append(fill)
        orders.append(dict(id=identity, exchange_order_id=identity, creation_pending=False))
        native.append(dict(clOrdId=identity, ordId=identity, tradeId=identity, instId='BTC-USDT-SWAP',
                           side=side.lower(), posSide='long' if leg == 'BUY' else 'short', fillSz=str(D(amount) / D('.01')),
                           fillPx=price, feeCcy='USDT', fee=str(-D(fee)), ts=str(fill['timestamp'])))
        holds[leg].add_orders_from_executor(SimpleNamespace(id=identity, config=SimpleNamespace(side=TradeType[side]), custom_info={
            'held_position_orders': [dict(client_order_id=identity, trade_type=side, executed_amount_base=amount,
                                          executed_amount_quote=D(amount) * D(price), cumulative_fee_paid_quote=fee)]}))
    positions = []
    for side, hold in holds.items():
        summary = hold.get_position_summary(hold.avg_entry_price)
        positions.append(dict(id=side, controller_id='demo', connector_name='okx_perpetual', trading_pair='BTC-USDT',
                              side=side, unrealized_pnl_quote='0', **{k: str(getattr(summary, k)) for k in FINANCIAL_FIELDS}))
    snapshot = dict(state=dict(desired_state='stopped', control_version=1, acknowledged_version=1, recovery_state={}),
                    orders=orders, fills=fills, positions=positions, runtime_journal=[], submission_attempts=[],
                    bindings=[dict(environment='demo', state='active')], financial_hash='a' * 64, bindings_hash='b' * 64,
                    runtime_checkpoints=[dict(financial_hash='a' * 64, bindings_hash='b' * 64)])
    venue = dict(all_local_fills_verified=len(fills), complete_scans_per_market=2, account_matches={'okx_perpetual': True},
                 pending={'okx_perpetual': []}, native_fills={'okx_perpetual': native},
                 native_positions=[dict(instId='BTC-USDT-SWAP', mgnMode='cross', posSide='long' if p['side'] == 'BUY' else 'short',
                                        pos=str(D(p['amount']) / D('.01'))) for p in positions])
    metadata = dict(code='0', data=[dict(instId='BTC-USDT-SWAP', ctType='linear', settleCcy='USDT',
                                         state='live', ctValCcy='BTC', ctVal='.01')])
    return snapshot, venue, metadata


def test_proposal_uses_only_remaining_opening_fees_and_never_edits_its_inputs():
    inputs = evidence()
    before = copy.deepcopy(inputs)
    result = prepare(*inputs)
    assert not result['applied'] and result['requires_operator_review']
    assert inputs == before
    bases = {r['side']: r['basis'] for r in result['proposed_reduction_basis']['positions']}
    assert D(bases['BUY']['opening_fees_quote']) == D('.3')
    assert D(bases['SELL']['opening_fees_quote']) == D('.4')
    assert D(inputs[0]['positions'][0]['cum_fees_quote']) == D('1.5')


@pytest.mark.parametrize('change', [
    'fee', 'currency', 'units', 'missing', 'duplicate', 'pending', 'risk', 'binding', 'no_binding', 'native_position',
])
def test_incomplete_or_changed_proof_cannot_produce_a_baseline(change):
    s, v, m = evidence()
    if change == 'fee':
        v['native_fills']['okx_perpetual'][-1]['fee'] = '-999'
    elif change == 'currency':
        s['fills'][-1]['trade_fee']['flat_fees'][0]['token'] = 'BTC'
    elif change == 'units':
        m['data'][0]['ctVal'] = '1'
    elif change == 'missing':
        v['native_fills']['okx_perpetual'].pop()
    elif change == 'duplicate':
        v['native_fills']['okx_perpetual'].append(v['native_fills']['okx_perpetual'][-1])
    elif change == 'pending':
        s['submission_attempts'] = [{}]
    elif change == 'risk':
        s['state']['recovery_state']['strategy_checkpoints'] = {'risk': {'_is_stop_triggered': True}}
    elif change == 'binding':
        s['runtime_checkpoints'][0]['bindings_hash'] = 'c' * 64
    elif change == 'no_binding':
        s['bindings'] = []
    else:
        v['native_positions'][0]['pos'] = '12345'
    with pytest.raises((ValueError, RecoveryBlocked)):
        prepare(s, v, m)


@pytest.mark.parametrize('kwargs', [dict(flat_anchor=False), dict(later_close=True)])
def test_unknown_genesis_or_ambiguous_legacy_close_ordering_remains_blocked(kwargs):
    with pytest.raises(ValueError, match='flat anchor'):
        prepare(*evidence(**kwargs))


def test_consumed_journal_and_terminal_attempts_do_not_block_a_valid_proposal():
    s, v, m = evidence()
    s['runtime_journal'] = [dict(applied=True)]
    s['submission_attempts'] = [dict(state='terminal')]
    assert prepare(s, v, m)['requires_operator_review']


def test_foreign_or_oneway_venue_position_cannot_be_hidden_by_hedge_key_mapping():
    for change in [dict(instId='ETH-USDT-SWAP'), dict(posSide='net'), dict(mgnMode='isolated')]:
        s, v, m = evidence()
        v['native_positions'][0].update(change)
        with pytest.raises(ValueError, match='venue position scope'):
            prepare(s, v, m)


def test_duplicate_native_leg_cannot_be_silently_collapsed_into_one_owned_position():
    s, v, m = evidence()
    v['native_positions'].append(copy.deepcopy(v['native_positions'][0]))
    with pytest.raises(ValueError, match='Duplicate venue leg'):
        prepare(s, v, m)
