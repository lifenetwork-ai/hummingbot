"""Economic and reservation contract for the opt-in close-first policy."""
from decimal import Decimal, localcontext
from fractions import Fraction
from random import Random

import pytest

from hummingbot.core.data_type.common import PositionAction, TradeType
from hummingbot.strategy_v2.utils.perpetual_reduction import (
    CloseReservation,
    ClosingFeeBound,
    HedgeCostBasis,
    ReductionView,
    basis_checkpoint,
    restore_basis_checkpoint,
)

D = Decimal


def basis(side=TradeType.BUY, amount='2', principal='200', fees='2'):
    return HedgeCostBasis(side, D(amount), D(principal), D(fees))


@pytest.mark.parametrize('side', [TradeType.BUY, TradeType.SELL])
def test_partial_close_allocates_opening_fees_once_and_flat_discards_no_cost(side):
    start = basis(side)
    partial = start.apply(PositionAction.CLOSE, D('.5'), D('60'), D('.06'))
    assert start.amount == D('2')  # The candidate cannot mutate its committed source.
    assert partial.amount == D('1.5')
    assert partial.principal_quote == D('150')
    assert partial.opening_fees_quote == D('1.5')
    # Closing fees have already been paid; they cannot become new opening fees.
    assert partial.apply(PositionAction.CLOSE, D('1.5'), D('180'), D('.18')) == basis(side, '0', '0', '0')


def test_reopen_after_partial_uses_remaining_cost_and_new_fee_only():
    remaining = basis().apply(PositionAction.CLOSE, D('.5'), D('60'), D('.6'))
    added = remaining.apply(PositionAction.OPEN, D('2'), D('240'), D('2.4'))
    assert added == basis(amount='3.5', principal='390', fees='3.9')
    assert added != basis(amount='3.5', principal='390', fees='5')


def test_rebates_cannot_lower_the_required_closing_price():
    opened = basis(amount='0', principal='0', fees='0').apply(
        PositionAction.OPEN, D('2'), D('200'), D('-1'))
    assert opened.opening_fees_quote == 0
    with pytest.raises(ValueError):
        ClosingFeeBound(D('-.001'), D('0'))


@pytest.mark.parametrize('side', [TradeType.BUY, TradeType.SELL])
def test_tick_rounded_boundary_covers_allocated_opening_and_actual_bound_close_fees(side):
    held = basis(side)
    fee = ClosingFeeBound(D('.001'), D('.03'))
    quantity = D('.3')
    price = held.closing_price(quantity, fee, D('.01'))
    signed_pnl = quantity * (price - D('100')) * (1 if side == TradeType.BUY else -1)
    assert signed_pnl - D('.3') - quantity * price * fee.rate - fee.flat_quote >= 0
    assert held.covers(price, quantity, fee)
    adverse_tick = price + (D('-.01') if side == TradeType.BUY else D('.01'))
    assert not held.covers(adverse_tick, quantity, fee)


def test_boundary_does_not_round_toward_a_loss_under_low_ambient_decimal_precision():
    with localcontext() as context:
        context.prec = 8
        held = basis(amount='3', principal='313.333333333333333333', fees='.654321')
        price = held.closing_price(D('.0003'), ClosingFeeBound(D('.001234'), D('.00007')), D('.00000001'))
        assert held.covers(price, D('.0003'), ClosingFeeBound(D('.001234'), D('.00007')))


@pytest.mark.parametrize('bad', ['NaN', 'Infinity', '-Infinity', '-.01'])
def test_unavailable_or_invalid_cost_and_fee_data_never_becomes_zero(bad):
    for index in range(3):
        values = [D('2'), D('200'), D('2')]
        values[index] = D(bad)
        with pytest.raises(ValueError):
            HedgeCostBasis(TradeType.BUY, *values)
    with pytest.raises(ValueError):
        ClosingFeeBound(D(bad), D('0'))


