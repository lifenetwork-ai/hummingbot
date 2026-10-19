"""Unsent original SPOT preparations retain holds during funding recovery."""
import json
from dataclasses import replace
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_legacy_applying import setup
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_legacy_spot import recover_case

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
from hummingbot.strategy_v2.life_liquidity.storage.postgres.legacy_applying import funding_profile


@pytest.mark.asyncio
@pytest.mark.parametrize('side', ['BUY', 'SELL'])
@pytest.mark.parametrize('stage', ['wallet_before', 'wallet_after', 'receipt'])
async def test_unsent_original_spot_holds_survive_funding_import_and_two_cold_restores(database, tmp_path, monkeypatch, side, stage):
    await recover_case(database, tmp_path, monkeypatch, stage, side, 'USDT', spot_prepared_only=True)


@pytest.mark.asyncio
async def test_original_unsent_native_history_has_independent_cut_proof(database, tmp_path, monkeypatch):
    values, _, command, cut, _, _, _, store, source = await setup(
        database, tmp_path, monkeypatch, spot_fill=('BUY', 'USDT'), spot_prepared_only=True)
    financial, cfg = values[:2]
    try:
        basis = funding_profile(source, command)
        assert basis['spot_prepared_only_ids'] == ['operation1']
        c = database[0]

        async def valid(raw):
            return await c.fetchval('SELECT life_financial.legacy_spot_basis_valid($1,$2::jsonb,$3)',
                                    cfg.account_id, json.dumps(raw), cut.checkpoint.revision)
        assert await valid(basis)
        for ids in ([], ['other'], ['operation1', 'operation1'], [1], None, 'operation1'):
            assert not await valid({**basis, 'spot_prepared_only_ids': ids})
        assert not await valid({k: v for k, v in basis.items() if k != 'spot_prepared_only_ids'})
        for statement in (
            "UPDATE life_financial.operations SET fingerprint=repeat('f',64) WHERE account_id=$1 AND operation_id='operation1'",
            "UPDATE life_financial.operations SET receipt=jsonb_set(receipt,'{version}','0') WHERE account_id=$1 AND operation_id='operation1'",
            "DELETE FROM life_financial.operations WHERE account_id=$1 AND operation_id='operation1'",
        ):
            tx = c.transaction()
            await tx.start()
            try:
                await c.execute('ALTER TABLE life_financial.operations DISABLE TRIGGER USER')
                await c.execute(statement, cfg.account_id)
                assert not await valid(basis)
            finally:
                await tx.rollback()
        assert await valid(basis)
    finally:
        await store.close()
        await financial.close()


@pytest.mark.asyncio
async def test_already_armed_native_order_cannot_be_imported_as_unsent_even_with_matching_file_holds(database, tmp_path, monkeypatch):
    values, _, command, cut, facts, approval, importer, store, source = await setup(
        database, tmp_path, monkeypatch, spot_fill=('BUY', 'USDT'), spot_prepared_only=True, native_arm_prepared_only=True)
    financial, cfg = values[:2]
    try:
        basis = funding_profile(source, command)
        c = database[0]
        original = basis['spot_preparations'][0]
        # Historical proof uses the original cut, not today's mutable state.
        version = await c.fetchval("SELECT committed_version FROM life_financial.operations WHERE account_id=$1 AND operation_id='operation1'", cfg.account_id)
        assert await c.fetchval('SELECT life_financial.legacy_spot_unsent_valid($1,$2::jsonb,$3)', cfg.account_id, json.dumps(original), version)
        assert not await c.fetchval('SELECT life_financial.legacy_spot_unsent_valid($1,$2::jsonb,$3)', cfg.account_id, json.dumps(original), cut.checkpoint.revision)
        before = await financial.snapshot(cfg.binding)
        with pytest.raises(StorageError, match='PG_LEGACY_APPLYING_ORIGINAL_SPOT_BASIS_REQUIRED'):
            await importer.import_pending(command, source_store=store, approval=approval, cut=cut, facts=facts)
        after = await financial.snapshot(cfg.binding)
        assert (after.balance, after.budget, after.cut) == (before.balance, before.budget, before.cut)
        assert not await c.fetchval('SELECT EXISTS(SELECT 1 FROM life_financial.legacy_applying_imports WHERE account_id=$1)', cfg.account_id)
        assert (await financial.attempt('intent1', cfg.binding)).state.value == 'SEND_UNKNOWN'
    finally:
        await store.close()
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('database', [61], indirect=True)
async def test_actual_61_unsent_preparation_upgrades_without_new_arm_or_hold_changes(database, tmp_path, monkeypatch):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_legacy_core_cashflows import restore

    from hummingbot.strategy_v2.life_liquidity.storage.postgres import runtime
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.migrations import migrate
    load = runtime.load_migrations
    expected = {}
    with monkeypatch.context() as patch:
        patch.setattr(runtime, 'load_migrations', lambda *a, **k: tuple(m for m in load(*a, **k) if m.version <= 61))
        values, batch, command, cut, facts, approval, importer, store, source = await setup(
            database, tmp_path, monkeypatch, spot_fill=('SELL', 'USDT'), spot_prepared_only=True, file_result=expected)
    financial, cfg, owner = values[:3]
    cold = None
    try:
        before = await financial.snapshot(cfg.binding)
        await financial.close()
        assert (await migrate(database[0], action='up'))['applied'] == ['000062_legacy_unsent_spot.sql', '000063_legacy_spot_ack_prefix.sql', '000064_legacy_repaired_parent_prefix.sql', '000065_legacy_cancel_prefix.sql', '000066_legacy_terminal_prefix.sql', '000067_legacy_settlement_prefix.sql', '000068_legacy_abort_nav_history.sql', '000069_legacy_joint_spot_claims.sql', '000070_repair_view_reuse.sql', '000071_required_ack_completion.sql', '000072_unfilled_swap_prefix.sql', '000073_joint_provider_routes.sql', '000074_joint_source_deadline.sql', '000075_joint_trade_id_sets.sql', '000076_signed_native_control.sql', '000077_reconciled_session_successors.sql', '000078_native_safety.sql', '000079_native_markout_gate.sql', '000080_native_stress.sql', '000081_native_execution_modes.sql']
        cold, capital, batch, importer = await restore(cfg, owner, database[0])
        upgraded = await cold.snapshot(cfg.binding)
        assert (upgraded.balance, upgraded.budget, upgraded.cut) == (before.balance, before.budget, before.cut)
        restored = list(values)
        restored[0], restored[5] = cold, capital
        prepared = (tuple(restored), batch, command, cut, facts, approval, importer, store, source)
        await recover_case(database, tmp_path, monkeypatch, 'wallet_after', 'SELL', 'USDT', prepared=prepared,
                           expected=expected, spot_prepared_only=True)
    finally:
        await store.close()
        await financial.close()
        if cold is not None:
            await cold.close()


