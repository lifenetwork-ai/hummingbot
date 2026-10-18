"""A completed inventory cycle is history, not another budget release."""
import copy
import json
from dataclasses import replace
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_legacy_applying import KEYS, setup
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_legacy_spot import recover_case

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError, encode, fingerprint
from hummingbot.strategy_v2.life_liquidity.storage.postgres.legacy_applying import funding_profile
from hummingbot.strategy_v2.life_liquidity.storage.postgres.settlement import PgInventorySettlementRepository
from hummingbot.strategy_v2.life_liquidity.storage.recovery import SpotFinancialRecoveryFacts
from hummingbot.strategy_v2.life_liquidity.storage.settlement import SettleSpotInventory, SpotSettlementPolicy
from hummingbot.strategy_v2.life_liquidity.storage.terminal import SpotTerminalFacts, TerminalSpot


async def setup_settled(database, tmp_path, monkeypatch, stage='wallet_after', expected=None, *, native_settlement=True, separate_fill_id=False):
    from hummingbot.strategy_v2.life_liquidity.storage import legacy_spot
    original_open = legacy_spot.open_legacy_spot

    async def open_with_settlement(*args, **kwargs):
        return await original_open(*args, **kwargs, settlement_policy=SpotSettlementPolicy(5000))

    async def settle_before_interruption(store, financial, token, owner, command):
        binding, c = financial.binding, database[0]
        fill = command.spot_fills[0]
        assert fill.side == 'SELL'
        # Actual pre-import native and File commands, not patched checkpoints
        # or balances inferred from the interrupted wallet.
        await financial.apply_fills(replace(fill, operation_id='native-prior-final-fill') if separate_fill_id else fill,
                                    cut=(await financial.snapshot(binding)).cut, owner=token)
        await store.apply_fills(fill, cut=(await store.snapshot(store.binding)).cut, owner=owner)
        view = await financial.snapshot(binding)
        facts = SpotTerminalFacts(binding, command.observed_at, 'CANCELED', fill.cumulative_base_life, D('2'), D('1'),
                                  fill.fills, view.balance.total_base_life, view.balance.total_quote_usdt, D('0'), D('0'),
                                  tuple(sorted(('previous-trade', *(f.trade_id for f in fill.fills)))), (), (), (), (), True, True, True)
        terminal = TerminalSpot('native-prior-final-terminal', fill.prepare_operation_id, fill.intent_id, fill.client_order_id,
                                fill.market, fill.side, fill.session_id, fill.epoch, fill.exchange_order_id, facts)
        await financial.confirm_terminal(terminal, cut=view.cut, owner=token)
        await store.confirm_terminal(replace(terminal, facts=replace(facts, binding=store.binding)),
                                     cut=(await store.snapshot(store.binding)).cut, owner=owner)
        settlement = SettleSpotInventory('native-prior-cycle', 'original-cycle', ('previous-intent', fill.intent_id))
        recovery = SpotFinancialRecoveryFacts(binding, command.observed_at, view.balance.total_base_life, view.balance.total_quote_usdt,
                                              D('0'), D('0'), facts.account_trade_ids, (), ('previouswire', fill.client_order_id), (), True, True, True)
        if native_settlement:
            await c.execute('INSERT INTO life_financial.spot_settlement_policies VALUES($1,$2,5000)',
                            financial._runtime.config.account_id, binding.policy_hash)
            await PgInventorySettlementRepository(financial).settle_inventory(
                settlement, facts=recovery, cut=(await financial.snapshot(binding)).cut, owner=token)
        await store.settle_inventory(settlement, facts=replace(recovery, binding=store.binding),
                                     cut=(await store.snapshot(store.binding)).cut, owner=owner)

    with monkeypatch.context() as patch:
        patch.setattr(legacy_spot, 'open_legacy_spot', open_with_settlement)
        result = await setup(database, tmp_path, monkeypatch, stage, spot_fill=('SELL', 'USDT'), prior_spot_order=True,
                             prior_terminal=True, file_result=expected, before_joint=settle_before_interruption)
    if separate_fill_id:
        values, batch, command, cut, facts, approval, importer, store, source = result
        # Select the independently retained native observation in the signed
        # target. The original File operation ID remains in its frozen capsule.
        command = replace(command, batch=replace(command.batch, spot_fills=tuple(
            replace(f, operation_id='native-prior-final-fill') if f.intent_id == 'intent1' else f
            for f in command.batch.spot_fills)))
        approval = KEYS.sign('operator', values[1].binding, 'IMPORT', command.json(), cut, facts,
                             issued_at=approval.issued_at, expires_at=approval.expires_at)
        result = values, batch, command, cut, facts, approval, importer, store, source
    return result


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['wallet_before', 'receipt'])
@pytest.mark.parametrize('separate_fill_id', [False, True])
async def test_completed_inventory_cycle_keeps_original_actuals_and_gross_loss_after_two_restarts(database, tmp_path, monkeypatch, stage, separate_fill_id):
    expected = {}
    prepared = await setup_settled(database, tmp_path, monkeypatch, stage, expected, separate_fill_id=separate_fill_id)
    funding_profile(prepared[-1], prepared[2])
    await recover_case(database, tmp_path, monkeypatch, stage, 'SELL', 'USDT', prepared=prepared, expected=expected,
                       prior_spot_order=True, book_native_prefix=True)