def test_overclose_or_empty_basis_is_not_a_new_opposite_position():
    with pytest.raises(ValueError):
        basis().apply(PositionAction.CLOSE, D('2.01'), D('201'), D('.2'))
    with pytest.raises(ValueError):
        basis(amount='0', principal='0', fees='0').closing_price(D('.1'), ClosingFeeBound(D('.001'), D('0')), D('.01'))
    with pytest.raises(ValueError):
        basis().apply(PositionAction.NIL, D('.1'), D('10'), D('.01'))


def test_durable_basis_round_trip_rejects_unknown_format_or_fields():
    held = basis(TradeType.SELL)
    assert HedgeCostBasis.restore(held.snapshot()) == held
    for changes in ({'version': 2}, {'amount': 'NaN'}, {'opening_fees_quote': None}, {'future_field': True}):
        with pytest.raises(ValueError):
            HedgeCostBasis.restore({**held.snapshot(), **changes})


def view(long='0', short='0', venue_long=None, venue_short=None, reservations=()):
    return ReductionView(
        basis(TradeType.BUY, long, str(D(long) * 100), long),
        basis(TradeType.SELL, short, str(D(short) * 100), short),
        D(long if venue_long is None else venue_long),
        D(short if venue_short is None else venue_short),
        reservations,
    )


@pytest.mark.parametrize('long,short,expected', [
    ('0', '0', (PositionAction.OPEN, PositionAction.OPEN)),
    ('1', '0', (PositionAction.OPEN, PositionAction.CLOSE)),
    ('0', '1', (PositionAction.CLOSE, PositionAction.OPEN)),
    ('1', '1', (PositionAction.CLOSE, PositionAction.CLOSE)),
])
def test_effect_matrix_is_per_quote_side(long, short, expected):
    assert tuple(view(long, short).decide(side).effect for side in (TradeType.BUY, TradeType.SELL)) == expected


def test_levels_share_minimum_owned_and_venue_quantity_without_netting_hedge_legs():
    pending = CloseReservation('sell_0', TradeType.BUY, D('.4'))
    state = view('1', '9', venue_long='.7', reservations=(pending,))
    sell = state.decide(TradeType.SELL)
    assert sell.effect == PositionAction.CLOSE and sell.leg == TradeType.BUY
    assert sell.available == D('.3')
    assert state.decide(TradeType.BUY).available == D('9')
    # Rechecking the owning quote excludes only its own reservation.
    assert state.decide(TradeType.SELL, reservation_id='sell_0').available == D('.7')


@pytest.mark.parametrize('terminal,applied,free', [
    (False, False, '0'), (True, False, '0'), (False, True, '0'), (True, True, '1'),
])
def test_pending_cancel_and_unapplied_terminal_fill_keep_the_reservation(terminal, applied, free):
    pending = CloseReservation('sell_0', TradeType.BUY, D('1'), terminal, applied)
    decision = view('1', reservations=(pending,)).decide(TradeType.SELL)
    assert decision.effect == PositionAction.CLOSE
    assert decision.available == D(free)
    assert decision.reason == (None if D(free) else 'reduction_reserved')


def test_foreign_inventory_and_unapplied_open_fill_do_not_create_owned_closing_authority():
    decision = view('0', venue_long='1').decide(TradeType.SELL)
    assert decision.effect == PositionAction.CLOSE and decision.available == 0
    assert decision.reason == 'reduction_ownership_unavailable'
    # Ledger ahead of the venue is also insufficient to close.
    decision = view('1', venue_long='0').decide(TradeType.SELL)
    assert decision.effect == PositionAction.CLOSE and decision.available == 0


def test_no_open_fallback_when_all_opposing_exposure_is_reserved_or_unknown():
    decision = view('1', reservations=(CloseReservation('close', TradeType.BUY, D('1')),)).decide(TradeType.SELL)
    assert decision.effect == PositionAction.CLOSE and decision.reason == 'reduction_reserved'
    state = ReductionView(None, basis(TradeType.SELL, '0', '0', '0'), D('0'), D('0'))
    decision = state.decide(TradeType.SELL)
    assert decision.effect is None and decision.reason == 'reduction_basis_unavailable'


