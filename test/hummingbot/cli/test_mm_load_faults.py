from copy import deepcopy
from decimal import Decimal

import pytest

from scripts.mm_load_faults import complete_quote_cohort, restriction_cancel_bounds


def cohort(*, long='0', short='0'):
    rows = []

    def add(instrument, side, amount, leg=None, filled='0'):
        rows.append(dict(clOrdId=str(len(rows)), instId=instrument, side=side,
                         posSide=leg, sz=str(amount), accFillSz=filled, ordType='post_only'))

    for side in ('buy', 'sell'):
        for _ in range(3):
            add('LIFE-USDT', side, '1')
    for side, leg, quantity in (('buy', 'short', short), ('sell', 'long', long)):
        if Decimal(quantity):
            add('LIFE-USDT-SWAP', side, quantity, leg)
        else:
            for _ in range(3):
                add('LIFE-USDT-SWAP', side, '1', 'long' if side == 'buy' else 'short')
    positions = [dict(instId='LIFE-USDT-SWAP', posSide=leg, pos=quantity)
                 for leg, quantity in (('long', long), ('short', short))]
    return rows, positions


@pytest.mark.parametrize('long,short', [('0', '0'), ('0.1', '0'), ('0', '0.1'), ('0.1', '0.2')])
def test_complete_reduction_cohort_reserves_the_owned_legs(long, short):
    rows, positions = cohort(long=long, short=short)
    assert complete_quote_cohort(rows, positions, reduction=True)


def test_partial_close_counts_only_its_remaining_reservation():
    rows, positions = cohort(short='0.1')
    closing = next(row for row in rows if row['instId'].endswith('-SWAP') and row['side'] == 'buy')
    closing.update(sz='0.2', accFillSz='0.1')
    assert complete_quote_cohort(rows, positions, reduction=True)


@pytest.mark.parametrize('change', [
    'missing_spot', 'missing_open', 'missing_close', 'underreserve',
    'overreserve', 'wrong_leg', 'duplicate', 'nonfinite', 'negative', 'overfilled'])
def test_reduction_cohort_rejects_incomplete_or_invalid_reservations(change):
    rows, positions = cohort(short='0.1')
    closing = next(row for row in rows if row['instId'].endswith('-SWAP') and row['side'] == 'buy')
    if change == 'missing_spot':
        rows.pop(0)
    elif change == 'missing_open':
        rows.remove(next(row for row in rows if row['instId'].endswith('-SWAP') and row['side'] == 'sell'))
    elif change == 'missing_close':
        rows.remove(closing)
    elif change == 'duplicate':
        rows.append(deepcopy(closing))
    else:
        closing.update({'underreserve': dict(sz='0.09'), 'overreserve': dict(sz='0.11'),
                        'wrong_leg': dict(posSide='long'), 'nonfinite': dict(sz='NaN'),
                        'negative': dict(sz='-0.1'), 'overfilled': dict(accFillSz='0.2')}[change])
    assert not complete_quote_cohort(rows, positions, reduction=True)


def test_default_open_only_cohort_still_requires_twelve_quotes():
    rows, positions = cohort()
    assert complete_quote_cohort(rows, positions)
    assert not complete_quote_cohort(rows[:-1], positions)


def test_closing_latency_retains_input_bound_but_uses_observed_restriction_decision():
    baseline = [dict(clOrdId='open', instId='LIFE-USDT', side='buy'),
                dict(clOrdId='close', instId='LIFE-USDT-SWAP', side='buy', posSide='short')]
    observed = [dict(row, state='canceled', cancel_terminal_ms=4999 if row['clOrdId'] == 'open' else 6500)
                for row in baseline]
    bounds = restriction_cancel_bounds(baseline, observed, 0, {'LIFE-USDT-SWAP': 3000})
    assert bounds['all_input_upper_bound_ms']['LIFE-USDT-SWAP'] == 6500
    assert bounds['opening_input_upper_bound_ms']['LIFE-USDT'] == 4999
    assert bounds['closing_decision_upper_bound_ms']['LIFE-USDT-SWAP'] == 3500


def test_missing_closing_decision_cannot_pass_as_zero_latency():
    baseline = [dict(clOrdId='close', instId='LIFE-USDT-SWAP', side='sell', posSide='long')]
    with pytest.raises(KeyError):
        restriction_cancel_bounds(baseline, [dict(baseline[0], state='canceled', cancel_terminal_ms=10)], 0, {})


def test_late_opening_cancel_keeps_its_original_input_budget():
    baseline = [dict(clOrdId='open', instId='LIFE-USDT-SWAP', side='buy', posSide='long')]
    bounds = restriction_cancel_bounds(
        baseline, [dict(baseline[0], state='canceled', cancel_terminal_ms=5001)], 0, {'LIFE-USDT-SWAP': 4000})
    assert bounds['opening_input_upper_bound_ms']['LIFE-USDT-SWAP'] == 5001