@pytest.mark.asyncio
async def test_settled_history_requires_exact_cost_proof_members_native_receipt_and_original_cut(database, tmp_path, monkeypatch):
    values, _, command, cut, _, _, _, store, source = await setup_settled(database, tmp_path, monkeypatch)
    financial, cfg = values[:2]
    c = database[0]
    try:
        basis = funding_profile(source, command)
        assert set(basis['spot_settlements'][0]['actuals']) == {'intent1', 'previous-intent'}

        async def valid(body=basis, version=cut.checkpoint.revision):
            return await c.fetchval('SELECT life_financial.legacy_settlement_prefix_valid($1,$2::jsonb,$3)',
                                    cfg.account_id, json.dumps(body), version)
        assert await valid()
        original = await c.fetchval("SELECT committed_version FROM life_financial.operations WHERE account_id=$1 AND operation_id='native-prior-cycle'", cfg.account_id)
        assert not await valid(version=original - 1)
        for body in ({k: v for k, v in basis.items() if k != 'spot_settlements'},
                     {**basis, 'spot_settlements': []}, {**basis, 'spot_settlements': basis['spot_settlements'] * 2},
                     {**basis, 'spot_settlements': None}):
            assert not await valid(body)
        for key in ('proof', 'actuals', 'committed_before', 'facts', 'command'):
            changed = copy.deepcopy(basis)
            row = changed['spot_settlements'][0]
            if key == 'proof':
                row[key] = 'f' * 64
            elif key in ('actuals', 'committed_before'):
                row[key]['intent1'] = '0'
            elif key == 'facts':
                row[key]['account_quote_usdt'] = '1000'
            else:
                row[key]['intent_ids'] = ['intent1']
            assert not await valid(changed), key
        for table, statement in (
            ('operations', "UPDATE life_financial.operations SET fingerprint=repeat('f',64) WHERE account_id=$1 AND operation_id='native-prior-cycle'"),
            ('operations', "UPDATE life_financial.operations SET receipt=jsonb_set(receipt,'{proof_digest}',to_jsonb(repeat('f',64))) WHERE account_id=$1 AND operation_id='native-prior-cycle'"),
            ('spot_inventory_settlements', "DELETE FROM life_financial.spot_inventory_settlements WHERE account_id=$1"),
            ('spot_inventory_settlements', "UPDATE life_financial.spot_inventory_settlements SET evidence=jsonb_set(evidence,'{actuals,intent1}','\"0\"') WHERE account_id=$1"),
        ):
            tx = c.transaction()
            await tx.start()
            try:
                await c.execute(f'ALTER TABLE life_financial.{table} DISABLE TRIGGER USER')
                await c.execute(statement, cfg.account_id)
                assert not await valid(), statement
            finally:
                await tx.rollback()
        for fault in ('missing', 'state', 'fingerprint', 'proof', 'actuals', 'held', 'members'):
            envelope = json.loads(source.checkpoint_bytes)
            rows = envelope['state']['resources']['settlement_operations']
            row = rows['native-prior-cycle']
            if fault == 'missing':
                rows.clear()
            elif fault == 'state':
                row['state'] = 'SETTLING'
            elif fault in ('fingerprint', 'proof'):
                row[fault] = 'f' * 64
            elif fault in ('actuals', 'held'):
                row['actuals' if fault == 'actuals' else 'committed_before']['intent1'] = '0'
            else:
                row['command']['intent_ids'] = ['intent1']
            changed = replace(source, checkpoint_bytes=encode(envelope), cut=replace(source.cut,
                              checkpoint=replace(source.cut.checkpoint, state_digest=fingerprint(envelope['state']))))
            with pytest.raises(StorageError):
                funding_profile(changed, command)
        assert await valid()
    finally:
        await store.close()
        await financial.close()