def test_final_check_after_venue_shrink_cannot_create_capacity_from_its_own_reservation():
    pending = CloseReservation('close', TradeType.BUY, D('1'))
    decision = view('1', venue_long='.2', reservations=(pending,)).decide(TradeType.SELL, reservation_id='close')
    assert decision.available == D('.2')
    assert decision.available < pending.amount
    assert view('1', venue_long='0', reservations=(pending,)).decide(TradeType.SELL).effect == PositionAction.CLOSE


def test_invalid_reservation_set_and_venue_observation_fail_closed():
    pending = CloseReservation('close', TradeType.BUY, D('1'))
    with pytest.raises(ValueError):
        view('1', reservations=(pending, pending))
    for invalid in ('NaN', '-1', 'Infinity'):
        with pytest.raises(ValueError):
            view('1', venue_long=invalid)
        with pytest.raises(ValueError):
            CloseReservation('close', TradeType.BUY, D(invalid))


def test_boundaries_cover_costs_using_independent_exact_rational_arithmetic():
    rng = Random(11)
    for side in (TradeType.BUY, TradeType.SELL):
        for _ in range(100):
            size = D(rng.randint(1, 100)) / 100
            principal = D(rng.randint(10000, 100000)) / 100
            fees = D(rng.randint(1, 50)) / 1000
            held = HedgeCostBasis(side, size, principal, fees)
            amount = size / 10
            fee = ClosingFeeBound(D(rng.randint(0, 1000)) / 1000000, D(rng.randint(0, 100)) / 1000000)
            tick = D('.00000001')
            with localcontext() as context:
                context.prec = 6
                price = held.closing_price(amount, fee, tick)
            cost = Fraction(fees) * Fraction(amount) / Fraction(size)

            def net(candidate):
                gross = Fraction(amount) * (Fraction(candidate) - Fraction(principal) / Fraction(size))
                if side == TradeType.SELL:
                    gross = -gross
                return gross - cost - Fraction(amount) * Fraction(candidate) * Fraction(fee.rate) - Fraction(fee.flat_quote)
            assert net(price) >= 0
            adverse = price - tick if side == TradeType.BUY else price + tick
            assert net(adverse) < 0


@pytest.mark.parametrize('side', [TradeType.BUY, TradeType.SELL])
def test_repeated_fee_partition_is_conservative_even_for_repeating_fractions(side):
    held = basis(side, '7', '711.111', '1')
    for remaining in range(6, 0, -1):
        held = held.apply(PositionAction.CLOSE, D('1'), D('100'), D('.1'))
        assert Fraction(held.opening_fees_quote) >= Fraction(remaining, 7)
        actual_principal = Fraction(D('711.111')) * remaining / 7
        assert (Fraction(held.principal_quote) >= actual_principal if side == TradeType.BUY
                else Fraction(held.principal_quote) <= actual_principal)


def test_checkpoint_scope_hash_version_and_duplicate_rows_are_not_optional():
    key = ('maker', 'okx_perpetual', 'BTC-USDT', 'BUY')
    value = basis_checkpoint('a' * 64, {key: basis()})
    assert restore_basis_checkpoint(value, 'a' * 64) == {key: basis()}
    for change in ({'version': True}, {'financial_hash': 'b' * 64}, {'unexpected': 1},
                   {'positions': value['positions'] * 2}):
        with pytest.raises(ValueError):
            restore_basis_checkpoint({**value, **change}, 'a' * 64)
    for bad_key in (('foreign', 'okx', 'BTC-USDT', 'BUY'), ('maker', 'okx_perpetual', 'BTC-USDT', 'SELL'), 1):
        with pytest.raises(ValueError):
            basis_checkpoint('a' * 64, {bad_key: basis()})


def test_extreme_numeric_sizes_are_bounded_before_quote_math():
    for invalid in ('1e1000', '1e-1000', '1.' + '1' * 300):
        with pytest.raises(ValueError):
            basis(amount=invalid)
