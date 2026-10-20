"""Captured action admission is immutable, typed and independent of a driver."""
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.test_quote_action_contracts import action, facts

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.action_preparation import PrepareQuoteAction, QuoteAdmissionFacts
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
from hummingbot.strategy_v2.life_liquidity.storage.financial import PrepareSpot


def admission():
    binding, at = facts().binding, datetime(2026, 10, 16, tzinfo=timezone.utc)
    return QuoteAdmissionFacts(binding, at, at, at, at, D('1'), D('1'), D('.9'), D('1.1'),
                               D('.01'), D('0'), D('0'), D('0'), D('0'), False, 'independent_market')


def prepare():
    return PrepareSpot('prepare', 'intent', 'wire', 'LIFE-USDT', 'BUY', D('2'), D('1'), D('1'), D('.02'),
                       'session', 1, 1, 0, D('.1'), D('.1'))


@pytest.mark.parametrize('changes', [dict(reference_price_usdt=D('0')), dict(exit_value_usdt=D('NaN')), dict(impact_cost_usdt=D('-1')),
                                     dict(best_bid_usdt=D('1.1')), dict(maker_fee_rate=.01), dict(binding=None),
                                     dict(exit_source_kind='quote_model'), dict(exit_value_includes_impact=1),
                                     dict(reference_at=datetime(2026, 10, 16)),
                                     dict(fee_at=datetime(2026, 10, 16, tzinfo=timezone.utc) + timedelta(seconds=1))])
def test_unqualified_admission_facts_are_denied(changes):
    with pytest.raises(StorageError):
        replace(admission(), **changes)


@pytest.mark.parametrize('changes', [dict(intent_id='other'), dict(session_id='other'), dict(epoch=2), dict(config_version=2),
                                     dict(side='SELL'), dict(quote_level=1), dict(risk_epoch=0), dict(reference_price_usdt=D('2'))])
def test_preparation_scope_cannot_diverge_from_issued_action(changes):
    with pytest.raises(StorageError):
        PrepareQuoteAction(action(), replace(prepare(), **changes), admission())


def test_command_exact_roundtrip_and_json_copy_preserves_financial_values():
    command = PrepareQuoteAction(action(), prepare(), admission())
    assert PrepareQuoteAction.from_json(command.json()) == command
    saved = command.json()
    saved['prepare']['quantity_base'] = '999'
    assert command.prepare.quantity_base == D('2')
    with pytest.raises(FrozenInstanceError):
        command.admission.impact_cost_usdt = D('1')


def test_shared_preparation_retains_explicit_risk_epoch_without_inventing_one_for_spot_only():
    plain = PrepareQuoteAction(action(), prepare(), admission())
    shared = PrepareQuoteAction(action(), replace(prepare(), risk_epoch=7), admission())
    assert 'risk_epoch' not in plain.json()['prepare']
    assert shared.json()['prepare']['risk_epoch'] == 7
    assert PrepareQuoteAction.from_json(shared.json()) == shared


def test_missing_source_kind_never_implicitly_labels_an_independent_valuation():
    values = dict(admission().__dict__)
    values.pop('exit_source_kind')
    with pytest.raises(TypeError):
        QuoteAdmissionFacts(**values)
