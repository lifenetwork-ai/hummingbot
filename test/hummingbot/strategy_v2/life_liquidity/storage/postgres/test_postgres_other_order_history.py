"""A completed earlier order remains history when the pending command omits it."""
import copy
import json
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_legacy_applying import setup
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_legacy_spot import recover_case

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
from hummingbot.strategy_v2.life_liquidity.storage.financial import AckSpot
from hummingbot.strategy_v2.life_liquidity.storage.postgres.legacy_applying import funding_profile


@pytest.mark.asyncio
@pytest.mark.parametrize('side', ['BUY', 'SELL'])
@pytest.mark.parametrize('stage', ['wallet_before', 'receipt'])
@pytest.mark.parametrize('history', ['none', 'before', 'after', 'mixed'])
async def test_prior_other_order_survives_funding_recovery_with_original_receipts_and_holds(database, tmp_path, monkeypatch, stage, side, history):
    await recover_case(database, tmp_path, monkeypatch, stage, side, 'USDT', prior_spot_order=True,
                       prior_funding=() if history == 'none' else (D('-.03'), D('.02')),
                       prior_after_accounting=history in ('after', 'mixed'), prior_embedded_spot=history == 'mixed')


@pytest.mark.asyncio
async def test_original_ack_prefix_requires_order_scope_zero_money_hash_and_receipt(database, tmp_path, monkeypatch):
    values, _, command, cut, _, _, _, store, source = await setup(
        database, tmp_path, monkeypatch, spot_fill=('BUY', 'USDT'), prior_spot_order=True)
    financial, cfg = values[:2]
    c = database[0]
    try:
        basis, target = funding_profile(source, command), command.batch.json()

        async def valid(body):
            return await c.fetchval('SELECT life_financial.legacy_ack_prefix_valid($1,$2::jsonb,$3::jsonb,$4)',
                                    cfg.account_id, json.dumps(basis), json.dumps(body), cut.checkpoint.revision)
        assert await valid(target)
        # Omit both independent File representations to remove ACK order scope.
        missing = {**basis, 'spot_acknowledgements': []}
        no_previous = copy.deepcopy(target)
        no_previous['spot_fills'] = [v for v in no_previous['spot_fills'] if v['intent_id'] != 'previous-intent']
        assert not await c.fetchval('SELECT life_financial.legacy_ack_prefix_valid($1,$2::jsonb,$3::jsonb,$4)',
                                    cfg.account_id, json.dumps(missing), json.dumps(no_previous), cut.checkpoint.revision)
        for table, statement in (
            ('operations', "UPDATE life_financial.operations SET fingerprint=repeat('f',64) WHERE account_id=$1 AND operation_id='previous-ack'"),
            ('operations', "UPDATE life_financial.operations SET receipt=jsonb_set(receipt,'{version}','0') WHERE account_id=$1 AND operation_id='previous-ack'"),
            ('operations', "DELETE FROM life_financial.operations WHERE account_id=$1 AND operation_id='previous-ack'"),
            ('financial_events', "UPDATE life_financial.financial_events SET source_hash=repeat('f',64) WHERE account_id=$1 AND kind='ACK'"),
            ('financial_events', "UPDATE life_financial.financial_events SET quote_delta=1 WHERE account_id=$1 AND kind='ACK'"),
            ('financial_events', "UPDATE life_financial.financial_events SET event_id='ack:other' WHERE account_id=$1 AND kind='ACK'"),
            ('financial_events', "DELETE FROM life_financial.financial_events WHERE account_id=$1 AND kind='ACK'"),
        ):
            tx = c.transaction()
            await tx.start()
            try:
                await c.execute(f'ALTER TABLE life_financial.{table} DISABLE TRIGGER USER')
                await c.execute(statement, cfg.account_id)
                assert not await valid(target), statement
            finally:
                await tx.rollback()
        assert await valid(target)
        # A later received duplicate can carry an earlier observation time.
        # The first ACK event and every original receipt remain unchanged.
        from datetime import timedelta
        raw = json.loads(await c.fetchval("SELECT command->'command' FROM life_financial.operations WHERE account_id=$1 AND operation_id='previous-ack'", cfg.account_id))
        earlier = await c.fetchval("SELECT prepared_at FROM life_financial.spot_preparations WHERE account_id=$1 AND operation_id='previous-prepare'", cfg.account_id)
        original_receipt = await c.fetchval("SELECT receipt FROM life_financial.operations WHERE account_id=$1 AND operation_id='previous-ack'", cfg.account_id)
        late = AckSpot(**{**raw, 'operation_id': 'previous-ack-late', 'observed_at': earlier + timedelta(microseconds=1)})
        before = await financial.snapshot(cfg.binding)
        await financial.acknowledge(late, cut=before.cut, owner=values[2])
        after = await financial.snapshot(cfg.binding)
        assert (after.balance, after.budget) == (before.balance, before.budget)
        assert await c.fetchval("SELECT receipt FROM life_financial.operations WHERE account_id=$1 AND operation_id='previous-ack'", cfg.account_id) == original_receipt
        assert await c.fetchval('SELECT life_financial.legacy_ack_prefix_valid($1,$2::jsonb,$3::jsonb,$4)',
                                cfg.account_id, json.dumps(basis), json.dumps(target), after.cut.checkpoint.revision)
        assert await valid(target)
    finally:
        await store.close()
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('database', [62], indirect=True)
async def test_actual_62_original_order_ack_upgrades_without_rewriting_receipts_or_money(database, tmp_path, monkeypatch):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_legacy_core_cashflows import restore

    from hummingbot.strategy_v2.life_liquidity.storage.postgres import runtime
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.migrations import migrate
    load = runtime.load_migrations
    expected = {}
    with monkeypatch.context() as patch:
        patch.setattr(runtime, 'load_migrations', lambda *a, **k: tuple(m for m in load(*a, **k) if m.version <= 62))
        prepared = await setup(database, tmp_path, monkeypatch, spot_fill=('SELL', 'USDT'), prior_spot_order=True, file_result=expected)
    values, _, command, cut, facts, approval, _, store, source = prepared
    financial, cfg, owner = values[:3]
    c, cold = database[0], None
    try:
        before = await financial.snapshot(cfg.binding)
        rows = await c.fetchval("SELECT jsonb_agg(to_jsonb(o) ORDER BY operation_id) FROM life_financial.operations o WHERE account_id=$1", cfg.account_id)
        await financial.close()
        assert (await migrate(c, action='up'))['applied'] == ['000063_legacy_spot_ack_prefix.sql', '000064_legacy_repaired_parent_prefix.sql', '000065_legacy_cancel_prefix.sql', '000066_legacy_terminal_prefix.sql', '000067_legacy_settlement_prefix.sql', '000068_legacy_abort_nav_history.sql', '000069_legacy_joint_spot_claims.sql', '000070_repair_view_reuse.sql', '000071_required_ack_completion.sql', '000072_unfilled_swap_prefix.sql', '000073_joint_provider_routes.sql', '000074_joint_source_deadline.sql', '000075_joint_trade_id_sets.sql', '000076_signed_native_control.sql', '000077_reconciled_session_successors.sql', '000078_native_safety.sql', '000079_native_markout_gate.sql', '000080_native_stress.sql']
        cold, capital, batch, importer = await restore(cfg, owner, c)
        after = await cold.snapshot(cfg.binding)
        assert (after.balance, after.budget, after.cut) == (before.balance, before.budget, before.cut)
        assert await c.fetchval("SELECT jsonb_agg(to_jsonb(o) ORDER BY operation_id) FROM life_financial.operations o WHERE account_id=$1", cfg.account_id) == rows
        restored = list(values)
        restored[0], restored[5] = cold, capital
        await recover_case(database, tmp_path, monkeypatch, 'wallet_after', 'SELL', 'USDT',
                           prepared=(tuple(restored), batch, command, cut, facts, approval, importer, store, source),
                           expected=expected, prior_spot_order=True)
    finally:
        await store.close()
        await financial.close()
        if cold is not None:
            await cold.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('database', [62], indirect=True)
