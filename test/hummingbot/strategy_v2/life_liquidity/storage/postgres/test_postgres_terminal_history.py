"""Original completed terminals survive interrupted File money recovery."""
import copy
import json
from dataclasses import replace
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_legacy_applying import setup
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_legacy_spot import recover_case

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError, encode, fingerprint
from hummingbot.strategy_v2.life_liquidity.storage.postgres.legacy_applying import funding_profile


@pytest.mark.asyncio
@pytest.mark.parametrize('side', ['BUY', 'SELL'])
@pytest.mark.parametrize('stage', ['wallet_before', 'receipt'])
@pytest.mark.parametrize('cancel', [False, 'retry_before_ack'])
@pytest.mark.parametrize('terminal', [True, 'zero'])
async def test_original_terminal_history_preserves_receipts_claims_and_gross_budgets(database, tmp_path, monkeypatch, side, stage, cancel, terminal):
    expected = {}
    prepared = await setup(database, tmp_path, monkeypatch, stage, spot_fill=(side, 'USDT'), file_result=expected,
                           prior_spot_order=True, prior_terminal=terminal, prior_cancel=cancel,
                           prior_after_accounting=True, prior_funding=(D('-.03'), D('.02')))
    await recover_case(database, tmp_path, monkeypatch, stage, side, 'USDT', prepared=prepared, expected=expected,
                       prior_spot_order=True, prior_spot_zero=terminal == 'zero', prior_after_accounting=True, prior_funding=(D('-.03'), D('.02')))


@pytest.mark.asyncio
async def test_terminal_history_requires_exact_file_projection_and_independent_native_receipt(database, tmp_path, monkeypatch):
    values, _, command, cut, _, _, _, store, source = await setup(
        database, tmp_path, monkeypatch, spot_fill=('BUY', 'USDT'), prior_spot_order=True, prior_terminal=True)
    financial, cfg = values[:2]
    c = database[0]
    try:
        basis = funding_profile(source, command)

        async def valid(body=basis, version=cut.checkpoint.revision):
            return await c.fetchval('SELECT life_financial.legacy_terminal_prefix_valid($1,$2::jsonb,$3)',
                                    cfg.account_id, json.dumps(body), version)
        assert await valid()
        original_version = await c.fetchval("SELECT committed_version FROM life_financial.operations WHERE account_id=$1 AND operation_id='previous-terminal'", cfg.account_id)
        assert not await valid(version=original_version - 1)
        for body in ({k: v for k, v in basis.items() if k != 'spot_terminals'},
                     {**basis, 'spot_terminals': []}, {**basis, 'spot_terminals': basis['spot_terminals'] * 2},
                     {**basis, 'spot_terminals': None}):
            assert not await valid(body)
        for path, value in ((('facts', 'account_quote_usdt'), '1000'), (('facts', 'cumulative_base_life'), '0'),
                            (('exchange_order_id',), '1'), (('operation_id',), 'invented')):
            changed = copy.deepcopy(basis)
            row = changed['spot_terminals'][0]
            for key in path[:-1]:
                row = row[key]
            row[path[-1]] = value
            assert not await valid(changed)
        for table, statement in (
            ('operations', "UPDATE life_financial.operations SET fingerprint=repeat('f',64) WHERE account_id=$1 AND operation_id='previous-terminal'"),
            ('operations', "UPDATE life_financial.operations SET receipt=jsonb_set(receipt,'{subsidy_settled}','true') WHERE account_id=$1 AND operation_id='previous-terminal'"),
            ('operations', "UPDATE life_financial.operations SET receipt=jsonb_set(receipt,'{version}','0') WHERE account_id=$1 AND operation_id='previous-terminal'"),
            ('spot_terminal_facts', "DELETE FROM life_financial.spot_terminal_facts WHERE account_id=$1"),
            ('spot_terminal_facts', "UPDATE life_financial.spot_terminal_facts SET subsidy_settled=true WHERE account_id=$1"),
        ):
            tx = c.transaction()
            await tx.start()
            try:
                await c.execute(f'ALTER TABLE life_financial.{table} DISABLE TRIGGER USER')
                await c.execute(statement, cfg.account_id)
                assert not await valid(), statement
            finally:
                await tx.rollback()
        for fault in ('missing', 'state', 'principal', 'hash', 'core_hash', 'order', 'fills'):
            envelope = json.loads(source.checkpoint_bytes)
            resources = envelope['state']['resources']
            row = resources['terminal_operations']['previous-terminal']
            if fault == 'missing':
                del resources['terminal_operations']['previous-terminal']
            elif fault == 'state':
                row['state'] = 'TERMINATING'
            elif fault == 'principal':
                row['principal_remaining_base'] = '0'
            elif fault in ('hash', 'core_hash'):
                row['core_fingerprint' if fault == 'core_hash' else 'fingerprint'] = 'f' * 64
            elif fault == 'order':
                row['command']['client_order_id'] = 'other'
            else:
                row['command']['facts']['fills'] = []
            changed = replace(source, checkpoint_bytes=encode(envelope), cut=replace(source.cut,
                              checkpoint=replace(source.cut.checkpoint, state_digest=fingerprint(envelope['state']))))
            with pytest.raises(StorageError):
                funding_profile(changed, command)
        assert await valid()
    finally:
        await store.close()
        await financial.close()


