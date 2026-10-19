"""An original cancel request never frees a hold during retired File recovery."""
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_legacy_applying import setup
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_legacy_spot import recover_case

import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize('cancel', ['request', 'retry', 'request_before_ack', 'retry_before_ack'])
@pytest.mark.parametrize('side', ['BUY', 'SELL'])
@pytest.mark.parametrize('stage', ['wallet_before', 'receipt'])
async def test_original_cancel_request_and_attempt_keep_holds_receipts_and_money_during_recovery(database, tmp_path, monkeypatch, side, stage, cancel):
    expected = {}
    prepared = await setup(database, tmp_path, monkeypatch, stage, spot_fill=(side, 'USDT'), prior_spot_order=True,
                           prior_cancel=cancel, file_result=expected)
    c, cfg = database[0], prepared[0][1]
    progress = await c.fetchval("SELECT to_jsonb(p) FROM life_financial.spot_cancel_progress p WHERE account_id=$1 AND intent_id='previous-intent'", cfg.account_id)
    await recover_case(database, tmp_path, monkeypatch, stage, side, 'USDT', prepared=prepared, expected=expected, prior_spot_order=True)
    assert await c.fetchval("SELECT to_jsonb(p) FROM life_financial.spot_cancel_progress p WHERE account_id=$1 AND intent_id='previous-intent'", cfg.account_id) == progress


@pytest.mark.asyncio
async def test_original_cancel_basis_rejects_changed_file_receipts_and_native_retries_at_original_cut(database, tmp_path, monkeypatch):
    import copy
    import json
    from dataclasses import replace

    from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError, encode, fingerprint
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.legacy_applying import funding_profile
    values, _, command, cut, _, _, _, store, source = await setup(
        database, tmp_path, monkeypatch, spot_fill=('BUY', 'USDT'), prior_spot_order=True, prior_cancel='retry')
    financial, cfg = values[:2]
    c = database[0]
    try:
        basis = funding_profile(source, command)

        async def valid(body=basis, version=cut.checkpoint.revision):
            return await c.fetchval('SELECT life_financial.legacy_cancel_prefix_valid($1,$2::jsonb,$3)', cfg.account_id, json.dumps(body), version)
        assert await valid()
        stripped = copy.deepcopy(basis)
        stripped.pop('spot_cancellations')
        assert not await valid(stripped)
        before = await c.fetchval("SELECT committed_version-1 FROM life_financial.operations WHERE account_id=$1 AND operation_id='previous-cancel'", cfg.account_id)
        assert not await valid(version=before)  # File callbacks cannot predate their independent native originals.
        for fault in ('state', 'result', 'command_id', 'owner', 'retry_count', 'interval', 'callback', 'missing_request', 'missing_attempt'):
            envelope = json.loads(source.checkpoint_bytes)
            resources = envelope['state']['resources']
            rows = resources['lifecycle_operations']
            row = rows['previous-cancel-attempt']
            if fault == 'state':
                row['state'] = 'APPLYING'
            elif fault == 'result':
                row['result'] = row['prior_state'] = 'SEND_UNKNOWN'
            elif fault == 'command_id':
                row['command']['operation_id'] = 'invented'
            elif fault == 'owner':
                row['owner']['epoch'] += 1
            elif fault == 'retry_count':
                row['command']['expected_cancel_attempts'] += 1
            elif fault == 'interval':
                row['command']['retry_interval_ms'] += 1
            elif fault == 'callback':
                row['command']['attempted_at'] = row['owner']['valid_from']
            elif fault == 'missing_request':
                del rows['previous-cancel']
            else:
                del rows['previous-cancel-attempt']
            row['identity'] = fingerprint({'command': row['command'], 'owner': row['owner']})
            changed = replace(source, checkpoint_bytes=encode(envelope), cut=replace(source.cut,
                              checkpoint=replace(source.cut.checkpoint, state_digest=fingerprint(envelope['state']))))
            if fault in ('missing_request', 'interval', 'result'):
                # A retry itself implies cancellation; native original coverage
                # independently rejects dropping an earlier explicit request.
                assert not await valid(funding_profile(changed, command))
            else:
                with pytest.raises(StorageError):
                    funding_profile(changed, command)
        for table, statement in (
            ('operations', "UPDATE life_financial.operations SET fingerprint=repeat('f',64) WHERE account_id=$1 AND operation_id='previous-cancel'"),
            ('operations', "UPDATE life_financial.operations SET receipt=jsonb_set(receipt,'{version}','0') WHERE account_id=$1 AND operation_id='previous-cancel-attempt'"),
            ('operations', "DELETE FROM life_financial.operations WHERE account_id=$1 AND operation_id='previous-cancel'"),
            ('spot_lifecycle_events', "UPDATE life_financial.spot_lifecycle_events SET after_cancel=jsonb_set(after_cancel,'{cancel_attempts}','0') WHERE account_id=$1 AND operation_id='previous-cancel-attempt'"),
            ('spot_lifecycle_events', "UPDATE life_financial.spot_lifecycle_events SET after_cancel=jsonb_set(after_cancel,'{version}','99') WHERE account_id=$1 AND operation_id='previous-cancel'"),
        ):
            tx = c.transaction()
            await tx.start()
            try:
                await c.execute(f'ALTER TABLE life_financial.{table} DISABLE TRIGGER USER')
                await c.execute(statement, cfg.account_id)
                assert not await valid(), statement
            finally:
                await tx.rollback()
        assert await valid()
    finally:
        await store.close()
        await financial.close()