async def test_original_62_import_without_ack_projection_still_verifies_after_upgrade(database, tmp_path, monkeypatch):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_legacy_core_cashflows import restore

    from hummingbot.strategy_v2.life_liquidity.storage.postgres import legacy_spot_basis, runtime
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.migrations import migrate
    load, original = runtime.load_migrations, legacy_spot_basis.spot_basis

    def previous_basis(*args, **kwargs):
        result = original(*args, **kwargs)
        result[0].pop('spot_acknowledgements', None)
        return result
    store = financial = cold = None
    try:
        with monkeypatch.context() as patch:
            patch.setattr(runtime, 'load_migrations', lambda *a, **k: tuple(m for m in load(*a, **k) if m.version <= 62))
            patch.setattr(legacy_spot_basis, 'spot_basis', previous_basis)
            values, _, command, cut, facts, approval, importer, store, source = await setup(
                database, tmp_path, monkeypatch, spot_fill=('BUY', 'USDT'), file_ack_only=True)
            financial, cfg, owner = values[:3]
            assert (await importer.import_pending(command, source_store=store, approval=approval, cut=cut, facts=facts)).state == 'RECOVERY_REQUIRED'
            body = await database[0].fetchval('SELECT body FROM life_financial.legacy_applying_imports WHERE account_id=$1', cfg.account_id)
            assert 'spot_acknowledgements' not in json.loads(body)['source_basis']
            before = await financial.snapshot(cfg.binding)
            await financial.close()
        assert (await migrate(database[0], action='up'))['applied'] == ['000063_legacy_spot_ack_prefix.sql', '000064_legacy_repaired_parent_prefix.sql', '000065_legacy_cancel_prefix.sql', '000066_legacy_terminal_prefix.sql', '000067_legacy_settlement_prefix.sql', '000068_legacy_abort_nav_history.sql', '000069_legacy_joint_spot_claims.sql', '000070_repair_view_reuse.sql', '000071_required_ack_completion.sql', '000072_unfilled_swap_prefix.sql', '000073_joint_provider_routes.sql', '000074_joint_source_deadline.sql', '000075_joint_trade_id_sets.sql', '000076_signed_native_control.sql', '000077_reconciled_session_successors.sql', '000078_native_safety.sql', '000079_native_markout_gate.sql', '000080_native_stress.sql']
        cold, _, _, importer = await restore(cfg, owner, database[0])
        assert (await importer.lookup(command.operation_id)).replayed
        after = await cold.snapshot(cfg.binding)
        assert (after.balance, after.budget, after.cut) == (before.balance, before.budget, before.cut)
        assert await database[0].fetchval('SELECT body FROM life_financial.legacy_applying_imports WHERE account_id=$1', cfg.account_id) == body
        assert (await cold.attempt('intent1', cfg.binding)).state.value == 'SEND_UNKNOWN'
        assert await store.frozen_legacy_applying_source(store.binding) == source
    finally:
        if store is not None:
            await store.close()
        if financial is not None:
            await financial.close()
        if cold is not None:
            await cold.close()