@pytest.mark.asyncio
async def test_terminal_prefix_remains_verified_after_import_and_cold_read(database, tmp_path, monkeypatch):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_legacy_core_cashflows import restore
    values, _, command, cut, facts, approval, importer, store, _ = await setup(
        database, tmp_path, monkeypatch, spot_fill=('SELL', 'USDT'), prior_spot_order=True, prior_terminal=True)
    financial, cfg, owner = values[:3]
    cold = None
    c = database[0]
    try:
        before, claims = await financial.snapshot(cfg.binding), await values[5].claims(cfg.binding)
        assert (await importer.import_pending(command, source_store=store, approval=approval, cut=cut, facts=facts)).state == 'RECOVERY_REQUIRED'
        await c.execute('ALTER TABLE life_financial.operations DISABLE TRIGGER USER')
        await c.execute("UPDATE life_financial.operations SET fingerprint=repeat('f',64) WHERE account_id=$1 AND operation_id='previous-terminal'", cfg.account_id)
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
@pytest.mark.parametrize('terminal', [True, 'zero'])
async def test_file_terminal_without_original_native_provenance_cannot_release_or_import(database, tmp_path, monkeypatch, terminal):
    values, _, command, cut, facts, approval, importer, store, _ = await setup(
        database, tmp_path, monkeypatch, spot_fill=('BUY', 'USDT'), prior_spot_order=True,
        prior_terminal=terminal, native_terminal=False)
    financial, cfg = values[:2]
    c = database[0]
    try:
        before, claims = await financial.snapshot(cfg.binding), await values[5].claims(cfg.binding)
        with pytest.raises(StorageError):
            await importer.import_pending(command, source_store=store, approval=approval, cut=cut, facts=facts)
        after = await financial.snapshot(cfg.binding)
        assert (after.balance, after.budget, after.cut) == (before.balance, before.budget, before.cut)
        assert await values[5].claims(cfg.binding) == claims
        assert not await c.fetchval('SELECT EXISTS(SELECT 1 FROM life_financial.legacy_applying_imports WHERE account_id=$1)', cfg.account_id)
        assert (await financial.attempt('previous-intent', cfg.binding)).state.value == 'ACKED'
    finally:
        await store.close()
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('database', [65], indirect=True)
async def test_actual_65_terminal_history_upgrades_and_recovers_original_money_and_claims(database, tmp_path, monkeypatch):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_legacy_core_cashflows import restore

    from hummingbot.strategy_v2.life_liquidity.storage.postgres import runtime
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.migrations import migrate
    load = runtime.load_migrations
    expected = {}
    with monkeypatch.context() as patch:
        patch.setattr(runtime, 'load_migrations', lambda *a, **k: tuple(m for m in load(*a, **k) if m.version <= 65))
        prepared = await setup(database, tmp_path, monkeypatch, 'receipt', spot_fill=('SELL', 'USDT'), prior_spot_order=True,
                               prior_terminal=True, file_result=expected)
    values, batch, command, cut, facts, approval, importer, store, source = prepared
    financial, cfg, owner = values[:3]
    cold = None
    try:
        before = await financial.snapshot(cfg.binding)
        await financial.close()
        assert (await migrate(database[0], action='up'))['applied'] == ['000066_legacy_terminal_prefix.sql', '000067_legacy_settlement_prefix.sql', '000068_legacy_abort_nav_history.sql']
        cold, capital, batch, importer = await restore(cfg, owner, database[0])
        after = await cold.snapshot(cfg.binding)
        assert (after.balance, after.budget, after.cut) == (before.balance, before.budget, before.cut)
        restored = list(values)
        restored[0], restored[5] = cold, capital
        await recover_case(database, tmp_path, monkeypatch, 'receipt', 'SELL', 'USDT',
                           prepared=(tuple(restored), batch, command, cut, facts, approval, importer, store, source),
                           expected=expected, prior_spot_order=True)
    finally:
        await store.close()
        await financial.close()
        if cold is not None:
            await cold.close()
