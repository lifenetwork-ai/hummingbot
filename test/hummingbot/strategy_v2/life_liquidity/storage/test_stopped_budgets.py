"""A cold import cannot reset losses, inventory cost floors or budget keys."""
import json
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.test_stopped_capital import recut, source_fixture

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError, encode
from hummingbot.strategy_v2.life_liquidity.storage.stopped_capital import project_stopped_capital


@pytest.mark.asyncio
@pytest.mark.parametrize('paid', [False, True])
async def test_cold_budget_projection_retains_original_rows_and_all_period_totals(tmp_path, paid):
    source, _ = await source_fixture(tmp_path, paid=paid)
    body = project_stopped_capital(source, max_rows=10000).json()
    budgets = body['budgets']
    for name in ('subsidy', 'loss'):
        old = json.loads(dict(source.base.core.journal_images)[name])
        row = budgets[name]
        assert row['journal'] == old
        assert D(row['totals']['campaign']) == (D('.12') if paid else 0)
        assert sum(map(D, row['totals']['by_session'].values()), D('0')) == D(row['totals']['campaign'])
        assert sum(map(D, row['totals']['by_day'].values()), D('0')) == D(row['totals']['campaign'])
    if paid:
        # Terminal order still holds inventory: no fabricated settlement/release.
        assert budgets['subsidy']['journal']['entries']['intent1']['actual'] is None
        assert budgets['subsidy']['journal']['entries']['intent1']['fill_floor'] == '0.12'
    assert budgets['exit_subsidy'] is None


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['loss_missing', 'loss_amount', 'loss_day', 'loss_session',
                                   'subsidy_floor', 'subsidy_session', 'subsidy_policy',
                                   'subsidy_campaign', 'subsidy_extra', 'negative_extra_loss', 'invalid_day'])
async def test_rehashed_budget_corruption_is_rejected(tmp_path, fault):
    source, _ = await source_fixture(tmp_path)
    checkpoint = json.loads(source.base.core.checkpoint_bytes)
    journals = dict(source.base.core.journal_images)
    name = 'loss' if fault.startswith('loss') or fault in ('negative_extra_loss', 'invalid_day') else 'subsidy'
    journal = json.loads(journals[name])
    if fault == 'loss_missing':
        del journal['events']['fill:trade1']
    elif fault in ('loss_amount', 'loss_day', 'loss_session'):
        field, value = {'loss_amount': ('loss_quote', '0'), 'loss_day': ('day', '2099-01-01'),
                        'loss_session': ('session_id', 'different-session')}[fault]
        journal['events']['fill:trade1'][field] = value
    elif fault == 'negative_extra_loss':
        journal['events']['fake'] = dict(session_id='other', day='2026-01-01', loss_quote='-1')
    elif fault == 'invalid_day':
        journal['events']['opening']['day'] = '2026-02-30'
    elif fault == 'subsidy_floor':
        journal['entries']['intent1']['fill_floor'] = '0'
    elif fault == 'subsidy_session':
        journal['entries']['intent1']['session_id'] = 'other'
    elif fault == 'subsidy_policy':
        journal['policy']['campaign_limit_quote'] = '999999'
    elif fault == 'subsidy_campaign':
        journal['campaign_id'] = 'fresh-budget'
    else:
        journal['entries']['foreign'] = dict(journal['entries']['intent1'])
    journals[name] = encode(journal)
    corrupted = recut(source, checkpoint, journals=tuple(sorted(journals.items())))
    with pytest.raises(StorageError, match='STOPPED_BUDGET'):
        project_stopped_capital(corrupted, max_rows=10000)


@pytest.mark.asyncio
async def test_historical_non_fill_costs_are_retained_across_days_and_sessions(tmp_path):
    source, _ = await source_fixture(tmp_path)
    checkpoint = json.loads(source.base.core.checkpoint_bytes)
    journals = dict(source.base.core.journal_images)
    loss = json.loads(journals['loss'])
    # A retained historical cost is not erased merely because it is not a fill.
    loss['events']['prior-day-cost'] = dict(session_id='old-session', day='2020-01-01', loss_quote='2.50')
    journals['loss'] = encode(loss)
    source = recut(source, checkpoint, journals=tuple(sorted(journals.items())))
    result = project_stopped_capital(source, max_rows=10000).json()['budgets']['loss']
    assert D(result['totals']['campaign']) == D('2.62')
    assert result['totals']['by_session']['old-session'] == '2.50'
    assert result['totals']['by_day']['2020-01-01'] == '2.50'
    assert result['journal'] == loss


@pytest.mark.asyncio
async def test_budget_capacity_is_checked_before_aggregation(tmp_path):
    source, _ = await source_fixture(tmp_path, paid=False)
    checkpoint = json.loads(source.base.core.checkpoint_bytes)
    journals = dict(source.base.core.journal_images)
    loss = json.loads(journals['loss'])
    loss['events'].update({f'old-{i}': dict(loss['events']['opening']) for i in range(10)})
    journals['loss'] = encode(loss)
    source = recut(source, checkpoint, journals=tuple(sorted(journals.items())))
    with pytest.raises(StorageError, match='STOPPED_BUDGET_CAPACITY_EXCEEDED'):
        project_stopped_capital(source, max_rows=5)


@pytest.mark.asyncio
async def test_budget_totals_do_not_round_away_original_fractional_spending(tmp_path):
    source, _ = await source_fixture(tmp_path)
    checkpoint = json.loads(source.base.core.checkpoint_bytes)
    journals = dict(source.base.core.journal_images)
    loss = json.loads(journals['loss'])
    amount = '12345678901234567890123456789.123456789012345678'
    loss['events']['retained-cost'] = dict(session_id='prior', day='2020-01-01', loss_quote=amount)
    journals['loss'] = encode(loss)
    source = recut(source, checkpoint, journals=tuple(sorted(journals.items())))
    result = project_stopped_capital(source, max_rows=10000).json()['budgets']['loss']
    assert result['totals']['campaign'] == '12345678901234567890123456789.243456789012345678'
    assert result['totals']['by_session']['prior'] == amount
    assert result['totals']['by_day']['2020-01-01'] == amount
