"""Original unsent aborts and NAV history compose with monetary recovery."""
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_legacy_applying import setup
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_legacy_spot import recover_case

import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize('side', ['BUY', 'SELL'])
@pytest.mark.parametrize('stage', ['wallet_before', 'receipt'])
@pytest.mark.parametrize('aborted,prices', [(True, ()), (False, (D('1.2'), D('.8'))), (True, (D('1.2'), D('.8')))])
async def test_original_abort_nav_histories_retain_costs_and_highwater_through_two_restarts(database, tmp_path, monkeypatch, side, stage, aborted, prices):
    expected = {}
    prepared = await setup(database, tmp_path, monkeypatch, stage, spot_fill=(side, 'USDT'), file_result=expected,
                           spot_aborted_only=aborted, nav_prices=prices)
    values = prepared[0]
    financial, cfg = values[:2]
    c = database[0]
    original_nav = await c.fetch('SELECT * FROM life_financial.spot_nav_observations WHERE account_id=$1 ORDER BY sequence', cfg.account_id)
    if prices:
        assert [row['highwater'] for row in original_nav] == [D('16'), D('16')]
        assert not (await financial.capital(cfg.binding)).current
    await recover_case(database, tmp_path, monkeypatch, stage, side, 'USDT', prepared=prepared, expected=expected,
                       spot_aborted_only=aborted)
    assert original_nav == await c.fetch('SELECT * FROM life_financial.spot_nav_observations WHERE account_id=$1 ORDER BY sequence', cfg.account_id)


@pytest.mark.asyncio
@pytest.mark.parametrize('profile', ['abort', 'nav'])
async def test_missing_independent_original_lifecycle_history_never_imports_money(database, tmp_path, monkeypatch, profile):
    from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
    values, _, command, cut, facts, approval, importer, store, _ = await setup(
        database, tmp_path, monkeypatch, spot_fill=('BUY', 'USDT'),
        spot_aborted_only=profile == 'abort', native_abort=False,
        nav_prices=(D('1.2'), D('.8')) if profile == 'nav' else (), native_nav=False)
    financial, cfg = values[:2]
    try:
        before, claims = await financial.snapshot(cfg.binding), await values[5].claims(cfg.binding)
        with pytest.raises(StorageError):
            await importer.import_pending(command, source_store=store, approval=approval, cut=cut, facts=facts)
        after = await financial.snapshot(cfg.binding)
        assert (after.cut, after.balance, after.budget) == (before.cut, before.balance, before.budget)
        assert await values[5].claims(cfg.binding) == claims
        assert not await database[0].fetchval('SELECT EXISTS(SELECT 1 FROM life_financial.legacy_applying_imports WHERE account_id=$1)', cfg.account_id)
    finally:
        await store.close()
        await financial.close()


@pytest.mark.asyncio
async def test_lifecycle_prefix_validates_original_cut_native_money_and_file_request(database, tmp_path, monkeypatch):
    import copy
    import json
    from dataclasses import replace

    from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError, encode, fingerprint
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.legacy_applying import funding_profile
    values, _, command, cut, _, _, _, store, source = await setup(
        database, tmp_path, monkeypatch, spot_fill=('BUY', 'USDT'), spot_aborted_only=True, nav_prices=(D('1.2'), D('.8')))
    financial, cfg = values[:2]
    c = database[0]
    try:
        basis = funding_profile(source, command)
        for profile, field, table, operation in (
            ('abort', 'spot_aborts', 'spot_lifecycle_events', 'native-prior-abort'),
            ('nav', 'spot_navs', 'spot_nav_observations', 'native-prior-nav-1'),
        ):
            async def valid(body=basis, version=cut.checkpoint.revision):
                return await c.fetchval(f'SELECT life_financial.legacy_{profile}_prefix_valid($1,$2::jsonb,$3)',
                                        cfg.account_id, json.dumps(body), version)
            assert await valid()
            original_version = await c.fetchval('SELECT committed_version FROM life_financial.operations WHERE account_id=$1 AND operation_id=$2', cfg.account_id, operation)
            assert not await valid(version=original_version - 1)
            for changed in (None, [], {}, {k: v for k, v in basis.items() if k != field}, {**basis, field: []},
                            {**basis, field: basis[field] * 2}, {**basis, field: None}):
                assert not await valid(changed)
            changed = copy.deepcopy(basis)
            target = changed[field][0] if profile == 'abort' else changed[field][0]['command']
            target['operation_id'] = 'invented'
            assert not await valid(changed)
            for fault in ('receipt', 'fingerprint', 'missing', 'measurement' if profile == 'nav' else 'state'):
                tx = c.transaction()
                await tx.start()
                try:
                    name = 'operations' if fault in ('receipt', 'fingerprint') else table
                    await c.execute(f'ALTER TABLE life_financial.{name} DISABLE TRIGGER USER')
                    if fault == 'receipt':
                        await c.execute("UPDATE life_financial.operations SET receipt=jsonb_set(receipt,'{version}','0') WHERE account_id=$1 AND operation_id=$2", cfg.account_id, operation)
                    elif fault == 'fingerprint':
                        await c.execute("UPDATE life_financial.operations SET fingerprint=repeat('f',64) WHERE account_id=$1 AND operation_id=$2", cfg.account_id, operation)
                    elif fault == 'missing':
                        await c.execute(f'DELETE FROM life_financial.{table} WHERE account_id=$1 AND operation_id=$2', cfg.account_id, operation)
                    elif profile == 'nav':
                        await c.execute("UPDATE life_financial.spot_nav_observations SET money_digest=repeat('f',64) WHERE account_id=$1 AND operation_id=$2", cfg.account_id, operation)
                    else:
                        await c.execute("UPDATE life_financial.spot_lifecycle_events SET result_state='ACKED' WHERE account_id=$1 AND operation_id=$2", cfg.account_id, operation)
                    assert not await valid(), fault
                finally:
                    await tx.rollback()
        for category, op in (('lifecycle_operations', 'native-prior-abort'), ('nav_operations', 'native-prior-nav-1')):
            envelope = json.loads(source.checkpoint_bytes)
            row = envelope['state']['resources'][category][op]
            row['identity' if category == 'lifecycle_operations' else 'fingerprint'] = 'f' * 64
            changed = replace(source, checkpoint_bytes=encode(envelope), cut=replace(source.cut,
                              checkpoint=replace(source.cut.checkpoint, state_digest=fingerprint(envelope['state']))))
            with pytest.raises(StorageError):
                funding_profile(changed, command)
    finally:
        await store.close()
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('profile,operation', [('abort', 'native-prior-abort'), ('nav', 'native-prior-nav-1')])
async def test_committed_original_history_corruption_revokes_cold_import_without_release(database, tmp_path, monkeypatch, profile, operation):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_legacy_core_cashflows import restore

    from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
    values, _, command, cut, facts, approval, importer, store, _ = await setup(
        database, tmp_path, monkeypatch, spot_fill=('SELL', 'USDT'), spot_aborted_only=True, nav_prices=(D('1.2'), D('.8')))
    financial, cfg, owner = values[:3]
    cold = None
    c = database[0]
    try:
        before, claims = await financial.snapshot(cfg.binding), await values[5].claims(cfg.binding)
        await importer.import_pending(command, source_store=store, approval=approval, cut=cut, facts=facts)
        await c.execute('ALTER TABLE life_financial.operations DISABLE TRIGGER USER')
        await c.execute("UPDATE life_financial.operations SET fingerprint=repeat('f',64) WHERE account_id=$1 AND operation_id=$2", cfg.account_id, operation)
        await c.execute('ALTER TABLE life_financial.operations ENABLE TRIGGER USER')
        await financial.close()
        cold, capital, _, importer = await restore(cfg, owner, c)
        view = await cold.snapshot(cfg.binding)
        assert view.recovery_required and (view.balance, view.budget) == (before.balance, before.budget)
        with pytest.raises(StorageError, match='PG_LEGACY_APPLYING_UNVERIFIED'):
            await importer.lookup(command.operation_id)
        assert await capital.claims(cfg.binding) == claims
    finally:
        await store.close()
        await financial.close()
        if cold is not None:
            await cold.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('database', [67], indirect=True)