@pytest.mark.asyncio
async def test_file_unsent_claim_cannot_hide_wal_send_cancel_or_runner_acceptance(database, tmp_path, monkeypatch):
    from types import SimpleNamespace
    values, _, command, _, _, _, _, store, source = await setup(
        database, tmp_path, monkeypatch, spot_fill=('BUY', 'USDT'), spot_prepared_only=True)
    try:
        funding_profile(source, command)
        for field, value in (('state', 'SEND_UNKNOWN'), ('exchange_order_id', '123'), ('client_order_id', 'other'),
                             ('cancel_requested', True), ('cancel_attempts', 1), ('exchange_terminal_observed', True),
                             ('slot_level', 1), ('reservation_id', 'other')):
            images = dict(source.journal_images)
            wal = json.loads(images['wal'])
            wal['records']['intent1'][field] = value
            images['wal'] = json.dumps(wal).encode()
            # Read-only component mutation, not a signed/rehashed import.
            capsule = SimpleNamespace(**{name: getattr(source, name) for name in (
                'checkpoint_bytes', 'original_operations', 'cut', 'ancillary_images', 'core_pending_operation_ids', 'facts')},
                journal_images=tuple(images.items()))
            with pytest.raises(StorageError, match='ORIGINAL_BASIS_REQUIRED'):
                funding_profile(capsule, command)
        for name in ('accepted_wire_ids', 'runner_wire_ids'):
            with pytest.raises(StorageError, match='ORIGINAL_BASIS_REQUIRED'):
                funding_profile(replace(source, facts=replace(source.facts, **{name: ('wire1',)})), command)
    finally:
        await store.close()
        await values[0].close()


@pytest.mark.asyncio
async def test_wire_bound_quote_arm_cannot_be_disguised_as_an_unsent_original(database):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_quote_arm import prepared
    financial, cfg, owner, command = await prepared(database)
    c = database[0]
    try:
        original = await c.fetchval("SELECT command->'command' FROM life_financial.operations WHERE account_id=$1 AND operation_id='operation1'", cfg.account_id)
        before = (await financial.snapshot(cfg.binding)).cut
        assert await c.fetchval('SELECT life_financial.legacy_spot_unsent_valid($1,$2::jsonb,$3)', cfg.account_id, original, before.checkpoint.revision)
        await financial.arm_quote_action(command, cut=before, owner=owner)
        after = (await financial.snapshot(cfg.binding)).cut
        assert not await c.fetchval('SELECT life_financial.legacy_spot_unsent_valid($1,$2::jsonb,$3)', cfg.account_id, original, after.checkpoint.revision)
        assert await c.fetchval('SELECT life_financial.legacy_spot_unsent_valid($1,$2::jsonb,$3)', cfg.account_id, original, before.checkpoint.revision)
    finally:
        await financial.close()