@pytest.mark.asyncio
async def test_file_cycle_without_original_native_settlement_cannot_refund_or_import(database, tmp_path, monkeypatch):
    values, _, command, cut, facts, approval, importer, store, _ = await setup_settled(
        database, tmp_path, monkeypatch, native_settlement=False)
    financial, cfg = values[:2]
    try:
        before, claims = await financial.snapshot(cfg.binding), await values[5].claims(cfg.binding)
        with pytest.raises(StorageError):
            await importer.import_pending(command, source_store=store, approval=approval, cut=cut, facts=facts)
        after = await financial.snapshot(cfg.binding)
        assert (after.balance, after.budget, after.cut) == (before.balance, before.budget, before.cut)
        assert await values[5].claims(cfg.binding) == claims
        assert not await database[0].fetchval('SELECT EXISTS(SELECT 1 FROM life_financial.legacy_applying_imports WHERE account_id=$1)', cfg.account_id)
    finally:
        await store.close()
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['receipt', 'member'])
async def test_committed_cycle_corruption_denies_cold_import_proof_without_refunding_cost(database, tmp_path, monkeypatch, fault):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_legacy_core_cashflows import restore
    values, _, command, cut, facts, approval, importer, store, _ = await setup_settled(database, tmp_path, monkeypatch)
    financial, cfg, owner = values[:3]
    c, cold = database[0], None
    try:
        before, claims = await financial.snapshot(cfg.binding), await values[5].claims(cfg.binding)
        assert (await importer.import_pending(command, source_store=store, approval=approval, cut=cut, facts=facts)).state == 'RECOVERY_REQUIRED'
        table = 'operations' if fault == 'receipt' else 'spot_inventory_members'
        await c.execute(f'ALTER TABLE life_financial.{table} DISABLE TRIGGER USER')
        if fault == 'receipt':
            await c.execute("UPDATE life_financial.operations SET receipt=jsonb_set(receipt,'{proof_digest}',to_jsonb(repeat('f',64))) WHERE account_id=$1 AND operation_id='native-prior-cycle'", cfg.account_id)
        else:
            await c.execute("UPDATE life_financial.spot_inventory_members SET actual_usdt=0 WHERE account_id=$1 AND intent_id='intent1'", cfg.account_id)
        await c.execute(f'ALTER TABLE life_financial.{table} ENABLE TRIGGER USER')
        await financial.close()
        cold, capital, _, importer = await restore(cfg, owner, c)
        view = await cold.snapshot(cfg.binding)
        assert view.recovery_required and (view.balance, view.budget) == (before.balance, before.budget)
        with pytest.raises(StorageError):
            await importer.lookup(command.operation_id)
        assert await capital.claims(cfg.binding) == claims
        assert await c.fetchval('SELECT halted FROM life_financial.accounts WHERE account_id=$1', cfg.account_id)
    finally:
        await store.close()
        await financial.close()
        if cold is not None:
            await cold.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('database', [66], indirect=True)
