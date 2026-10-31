"""Known future debits share one order reservation, never its flat allowance per fill."""
from decimal import Decimal
from random import Random
from test.hummingbot.core.test_spot_outflow import fill, order

import pytest

from hummingbot.core.spot_outflow import SpotOutflowConfig, SpotOutflowProjection


def projection(cap='25'):
    config = SpotOutflowConfig(window_seconds=10, max_net_outflow_quote=cap, fee_allowance_rate='0')
    ledger = SpotOutflowProjection(config, 'LIFE-USDT', coverage_start_ms=0)
    ledger.advance(20000)
    assert ledger.reserve(order('a', amount='1'), flat_fee=Decimal('5'))
    return ledger


@pytest.mark.parametrize('reverse', [False, True])
def test_multiple_future_fees_cannot_reuse_the_flat_allowance(reverse):
    ledger = projection()
    rows = [fill('a:1', 20000, amount='.5', fees=[('USDT', '5')]),
            fill('a:2', 20001, amount='.5', fees=[('USDT', '5')])]
    for row in (list(reversed(rows)) if reverse else rows):
        ledger.add_fill(row)
        ledger.add_fill(row)
    assert ledger.reserved() == Decimal('20')
    assert not ledger.reserve(order('b', amount='.5'), flat_fee=Decimal('5'))
    ledger.confirm_terminal('a', Decimal('1'))
    ledger.advance(20001)
    assert ledger.net == Decimal('10') and ledger.reserved() == Decimal('10')
    ledger.advance(20002)
    assert ledger.net == Decimal('20') and ledger.reserved() == 0
    assert not ledger.future_costs and ledger.future_risk == 0
    ledger.advance(30002)
    assert ledger.net == 0 and not ledger.entries


def test_releasing_a_local_reservation_does_not_erase_known_future_cash():
    ledger = projection()
    for index in range(2):
        ledger.add_fill(fill(f'a:{index}', 20000, amount='.5', fees=[('USDT', '5')]))
    ledger.release_unsent('a')
    assert ledger.reserved() == Decimal('20')
    assert not ledger.reserve(order('b', amount='.5'), flat_fee=Decimal('5'))


def test_unreserved_future_debits_and_other_order_budgets_stay_separate():
    ledger = projection(cap='30')
    for index in range(2):
        ledger.add_fill(fill(f'a:{index}', 20000, amount='.5', fees=[('USDT', '5')]))
    ledger.add_fill(fill('other:1', 20000, amount='.3'))
    ledger.add_fill(fill('sell:1', 20000, side='SELL', amount='1', fees=[('USDT', '-1')]))
    assert ledger.reserved() == Decimal('23')
    assert ledger.reserve(order('b', amount='.2'), flat_fee=Decimal('5'))
    assert ledger.reserved() == Decimal('30')
    assert not ledger.reserve(order('c', amount='.001'))


@pytest.mark.parametrize('seed', range(20))
def test_clock_steps_and_reordered_duplicates_match_an_independent_cash_and_obligation_oracle(seed):
    rng = Random(seed)
    config = SpotOutflowConfig(window_seconds=10, max_net_outflow_quote='1000', fee_allowance_rate='.01')
    ledger = SpotOutflowProjection(config, 'LIFE-USDT', coverage_start_ms=0)
    ledger.advance(20000)
    flat = Decimal('.03')
    rows, totals = [], {}
    for identity in ('a', 'b', 'c'):
        assert ledger.reserve(order(identity, amount='1'), flat_fee=flat)
        count = rng.choice((1, 2))
        totals[identity] = Decimal('.5') * count
        for index in range(count):
            rows.append(fill(f'{identity}:{index}', rng.choice((20000, 20010, 20050, 20100)), amount='.5',
                             fees=[('USDT', rng.choice(('.01', '.03', '.10', '-.01')))]))
    rows.extend([fill('foreign:1', 20050, amount='.1'), fill('sell:1', 20010, side='SELL', amount='.2')])
    arrival = rows * 2
    rng.shuffle(arrival)
    for row in arrival:
        ledger.add_fill(row)
    for identity, total in totals.items():
        ledger.confirm_terminal(identity, total)
    for now in (20000, 20001, 20011, 20051, 20101, 30001, 30101):
        ledger.advance(now)
        future, matured = {}, {key: Decimal('0') for key in totals}
        net = Decimal('0')
        for row in rows:
            cash = row['price'] * row['amount'] * (1 if row['trade_type'] == 'BUY' else -1)
            cash += sum((Decimal(fee['amount']) for fee in row['trade_fee']['flat_fees']), Decimal('0'))
            if now - 10000 <= row['timestamp'] < now:
                net += cash
            if row['timestamp'] >= now:
                future[row['order_id']] = future.get(row['order_id'], Decimal('0')) + max(Decimal('0'), cash)
            elif row['order_id'] in matured:
                matured[row['order_id']] += row['amount']
        # A reservation is one obligation for the whole order. Future cash is
        # a second observation of that obligation, not free coverage per fill.
        expected = Decimal('0')
        for identity, total in totals.items():
            known = future.pop(identity, Decimal('0'))
            pending = ((1 - matured[identity]) * Decimal('10.1') + flat
                       if matured[identity] < total else Decimal('0'))
            expected += max(pending, known)
        expected += sum(future.values(), Decimal('0'))
        assert ledger.net == net and ledger.reserved() == expected
        assert ledger.future_risk >= 0
        if not ledger.future:
            assert ledger.future_risk == 0 and not ledger.future_costs