@pytest.mark.asyncio
async def test_cancel_receipt_corruption_after_import_denies_cold_proof_and_keeps_money_and_claims(database, tmp_path, monkeypatch):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_legacy_core_cashflows import restore

    from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
    values, _, command, cut, facts, approval, importer, store, _ = await setup(
        database, tmp_path, monkeypatch, spot_fill=('SELL', 'USDT'), prior_spot_order=True, prior_cancel='retry')
    financial, cfg, owner = values[:3]
    c, cold = database[0], None
    try:
        before, claims = await financial.snapshot(cfg.binding), await values[5].claims(cfg.binding)
        await importer.import_pending(command, source_store=store, approval=approval, cut=cut, facts=facts)
        await c.execute('ALTER TABLE life_financial.operations DISABLE TRIGGER USER')
        await c.execute("UPDATE life_financial.operations SET receipt=jsonb_set(receipt,'{version}','0') WHERE account_id=$1 AND operation_id='previous-cancel'", cfg.account_id)
        await c.execute('ALTER TABLE life_financial.operations ENABLE TRIGGER USER')
        await financial.close()
        cold, capital, _, importer = await restore(cfg, owner, c)
        view = await cold.snapshot(cfg.binding)
        assert view.recovery_required and (view.balance, view.budget) == (before.balance, before.budget)
        with pytest.raises(StorageError, match='PG_LEGACY_APPLYING_UNVERIFIED'):
            await importer.lookup(command.operation_id)
        assert await capital.claims(cfg.binding) == claims
        assert await c.fetchval('SELECT halted FROM life_financial.accounts WHERE account_id=$1', cfg.account_id)
    finally:
        await store.close()
        await financial.close()
        if cold is not None:
            await cold.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('database', [64], indirect=True)
async def test_actual_64_original_cancels_upgrade_without_resetting_retries_holds_or_receipts(database, tmp_path, monkeypatch):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_legacy_core_cashflows import restore

    from hummingbot.strategy_v2.life_liquidity.storage.postgres import runtime
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.migrations import migrate
    load, expected = runtime.load_migrations, {}
    with monkeypatch.context() as patch:
        patch.setattr(runtime, 'load_migrations', lambda *a, **k: tuple(v for v in load(*a, **k) if v.version <= 64))
        prepared = await setup(database, tmp_path, monkeypatch, 'receipt', spot_fill=('SELL', 'USDT'),
                               prior_spot_order=True, prior_cancel='retry', file_result=expected)
    values, _, command, cut, facts, approval, _, store, source = prepared
    financial, cfg, owner = values[:3]
    c, cold = database[0], None
    try:
        before = await financial.snapshot(cfg.binding)
        rows = await c.fetchval('SELECT jsonb_agg(to_jsonb(o) ORDER BY operation_id) FROM life_financial.operations o WHERE account_id=$1', cfg.account_id)
        progress = await c.fetchval("SELECT to_jsonb(p) FROM life_financial.spot_cancel_progress p WHERE account_id=$1 AND intent_id='previous-intent'", cfg.account_id)
        await financial.close()
        assert (await migrate(c, action='up'))['applied'] == ['000065_legacy_cancel_prefix.sql', '000066_legacy_terminal_prefix.sql', '000067_legacy_settlement_prefix.sql', '000068_legacy_abort_nav_history.sql', '000069_legacy_joint_spot_claims.sql', '000070_repair_view_reuse.sql', '000071_required_ack_completion.sql', '000072_unfilled_swap_prefix.sql', '000073_joint_provider_routes.sql', '000074_joint_source_deadline.sql', '000075_joint_trade_id_sets.sql', '000076_signed_native_control.sql', '000077_reconciled_session_successors.sql', '000078_native_safety.sql', '000079_native_markout_gate.sql', '000080_native_stress.sql', '000081_native_execution_modes.sql', '000082_native_execution_children.sql']
        cold, capital, batch, importer = await restore(cfg, owner, c)
        after = await cold.snapshot(cfg.binding)
        assert (after.balance, after.budget, after.cut) == (before.balance, before.budget, before.cut)
        assert await c.fetchval('SELECT jsonb_agg(to_jsonb(o) ORDER BY operation_id) FROM life_financial.operations o WHERE account_id=$1', cfg.account_id) == rows
        updated = list(values)
        updated[0], updated[5] = cold, capital
        await recover_case(database, tmp_path, monkeypatch, 'receipt', 'SELL', 'USDT',
                           prepared=(tuple(updated), batch, command, cut, facts, approval, importer, store, source), expected=expected, prior_spot_order=True)
        assert await c.fetchval("SELECT to_jsonb(p) FROM life_financial.spot_cancel_progress p WHERE account_id=$1 AND intent_id='previous-intent'", cfg.account_id) == progress
    finally:
        await store.close()
        await financial.close()
        if cold is not None:
            await cold.close()


@pytest.mark.asyncio
async def test_multiple_original_retries_survive_cold_recovery_without_counter_reset(database, tmp_path, monkeypatch):
    await test_original_cancel_request_and_attempt_keep_holds_receipts_and_money_during_recovery(
        database, tmp_path, monkeypatch, 'BUY', 'receipt', 'retry_three')