async def test_actual_66_settled_cycle_upgrades_without_resetting_original_actuals_or_money(database, tmp_path, monkeypatch):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_legacy_core_cashflows import restore

    from hummingbot.strategy_v2.life_liquidity.storage.postgres import runtime
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.migrations import migrate
    load, expected = runtime.load_migrations, {}
    with monkeypatch.context() as patch:
        patch.setattr(runtime, 'load_migrations', lambda *a, **k: tuple(m for m in load(*a, **k) if m.version <= 66))
        prepared = await setup_settled(database, tmp_path, monkeypatch, 'receipt', expected, separate_fill_id=True)
    values, _, command, cut, facts, approval, _, store, source = prepared
    financial, cfg, owner = values[:3]
    c, cold = database[0], None
    try:
        before = await financial.snapshot(cfg.binding)
        await financial.close()
        assert (await migrate(c, action='up'))['applied'] == ['000067_legacy_settlement_prefix.sql', '000068_legacy_abort_nav_history.sql', '000069_legacy_joint_spot_claims.sql']
        cold, capital, batch, importer = await restore(cfg, owner, c)
        after = await cold.snapshot(cfg.binding)
        assert (after.balance, after.budget, after.cut) == (before.balance, before.budget, before.cut)
        restored = list(values)
        restored[0], restored[5] = cold, capital
        await recover_case(database, tmp_path, monkeypatch, 'receipt', 'SELL', 'USDT',
                           prepared=(tuple(restored), batch, command, cut, facts, approval, importer, store, source),
                           expected=expected, prior_spot_order=True, book_native_prefix=True)
    finally:
        await store.close()
        await financial.close()
        if cold is not None:
            await cold.close()


@pytest.mark.asyncio
async def test_new_fill_alias_cannot_reopen_terminal_cycle_or_refund_original_cost(database, tmp_path, monkeypatch):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_legacy_core_cashflows import restore
    expected = {}
    prepared = await setup_settled(database, tmp_path, monkeypatch, 'receipt', expected, separate_fill_id=True)
    values, batch, command, cut, facts, approval, importer, store, source = prepared
    financial, cfg, owner = values[:3]
    c, cold = database[0], None
    try:
        before, claims = await financial.snapshot(cfg.binding), await values[5].claims(cfg.binding)
        command = replace(command, batch=replace(command.batch, spot_fills=tuple(
            replace(f, operation_id='invented-terminal-alias') if f.intent_id == 'intent1' else f
            for f in command.batch.spot_fills)))
        approval = KEYS.sign('operator', cfg.binding, 'IMPORT', command.json(), cut, facts,
                             issued_at=approval.issued_at, expires_at=approval.expires_at)
        with pytest.raises(StorageError, match='PG_JOINT_REPAIR_REPLACEMENT_REJECTED'):
            await recover_case(database, tmp_path, monkeypatch, 'receipt', 'SELL', 'USDT',
                               prepared=(values, batch, command, cut, facts, approval, importer, store, source),
                               expected=expected, prior_spot_order=True, book_native_prefix=True)
        cold, capital, _, _ = await restore(cfg, owner, c)
        view = await cold.snapshot(cfg.binding)
        assert view.recovery_required and (view.balance, view.budget) == (before.balance, before.budget)
        assert await capital.claims(cfg.binding) == claims
        assert not await c.fetchval("SELECT EXISTS(SELECT 1 FROM life_financial.operations WHERE account_id=$1 AND operation_id='invented-terminal-alias')", cfg.account_id)
        assert await c.fetchval('SELECT halted FROM life_financial.accounts WHERE account_id=$1', cfg.account_id)
    finally:
        await store.close()
        await financial.close()
        if cold is not None:
            await cold.close()
