"""Explicit close-first load fixture and evidence, without production policy changes."""
from decimal import Decimal


def enable_reduction(profiles):
    market = profiles['markets']['perpetual']
    market['inventory_reduction'] = dict(mode='close_first', price_mode='breakeven')
    guards = market.setdefault('quote_guards', {})
    guards['enabled'] = True
    for key, value in dict(max_deviation_bps='200', resume_deviation_bps='100', recovery_delay_seconds='1').items():
        if guards.get(key) is None:
            guards[key] = value
    market.setdefault('spread_policy', {})['fee_floor'] = True


def closing_activity(ledger):
    orders = {str(row['ordId']): row for row in ledger['orders']
              if row['instId'].endswith('-SWAP')
              and (row['posSide'], row['side']) in (('long', 'sell'), ('short', 'buy'))}
    fills = [row for row in ledger['fills'] if str(row['ordId']) in orders]
    identities = [(row['ordId'], row['tradeId']) for row in fills]
    assert len(identities) == len(set(identities)), 'duplicate closing fill'
    assert fills, 'No actual closing fill exercised the enabled reduction policy'
    assert {orders[str(row['ordId'])]['posSide'] for row in fills} == {'long', 'short'}, 'Both hedge legs require a closing fill'
    return dict(closing_orders=len(orders), closing_fills=len(fills),
                closed_base=str(sum((Decimal(row['fillSz']) * Decimal('.01') for row in fills), Decimal(0))),
                both_hedge_legs_closed=True)
