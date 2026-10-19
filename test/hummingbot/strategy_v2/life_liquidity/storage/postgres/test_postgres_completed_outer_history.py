"""Original completed File cash parents compose with later native recovery."""
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_legacy_core_cashflows import restore
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_legacy_joint import apply_recovery, setup

import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['outer_before_cash', 'wallet_after', 'receipt', 'cash_done'])
@pytest.mark.parametrize('booked', [False, True])
@pytest.mark.parametrize('entry,fee', [(D('1'), D('-.001')), (D('.8'), D('-.001')), (D('1.2'), D('.001'))])
async def test_prior_completed_file_swap_cash_and_later_funding_keep_exact_original_money_and_native_prefix(database, tmp_path, monkeypatch, stage, booked, entry, fee):
    expected = {}
    values, batch, command, cut, facts, approval, importer, store, source = await setup(
        database, tmp_path, monkeypatch, stage, entry=entry, fee=fee, funding=D('-.1'), book_native_swap=booked,
        file_result=expected, prior_outer=True)
    financial, cfg, owner = values[:3]
    cold = None
    try:
        original = await database[0].fetchval("SELECT to_jsonb(o) FROM life_financial.operations o WHERE account_id=$1 AND operation_id='native-prior-swap'", cfg.account_id)
        assert bool(original) == booked
        before, claims = await financial.snapshot(cfg.binding), await values[5].claims(cfg.binding)
        assert len(source.original_operations) == (1 if stage == 'outer_before_cash' else 2)
        await importer.import_pending(command, source_store=store, approval=approval, cut=cut, facts=facts)
        after = await financial.snapshot(cfg.binding)
        assert (after.balance, after.budget) == (before.balance, before.budget)
        await financial.close()
        cold, capital, batch, importer = await restore(cfg, owner, database[0])
        await apply_recovery(database, batch, command, facts, store)
        await cold.close()
        cold, capital, _, importer = await restore(cfg, owner, database[0])
        assert await cold.balance(cfg.binding) == expected['balance']
        assert await cold.budget(cfg.binding) == expected['budget']
        assert await capital.claims(cfg.binding) == claims
        assert (await importer.lookup(command.operation_id)).replayed
        assert await database[0].fetchval("SELECT to_jsonb(o) FROM life_financial.operations o WHERE account_id=$1 AND operation_id='native-prior-swap'", cfg.account_id) == original
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.swap_fill_facts WHERE account_id=$1', cfg.account_id) == 1
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.funding_cash_payments WHERE account_id=$1', cfg.account_id) == 1
        assert await database[0].fetchval('SELECT halted FROM life_financial.accounts WHERE account_id=$1', cfg.account_id)
        assert await store.frozen_legacy_applying_source(store.binding) == source
    finally:
        await financial.close()
        await store.close()
        if cold is not None:
            await cold.close()