@pytest.mark.asyncio
async def test_committed_original_ack_corruption_denies_cold_source_proof_and_keeps_money_and_claims(database, tmp_path, monkeypatch):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_legacy_core_cashflows import restore
    values, _, command, cut, facts, approval, importer, store, _ = await setup(
        database, tmp_path, monkeypatch, spot_fill=('BUY', 'USDT'), prior_spot_order=True)
    financial, cfg, owner = values[:3]
    c, cold = database[0], None
    try:
        before, claims = await financial.balance(cfg.binding), await values[5].claims(cfg.binding)
        assert (await importer.import_pending(command, source_store=store, approval=approval, cut=cut, facts=facts)).state == 'RECOVERY_REQUIRED'
        await c.execute('ALTER TABLE life_financial.operations DISABLE TRIGGER USER')
        await c.execute("UPDATE life_financial.operations SET receipt=jsonb_set(receipt,'{version}','0') WHERE account_id=$1 AND operation_id='previous-ack'", cfg.account_id)
        await c.execute('ALTER TABLE life_financial.operations ENABLE TRIGGER USER')
        await financial.close()
        cold, capital, _, importer = await restore(cfg, owner, c)
        view = await cold.snapshot(cfg.binding)
        assert view.recovery_required and view.balance == before
        with pytest.raises(StorageError, match='PG_LEGACY_APPLYING_UNVERIFIED'):
            await importer.lookup(command.operation_id)
        assert await capital.claims(cfg.binding) == claims
        assert await c.fetchval('SELECT halted FROM life_financial.accounts WHERE account_id=$1', cfg.account_id)
        assert await c.fetchval('SELECT count(*) FROM life_financial.funding_cash_payments WHERE account_id=$1', cfg.account_id) == 0
    finally:
        await store.close()
        await financial.close()
        if cold is not None:
            await cold.close()