async def test_actual_67_abort_nav_history_upgrade_preserves_highwater_and_original_receipts(database, tmp_path, monkeypatch, reference_migrations_82):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.historical_client import (
        pin_pre84_client,
        provision_upgraded_client,
    )
    await pin_pre84_client(monkeypatch, database[0])

    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_legacy_core_cashflows import restore

    from hummingbot.strategy_v2.life_liquidity.storage.postgres import runtime
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.migrations import migrate
    load, expected = runtime.load_migrations, {}
    with monkeypatch.context() as patch:
        patch.setattr(runtime, 'load_migrations', lambda *a, **k: tuple(m for m in load(*a, **k) if m.version <= 67))
        prepared = await setup(database, tmp_path, monkeypatch, 'receipt', spot_fill=('SELL', 'USDT'),
                               spot_aborted_only=True, nav_prices=(D('1.2'), D('.8')), file_result=expected)
    values, _, command, cut, facts, approval, _, store, source = prepared
    financial, cfg, owner = values[:3]
    c, cold = database[0], None
    try:
        before = await financial.snapshot(cfg.binding)
        navs = await c.fetch('SELECT * FROM life_financial.spot_nav_observations WHERE account_id=$1 ORDER BY sequence', cfg.account_id)
        await financial.close()
        assert (await migrate(c, reference_migrations_82, action='up'))['applied'] == ['000068_legacy_abort_nav_history.sql', '000069_legacy_joint_spot_claims.sql', '000070_repair_view_reuse.sql', '000071_required_ack_completion.sql', '000072_unfilled_swap_prefix.sql', '000073_joint_provider_routes.sql', '000074_joint_source_deadline.sql', '000075_joint_trade_id_sets.sql', '000076_signed_native_control.sql', '000077_reconciled_session_successors.sql', '000078_native_safety.sql', '000079_native_markout_gate.sql', '000080_native_stress.sql', '000081_native_execution_modes.sql', '000082_native_execution_children.sql']
        await provision_upgraded_client(c, values[1].account_id)
        cold, capital, batch, importer = await restore(cfg, owner, c)
        after = await cold.snapshot(cfg.binding)
        assert (after.balance, after.budget, after.cut) == (before.balance, before.budget, before.cut)
        restored = list(values)
        restored[0], restored[5] = cold, capital
        await recover_case(database, tmp_path, monkeypatch, 'receipt', 'SELL', 'USDT',
                           prepared=(tuple(restored), batch, command, cut, facts, approval, importer, store, source),
                           expected=expected, spot_aborted_only=True)
        assert navs == await c.fetch('SELECT * FROM life_financial.spot_nav_observations WHERE account_id=$1 ORDER BY sequence', cfg.account_id)
    finally:
        await store.close()
        await financial.close()
        if cold is not None:
            await cold.close()
