"""Original non-SPOT flows with causal cash-source constraints, not full parity."""
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres import (
    test_postgres_capital as capital,
    test_postgres_cashflow_repair as cashflow,
    test_postgres_funding as funding,
    test_postgres_joint_batch as joint,
    test_postgres_joint_repair as repair,
    test_postgres_shared_capital as shared,
    test_postgres_shared_release as release,
    test_postgres_swap_fills as swap,
)
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_fill_projection import (
    budgets,
    install,
    prefix,
)

import asyncpg
import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError


@pytest.mark.asyncio
@pytest.mark.parametrize('scenario', ['swap', 'swap_close', 'funding_debit', 'funding_credit',
                                      'transfer', 'joint', 'shared', 'release', 'repair', 'cashflow_repair'])
async def test_source_prefix_preserves_original_product_flow(database, tmp_path, scenario):
    # Fresh enrollment; all original source validation, permission checks and
    # physical COMMIT guards still run. No metadata timestamps are renewed.
    c = database[0]
    await install(c, products=True)
    if scenario == 'swap':
        await swap.test_swap_fill_records_fee_and_basis_once_without_spot_principal_or_claim_release(database)
    elif scenario == 'swap_close':
        await swap.test_native_close_credits_only_replayed_realized_pnl_and_actual_fee_without_releasing_claims(database)
    elif scenario.startswith('funding'):
        await funding.test_settled_funding_adjusts_only_actual_cash_once_and_never_spot_transfer_or_execution_loss(
            database, '-.1' if scenario == 'funding_debit' else '.2')
    elif scenario == 'transfer':
        await capital.test_deposit_adjusts_nav_without_profit_budget_reset_or_double_counting(database)
    elif scenario == 'joint':
        await joint.test_joint_fill_and_funding_apply_once_from_one_unchanged_final_source(database)
    elif scenario == 'shared':
        await shared.test_shared_spot_swap_cannot_allocate_same_cash_and_collateral_twice(database, tmp_path)
    elif scenario == 'release':
        await release.test_terminal_release_retires_only_original_claim_keeps_position_cash_budget_and_receipts(database)
    elif scenario == 'repair':
        await repair.test_signed_source_resolution_applies_actual_economics_once_keeps_raw_pending_and_never_activates(database)
    else:
        await cashflow.test_signed_cashflow_pending_books_bill_once_without_overwriting_original(database, 'USDT', False)
    account = await c.fetchval('SELECT account_id FROM life_financial.accounts')
    money = await prefix(c, account)
    assert await c.fetchval('SELECT count(*) FROM life_financial.financial_events WHERE account_id=$1', account) == (0 if money is None else money['ordinal'])
    if money is not None:
        total = await c.fetchrow('SELECT sum(base_delta) AS base,sum(quote_delta) AS quote FROM life_financial.financial_events WHERE account_id=$1', account)
        assert (total['base'], total['quote']) == (money['base_after'], money['quote_after'])
    for b in await budgets(c, account):
        # Untouched native windows may legitimately have no budget event.
        assert (b['committed'], b['incurred']) == (b['committed_after'] or 0, b['incurred_after'] or 0)


@pytest.mark.asyncio
@pytest.mark.parametrize('enrolled', [False, True], ids=['counterexample', 'source_bound'])
@pytest.mark.parametrize('kind', ['swap_fee', 'funding', 'cashflow'])
async def test_product_source_edit_counterexample_and_internal_fk_refusal(database, kind, enrolled):
    c = database[0]
    await install(c, products=enrolled)
    if kind == 'swap_fee':
        await swap.test_swap_fill_records_fee_and_basis_once_without_spot_principal_or_claim_release(database)
        table, update, verifier = 'fees', 'signed_fee=signed_fee-1', 'swap_accounting_verified'
    elif kind == 'funding':
        await funding.test_settled_funding_adjusts_only_actual_cash_once_and_never_spot_transfer_or_execution_loss(database, '-.1')
        table, update, verifier = 'funding_cash_payments', 'signed_payment_usdt=signed_payment_usdt-1', 'funding_verified'
    else:
        await capital.test_deposit_adjusts_nav_without_profit_budget_reset_or_double_counting(database)
        table, update, verifier = 'cashflows', 'value_usdt=value_usdt+1', 'spot_capital_verified'
    account = await c.fetchval('SELECT account_id FROM life_financial.accounts')
    before = await prefix(c, account)

    async def mutate():
        async with c.transaction():
            await c.execute(f'ALTER TABLE life_financial.{table} DISABLE TRIGGER USER')
            await c.execute(f'UPDATE life_financial.{table} SET {update} WHERE account_id=$1', account)
    if enrolled:
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await mutate()
        assert await c.fetchval(f'SELECT life_financial.{verifier}($1)', account)
    else:
        await mutate()
        assert not await c.fetchval(f'SELECT life_financial.{verifier}($1)', account)
    assert await prefix(c, account) == before


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['swap', 'funding', 'cashflow'])
async def test_product_missing_capture_rolls_back_original_money_at_commit(database, kind):
    c = database[0]
    await install(c, products=True)
    await c.execute('ALTER TABLE life_financial.financial_events DISABLE TRIGGER prototype_product_source')
    with pytest.raises(StorageError, match='^PG_CONSTRAINT_REJECTED$'):
        if kind == 'swap':
            await swap.test_swap_fill_records_fee_and_basis_once_without_spot_principal_or_claim_release(database)
        elif kind == 'funding':
            await funding.test_settled_funding_adjusts_only_actual_cash_once_and_never_spot_transfer_or_execution_loss(database, '-.1')
        else:
            await capital.test_deposit_adjusts_nav_without_profit_budget_reset_or_double_counting(database)
    await c.execute('ALTER TABLE life_financial.financial_events ENABLE TRIGGER prototype_product_source')
    assert await c.fetchval("SELECT count(*) FROM life_financial.financial_events WHERE kind IN ('FILL','FUNDING','CASHFLOW')") == 0
    assert await c.fetchval('SELECT count(*) FROM fill_projection_spike.swap_source') == 0
    assert await c.fetchval('SELECT count(*) FROM fill_projection_spike.funding_source') == 0
    assert await c.fetchval('SELECT count(*) FROM fill_projection_spike.cashflow_source') == 0
