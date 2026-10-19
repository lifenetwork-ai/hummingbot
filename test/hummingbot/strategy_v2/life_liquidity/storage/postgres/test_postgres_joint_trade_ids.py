"""Original cross-product trade-ID collisions retain distinct economic leaves."""
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_legacy_core_cashflows import restore
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_legacy_joint import apply_recovery, setup

import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['outer_before_cash', 'wallet_after', 'cash_done'])
@pytest.mark.parametrize('spot_fee', ['USDT', 'LIFE'])
@pytest.mark.parametrize('book_native_swap', [False, True])
async def test_retired_outer_recovery_keeps_spot_and_swap_with_same_trade_id_independent_through_cold_repair(
        database, tmp_path, monkeypatch, stage, spot_fee, book_native_swap):
    expected = {}
    values, batch, command, cut, facts, approval, importer, store, source = await setup(
        database, tmp_path, monkeypatch, stage, funding=D('-.1'), spot_side='BUY', spot_fee_currency=spot_fee,
        swap_trade_id='spottrade1', book_native_swap=book_native_swap, file_result=expected)
    financial, cfg, owner = values[:3]
    cold = None
    try:
        assert facts.account_trade_ids == ('spottrade1',)
        assert {f.trade_id for v in command.batch.spot_fills for f in v.fills} == {'spottrade1'}
        assert {f.trade_id for v in command.batch.swap_fills for f in v.fills} == {'spottrade1'}
        before = await financial.snapshot(cfg.binding)
        claims = await values[5].claims(cfg.binding)
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
        rows = await database[0].fetch('SELECT product,trade_id FROM life_financial.fills ORDER BY product,trade_id')
        assert [(v['product'], v['trade_id']) for v in rows] == [('SPOT', 'spottrade1'), ('SWAP', 'spottrade1')]
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.funding_cash_payments') == 1
        assert await database[0].fetchval('SELECT halted FROM life_financial.accounts WHERE account_id=$1', cfg.account_id)
        assert await store.frozen_legacy_applying_source(store.binding) == source
    finally:
        await financial.close()
        await store.close()
        if cold is not None:
            await cold.close()
