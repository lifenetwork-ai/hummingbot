"""Actual interrupted SPOT/funding File saga → native original-ID cold recovery."""
import copy
import hashlib
import json
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_legacy_applying import KEYS, setup
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_legacy_core_cashflows import restore
from types import SimpleNamespace

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError, fingerprint
from hummingbot.strategy_v2.life_liquidity.storage.postgres.joint_repair import (
    JointRepairFacts,
    PgJointRepairRepository,
    ResolveJointSources,
)
from hummingbot.strategy_v2.life_liquidity.storage.postgres.joint_repair_approval import PgJointRepairApprovalImporter
from hummingbot.strategy_v2.life_liquidity.storage.postgres.legacy_applying import funding_profile
from hummingbot.strategy_v2.life_liquidity.storage.postgres.migrations import migrate


def arithmetic_profile(source, state, operations):
    # Component-level arithmetic refusal must get past metadata/hash checks.
    # This does not produce a signed source or authorize a native import.
    operations = copy.deepcopy(operations)
    for row in operations.values():
        row['cut']['components'] = [[name, hashlib.sha256(bytes.fromhex(raw)).hexdigest()]
                                    for name, raw in sorted(row['before_native'].items())]
        row['fingerprint'] = fingerprint({'command': row['command'], 'cut': row['cut'], 'owner': row['owner']})
    return SimpleNamespace(checkpoint_bytes=json.dumps(state).encode(), original_operations=operations, cut=source.cut,
                           ancillary_images=(), core_pending_operation_ids=source.core_pending_operation_ids, facts=source.facts)


async def recover_case(database, tmp_path, monkeypatch, stage, side, fee_currency, cashflow=None, spot_prefix=False, book_native_prefix=False,
                       prepared=None, expected=None, spot_open_only=False, book_native_cashflow=False, prior_funding=(), prior_after_accounting=False, native_parent=False, spot_prepared_only=False):
    expected = {} if expected is None else expected
    values, batch, command, cut, facts, approval, importer, store, source = prepared or await setup(
        database, tmp_path, monkeypatch, stage, spot_fill=(side, fee_currency), file_result=expected, cashflow=cashflow,
        spot_prefix=spot_prefix, book_native_prefix=book_native_prefix, spot_open_only=spot_open_only, book_native_cashflow=book_native_cashflow,
        prior_funding=prior_funding, prior_after_accounting=prior_after_accounting, book_native_accounting_parent=native_parent, spot_prepared_only=spot_prepared_only)
    financial, cfg, owner = values[:3]
    cold = None
    c = database[0]
    try:
        before, claims = await financial.snapshot(cfg.binding), await values[5].claims(cfg.binding)
        originals = await c.fetchval("SELECT jsonb_agg(to_jsonb(o) ORDER BY operation_id) FROM life_financial.operations o WHERE account_id=$1 AND operation_id LIKE 'native-prior-%'", cfg.account_id)
        assert (await importer.import_pending(command, source_store=store, approval=approval, cut=cut, facts=facts)).state == 'RECOVERY_REQUIRED'
        imported = await financial.snapshot(cfg.binding)
        assert imported.balance == before.balance and imported.budget == before.budget
        assert await c.fetchval('SELECT count(*) FROM life_financial.financial_events WHERE account_id=$1', cfg.account_id) == int(book_native_prefix) + int(book_native_cashflow) + (len(command.batch.funding.bundle.funding.payments) if native_parent else 0)
        await financial.close()
        cold, capital, batch, importer = await restore(cfg, owner, c)
        assert (await importer.import_pending(command, source_store=None, approval=approval, cut=cut, facts=facts)).replayed
        repair_cut = (await cold.observation_cut(cfg.binding)).cut
        now = await c.fetchval('SELECT clock_timestamp()')
        at, b = int(now.timestamp() * 1000), command.batch.funding.bundle
        # The original native parent can advance every provider cursor before
        # this cold repair. A newly captured fake response advances those
        # actual fixture counters; it cannot reuse a conflicting fixed 3.
        prior_sequence = await c.fetchval("""SELECT COALESCE(max(greatest(
            (bundle#>>'{account,snapshot,sequence}')::bigint,(bundle#>>'{mark,sequence}')::bigint,
            (bundle#>>'{index,sequence}')::bigint,(bundle#>>'{funding,sequence}')::bigint,
            (bundle#>>'{terms,sequence}')::bigint)),0) FROM life_financial.funding_observations WHERE account_id=$1""", cfg.account_id)
        sequence = max(prior_sequence, b.account.snapshot.sequence, b.mark.sequence, b.index.sequence, b.funding.sequence, b.terms.sequence) + 1
        fresh = replace(b, account=replace(b.account, snapshot=replace(b.account.snapshot, sequence=sequence, observed_at_ms=at)),
                        mark=replace(b.mark, sequence=sequence, observed_at_ms=at), index=replace(b.index, sequence=sequence, observed_at_ms=at),
                        funding=replace(b.funding, sequence=sequence, observed_at_ms=at, coverage_through_ms=at), terms=replace(b.terms, sequence=sequence, observed_at_ms=at))
        replacement = replace(command.batch, operation_id='spot-funding-applied',
                              funding=replace(command.batch.funding, operation_id='spot-funding-paid', bundle=fresh))
        repair = ResolveJointSources('spot-funding-repair', command.operation_id, replacement)
        trades = tuple(('SPOT', f.trade_id) for group in command.batch.spot_fills for f in group.fills)
        proof = JointRepairFacts(replace(facts, observed_at=now, accepted_wire_ids=('SwapWire1',) if spot_prepared_only else ('SwapWire1', 'wire1')), trades, tuple(p.bill_id for p in b.funding.payments), True,
                                 int(store._accounting_policy.opened_at.timestamp() * 1000), at)
        signed = KEYS.sign('operator', cfg.binding, 'RECOVER', repair.json(), repair_cut, proof, issued_at=now, expires_at=now + timedelta(seconds=10))
        await PgJointRepairApprovalImporter(c, batch, verifier=KEYS.verifier(), max_fact_age_ms=5000).approve(repair, approval=signed, cut=repair_cut, facts=proof)
        assert not (await PgJointRepairRepository(batch).resolve(repair, cut=repair_cut, owner=owner)).trading_permission
        await cold.close()
        cold, capital, batch, importer = await restore(cfg, owner, c)
        assert (await PgJointRepairRepository(batch).resolve(repair, cut=repair_cut, owner=owner)).replayed
        view = await cold.snapshot(cfg.binding)
        assert view.balance == expected['balance']
        assert view.budget == expected['budget']
        assert await capital.claims(cfg.binding) == claims
        assert await c.fetchval("SELECT jsonb_agg(to_jsonb(o) ORDER BY operation_id) FROM life_financial.operations o WHERE account_id=$1 AND operation_id LIKE 'native-prior-%'", cfg.account_id) == originals
        if native_parent:
            assert (await batch.lookup('native-prior-parent', cfg.binding)).state == 'APPLIED'
        assert await c.fetchval("SELECT gross_debits_usdt FROM life_financial.funding_observations WHERE account_id=$1 AND state='APPLIED'", cfg.account_id) == D('.1') + sum((max(D('0'), -v) for v in prior_funding), D('0'))
        assert await c.fetchval('SELECT count(*) FROM life_financial.spot_fill_facts WHERE account_id=$1', cfg.account_id) == len(trades)
        assert await c.fetchval('SELECT count(*) FROM life_financial.funding_cash_payments WHERE account_id=$1', cfg.account_id) == len(prior_funding) + 1
        assert await c.fetchval('SELECT halted FROM life_financial.accounts WHERE account_id=$1', cfg.account_id)
        assert (await batch.lookup(command.operation_id, cfg.binding)).state == 'RECOVERY_REQUIRED'
        assert (await importer.lookup(command.operation_id)).replayed
        assert await store.frozen_legacy_applying_source(store.binding) == source
        if spot_open_only or spot_prepared_only:
            assert (await cold.attempt('intent1', cfg.binding)).state.value == ('PREPARED' if spot_prepared_only else 'SEND_UNKNOWN')
            assert view.balance.reserved_quote_usdt > 0 if side == 'BUY' else view.balance.reserved_base_life > 0
        with pytest.raises(StorageError):
            await cold.arm('operation1', cut=view.cut, owner=owner)
    finally:
        await store.close()
        await financial.close()
        if cold is not None:
            await cold.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['wallet_before', 'wallet_after', 'loss_after', 'floor_after', 'receipt'])
@pytest.mark.parametrize('side,fee_currency', [('BUY', 'USDT'), ('BUY', 'LIFE'), ('SELL', 'USDT'), ('SELL', 'LIFE')])
async def test_interrupted_file_spot_funding_recovers_identical_money_holds_and_gross_budgets_after_two_restarts(
        database, tmp_path, monkeypatch, stage, side, fee_currency):
    await recover_case(database, tmp_path, monkeypatch, stage, side, fee_currency)


@pytest.mark.asyncio
@pytest.mark.parametrize('side,fee_currency,cashflow', [
    ('BUY', 'USDT', ('LIFE', D('2'), D('1.2'))),
    ('SELL', 'LIFE', ('USDT', D('-2'), D('1'))),
])
async def test_approved_transfer_spot_fill_and_funding_recover_together_with_full_file_parity(database, tmp_path, monkeypatch, side, fee_currency, cashflow):
    await recover_case(database, tmp_path, monkeypatch, 'wallet_after', side, fee_currency, cashflow)


@pytest.mark.asyncio
@pytest.mark.parametrize('side,fee_currency,cashflow', [('BUY', 'LIFE', ('USDT', D('2'), D('1'))),
                                                        ('SELL', 'USDT', ('LIFE', D('-2'), D('1.2')))])
async def test_booked_native_transfer_and_spot_prefix_recover_together_without_double_accounting(
        database, tmp_path, monkeypatch, side, fee_currency, cashflow):
    await recover_case(database, tmp_path, monkeypatch, 'wallet_after', side, fee_currency, cashflow,
                       spot_prefix=True, book_native_prefix=True, book_native_cashflow=True)


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['price', 'fee', 'fee_currency', 'quantity', 'trade', 'wire', 'session', 'exchange_order', 'time', 'missing', 'native_preparation', 'native_arm', 'native_arm_id'])
async def test_resigned_import_cannot_change_original_spot_or_independent_native_preparation(database, tmp_path, monkeypatch, fault):
    values, _, command, _, facts, _, importer, store, _ = await setup(database, tmp_path, monkeypatch, spot_fill=('BUY', 'USDT'))
    financial, cfg, _ = values[:3]
    c = database[0]
    try:
        fill = command.batch.spot_fills[0]
        leaf = dict(price=dict(price_usdt=D('1.1')), fee=dict(signed_fee=D('-.01')), fee_currency=dict(fee_currency='LIFE'),
                    quantity=dict(quantity_base_life=D('.5')), trade=dict(trade_id='changed'), time=dict(fill_at_ms=fill.fills[0].fill_at_ms + 1))
        group = dict(wire=dict(client_order_id='changed'), session=dict(session_id='changed'), exchange_order=dict(exchange_order_id='999'))
        if fault in leaf:
            fill = replace(fill, fills=(replace(fill.fills[0], **leaf[fault]),),
                           **({'cumulative_base_life': D('.5')} if fault == 'quantity' else {}))
        elif fault in group:
            fill = replace(fill, **group[fault])
        elif fault == 'native_preparation':
            await c.execute('ALTER TABLE life_financial.spot_preparations DISABLE TRIGGER USER')
            await c.execute("UPDATE life_financial.spot_preparations SET request=jsonb_set(request,'{reference_price_usdt}','\"2\"') WHERE account_id=$1", cfg.account_id)
            await c.execute('ALTER TABLE life_financial.spot_preparations ENABLE TRIGGER USER')
        elif fault in ('native_arm', 'native_arm_id'):
            await c.execute('ALTER TABLE life_financial.operations DISABLE TRIGGER USER')
            await c.execute(("UPDATE life_financial.operations SET operation_id='forged-arm'" if fault == 'native_arm_id' else
                             "UPDATE life_financial.operations SET command=jsonb_set(command,'{prepare}','\"other\"')") +
                            " WHERE account_id=$1 AND operation_id='arm:operation1'", cfg.account_id)
            await c.execute('ALTER TABLE life_financial.operations ENABLE TRIGGER USER')
        command = replace(command, batch=replace(command.batch, spot_fills=() if fault == 'missing' else (fill,)))
        now = await c.fetchval('SELECT clock_timestamp()')
        facts = replace(facts, observed_at=now)
        cut = (await financial.observation_cut(cfg.binding)).cut
        signature = KEYS.sign('operator', cfg.binding, 'IMPORT', command.json(), cut, facts, issued_at=now, expires_at=now + timedelta(seconds=10))
        reason = 'PG_LEGACY_APPLYING_ORIGINAL_SPOT_BASIS_REQUIRED' if fault.startswith('native_') else 'PG_LEGACY_APPLYING_FACTS_CHANGED'
        with pytest.raises(StorageError, match=reason):
            await importer.import_pending(command, source_store=store, approval=signature, cut=cut, facts=facts)
        for table in ('legacy_applying_imports', 'joint_financial_batches', 'spot_fill_facts', 'funding_cash_payments'):
            assert await c.fetchval(f'SELECT count(*) FROM life_financial.{table} WHERE account_id=$1', cfg.account_id) == 0
    finally:
        await store.close()
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['native_preparation', 'native_arm', 'native_arm_id', 'native_prefix'])
async def test_original_native_basis_corruption_after_import_blocks_cold_source_readback_and_snapshot(database, tmp_path, monkeypatch, fault):
    values, _, command, cut, facts, approval, importer, store, _ = await setup(
        database, tmp_path, monkeypatch, spot_fill=('BUY', 'USDT'), spot_prefix=fault == 'native_prefix', book_native_prefix=fault == 'native_prefix')
    financial, cfg, owner = values[:3]
    cold = None
    c = database[0]
    try:
        await importer.import_pending(command, source_store=store, approval=approval, cut=cut, facts=facts)
        assert await c.fetchval('SELECT life_financial.legacy_spot_verified($1)', cfg.account_id)
        if fault == 'native_prefix':
            await c.execute('ALTER TABLE life_financial.spot_fill_facts DISABLE TRIGGER USER')
            await c.execute("UPDATE life_financial.spot_fill_facts SET observation=jsonb_set(observation,'{signed_fee}','\"-.002\"') WHERE account_id=$1", cfg.account_id)
            await c.execute('ALTER TABLE life_financial.spot_fill_facts ENABLE TRIGGER USER')
        elif fault == 'native_preparation':
            await c.execute('ALTER TABLE life_financial.spot_preparations DISABLE TRIGGER USER')
            await c.execute("UPDATE life_financial.spot_preparations SET request=jsonb_set(request,'{reference_price_usdt}','\"2\"') WHERE account_id=$1", cfg.account_id)
            await c.execute('ALTER TABLE life_financial.spot_preparations ENABLE TRIGGER USER')
        else:
            await c.execute('ALTER TABLE life_financial.operations DISABLE TRIGGER USER')
            await c.execute(("UPDATE life_financial.operations SET operation_id='forged-arm'" if fault == 'native_arm_id' else
                             "UPDATE life_financial.operations SET command=jsonb_set(command,'{prepare}','\"other\"')") +
                            " WHERE account_id=$1 AND operation_id='arm:operation1'", cfg.account_id)
            await c.execute('ALTER TABLE life_financial.operations ENABLE TRIGGER USER')
        assert not await c.fetchval('SELECT life_financial.legacy_spot_verified($1)', cfg.account_id)
        assert not await c.fetchval('SELECT life_financial.legacy_applying_verified($1)', cfg.account_id)
        await financial.close()
        cold, _, _, importer = await restore(cfg, owner, c)
        assert (await cold.snapshot(cfg.binding)).recovery_required
        with pytest.raises(StorageError, match='PG_LEGACY_APPLYING_UNVERIFIED'):
            await importer.lookup(command.operation_id)
        assert await c.fetchval('SELECT count(*) FROM life_financial.financial_events WHERE account_id=$1', cfg.account_id) == int(fault == 'native_prefix')
        assert await c.fetchval('SELECT halted FROM life_financial.accounts WHERE account_id=$1', cfg.account_id)
    finally:
        await store.close()
        await financial.close()
        if cold is not None:
            await cold.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['wallet_before', 'wallet_after', 'loss_after', 'floor_after', 'receipt'])
@pytest.mark.parametrize('side', ['BUY', 'SELL'])
@pytest.mark.parametrize('fee_currency', ['LIFE', 'USDT'])
@pytest.mark.parametrize('book_native_prefix', [False, True])
async def test_prior_spot_prefix_survives_import_and_cold_recovery_without_recredit_or_budget_reset(
        database, tmp_path, monkeypatch, stage, side, fee_currency, book_native_prefix):
    await recover_case(database, tmp_path, monkeypatch, stage, side, fee_currency,
                       spot_prefix=True, book_native_prefix=book_native_prefix)


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['wallet_before', 'wallet_after', 'receipt'])
@pytest.mark.parametrize('side', ['BUY', 'SELL'])
async def test_funding_recovery_preserves_original_unfilled_spot_order_and_holds(database, tmp_path, monkeypatch, stage, side):
    await recover_case(database, tmp_path, monkeypatch, stage, side, 'USDT', spot_open_only=True)


@pytest.mark.asyncio
async def test_unfilled_spot_prefix_cannot_drop_or_reset_its_original_holds(database, tmp_path, monkeypatch):
    values, _, command, _, _, _, _, store, source = await setup(
        database, tmp_path, monkeypatch, spot_fill=('BUY', 'USDT'), spot_open_only=True)
    try:
        envelope = json.loads(source.checkpoint_bytes)
        original = copy.deepcopy(source.original_operations)
        operation = next(iter(original))
        funding_profile(source, command)
        for journal, change in (
            ('reservations', lambda v: v['reservations'].clear()),
            ('reservations', lambda v: v['reservations']['intent1'].update(remaining_base='0')),
            ('reservations', lambda v: v['reservations']['intent1'].update(filled_base='1')),
            ('subsidy', lambda v: v['entries'].clear()),
            ('subsidy', lambda v: v['entries']['intent1'].update(reserved='0')),
            ('subsidy', lambda v: v['entries']['intent1'].update(actual='0')),
            ('subsidy', lambda v: v['entries']['intent1'].update(fill_floor='1')),
        ):
            # Pure arithmetic refusal over copies, not resigned source imports.
            operations = copy.deepcopy(original)
            before = json.loads(bytes.fromhex(operations[operation]['before_native'][journal]))
            change(before)
            operations[operation]['before_native'][journal] = json.dumps(before).encode().hex()
            profile = arithmetic_profile(source, envelope, operations)
            with pytest.raises(StorageError, match='PG_LEGACY_APPLYING_ORIGINAL_BASIS_REQUIRED'):
                funding_profile(profile, command)
    finally:
        await store.close()
        await values[0].close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['prepared', 'arm', 'arm_id'])
async def test_unfilled_file_order_still_requires_exact_independent_native_preparation_and_arm(database, tmp_path, monkeypatch, fault):
    values, _, command, _, facts, _, importer, store, _ = await setup(
        database, tmp_path, monkeypatch, spot_fill=('BUY', 'USDT'), spot_open_only=True)
    financial, cfg = values[:2]
    c = database[0]
    try:
        table = 'spot_preparations' if fault == 'prepared' else 'operations'
        await c.execute(f'ALTER TABLE life_financial.{table} DISABLE TRIGGER USER')
        statement = {
            'prepared': "UPDATE life_financial.spot_preparations SET request=jsonb_set(request,'{reference_price_usdt}','\"2\"') WHERE account_id=$1",
            'arm': "UPDATE life_financial.operations SET command=jsonb_set(command,'{prepare}','\"other\"') WHERE account_id=$1 AND operation_id='arm:operation1'",
            'arm_id': "UPDATE life_financial.operations SET operation_id='changed-arm' WHERE account_id=$1 AND operation_id='arm:operation1'",
        }[fault]
        await c.execute(statement, cfg.account_id)
        await c.execute(f'ALTER TABLE life_financial.{table} ENABLE TRIGGER USER')
        now = await c.fetchval('SELECT clock_timestamp()')
        facts = replace(facts, observed_at=now)
        cut = (await financial.observation_cut(cfg.binding)).cut
        approval = KEYS.sign('operator', cfg.binding, 'IMPORT', command.json(), cut, facts, issued_at=now, expires_at=now + timedelta(seconds=10))
        with pytest.raises(StorageError, match='PG_LEGACY_APPLYING_ORIGINAL_SPOT_BASIS_REQUIRED'):
            await importer.import_pending(command, source_store=store, approval=approval, cut=cut, facts=facts)
        for table in ('legacy_applying_imports', 'spot_fill_facts', 'funding_cash_payments'):
            assert await c.fetchval(f'SELECT count(*) FROM life_financial.{table} WHERE account_id=$1', cfg.account_id) == 0
        assert await c.fetchval('SELECT halted FROM life_financial.accounts WHERE account_id=$1', cfg.account_id)
    finally:
        await store.close()
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('database', [57], indirect=True)
async def test_existing_57_prefix_upgrades_then_recovers_with_original_money_and_receipts(database, tmp_path, monkeypatch):
    from hummingbot.strategy_v2.life_liquidity.storage.postgres import runtime as runtime_module
    load = runtime_module.load_migrations
    expected = {}
    with monkeypatch.context() as patch:
        patch.setattr(runtime_module, 'load_migrations', lambda path: tuple(m for m in load(path) if m.version <= 57))
        prepared = await setup(database, tmp_path, monkeypatch, 'wallet_after', spot_fill=('SELL', 'USDT'),
                               spot_prefix=True, book_native_prefix=True, file_result=expected)
        await prepared[0][0].close()
    values, _, command, cut, facts, approval, _, store, source = prepared
    financial = values[0]
    try:
        assert (await migrate(database[0], action='up'))['applied'] == ['000058_legacy_spot_prefix.sql', '000059_legacy_cashflow_prefix.sql', '000060_legacy_derivative_prefix.sql', '000061_legacy_parent_prefix.sql', '000062_legacy_unsent_spot.sql']
        financial, capital, batch, importer = await restore(values[1], values[2], database[0])
        values = list(values)
        values[0], values[5] = financial, capital
        await recover_case(database, tmp_path, monkeypatch, 'wallet_after', 'SELL', 'USDT', spot_prefix=True, book_native_prefix=True,
                           prepared=(values, batch, command, cut, facts, approval, importer, store, source), expected=expected)
    finally:
        await financial.close()
        await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('database', [58], indirect=True)
async def test_58_orphan_prefix_refusal_after_59_upgrade_uses_original_predecessor_as_oracle(database, tmp_path, monkeypatch):
    from hummingbot.strategy_v2.life_liquidity.storage.postgres import runtime as runtime_module
    load = runtime_module.load_migrations
    c = database[0]
    with monkeypatch.context() as patch:
        patch.setattr(runtime_module, 'load_migrations', lambda path: tuple(m for m in load(path) if m.version <= 58))
        values, _, command, cut, facts, approval, _, store, _ = await setup(
            database, tmp_path, monkeypatch, spot_fill=('BUY', 'USDT'), spot_prefix=True, book_native_prefix=True)
        await values[0].close()
    financial = values[0]
    try:
        old = await c.fetchval("SELECT pg_get_functiondef('life_financial.legacy_spot_prefix_valid(uuid,jsonb,bigint)'::regprocedure)")
        await c.execute('CREATE SCHEMA prefix_reference')
        await c.execute(old.replace('FUNCTION life_financial.legacy_spot_prefix_valid(', 'FUNCTION prefix_reference.legacy_spot_prefix_valid('))
        await c.execute('REVOKE ALL ON FUNCTION prefix_reference.legacy_spot_prefix_valid(uuid,jsonb,bigint) FROM PUBLIC')
        assert (await migrate(c, action='up'))['applied'] == ['000059_legacy_cashflow_prefix.sql', '000060_legacy_derivative_prefix.sql', '000061_legacy_parent_prefix.sql', '000062_legacy_unsent_spot.sql']
        financial, _, _, importer = await restore(values[1], values[2], c)
        await importer.import_pending(command, source_store=store, approval=approval, cut=cut, facts=facts)
        tx = c.transaction()
        await tx.start()
        try:
            await c.execute('ALTER TABLE life_financial.spot_fill_facts DISABLE TRIGGER USER')
            await c.execute("UPDATE life_financial.spot_fill_facts SET source_operation_id='missing-source' WHERE account_id=$1", values[1].account_id)
            args = values[1].account_id, json.dumps(command.batch.json()), cut.checkpoint.revision
            # The exact predecessor incorrectly treats the orphan as no booked
            # prefix. The upgraded proof refuses it before the deferred FK check.
            assert await c.fetchval('SELECT prefix_reference.legacy_spot_prefix_valid($1,$2::jsonb,$3)', *args)
            assert not await c.fetchval('SELECT life_financial.legacy_spot_prefix_valid($1,$2::jsonb,$3)', *args)
            assert not await c.fetchval('SELECT life_financial.legacy_applying_verified($1)', values[1].account_id)
            with pytest.raises(StorageError, match='PG_LEGACY_APPLYING_UNVERIFIED'):
                await importer._row(command.operation_id)
        finally:
            await tx.rollback()
        assert await c.fetchval('SELECT life_financial.legacy_applying_verified($1)', values[1].account_id)
    finally:
        await store.close()
        await financial.close()


@pytest.mark.asyncio
async def test_original_file_prefix_requires_exact_money_fee_hold_loss_floor_and_completed_receipt(database, tmp_path, monkeypatch):
    values, _, command, _, _, _, _, store, source = await setup(database, tmp_path, monkeypatch, spot_fill=('BUY', 'LIFE'), spot_prefix=True)
    financial = values[0]
    try:
        funding_profile(source, command)
        envelope = json.loads(source.checkpoint_bytes)
        original = copy.deepcopy(source.original_operations)
        operation_id = next(iter(original))
        changes = {
            'cash': ('reservations', lambda v: v.update(usdt_balance='10')),
            'base': ('reservations', lambda v: v.update(life_balance='10')),
            'missing_fee': ('reservations', lambda v: v['account_events'].pop('FEE:trade0')),
            'wrong_fee': ('reservations', lambda v: v['account_events']['FEE:trade0'].__setitem__(2, '-.002')),
            'extra_fee': ('reservations', lambda v: v['account_events'].update({'FEE:other': ['FEE', 'LIFE', '-.001']})),
            'quantity': ('reservations', lambda v: v['trades']['trade0'].__setitem__(1, '.6')),
            'filled': ('reservations', lambda v: v['reservations']['intent1'].update(filled_base='.6')),
            'remaining': ('reservations', lambda v: v['reservations']['intent1'].update(remaining_base='2')),
            'missing_loss': ('loss', lambda v: v['events'].pop('fill:trade0')),
            'wrong_loss': ('loss', lambda v: v['events']['fill:trade0'].update(loss_quote='0')),
            'wrong_loss_session': ('loss', lambda v: v['events']['fill:trade0'].update(session_id='other')),
            'wrong_loss_day': ('loss', lambda v: v['events']['fill:trade0'].update(day='2020-01-01')),
            'extra_loss': ('loss', lambda v: v['events'].update({'unexplained': dict(session_id='other', day='2020-01-01', loss_quote='1')})),
            'reset_floor': ('subsidy', lambda v: v['entries']['intent1'].update(fill_floor='0')),
            'reset_hold': ('subsidy', lambda v: v['entries']['intent1'].update(reserved='0')),
        }
        # These are arithmetic-refusal checks over copies of real source
        # journals, not forged sources accepted through the signed importer.

        def profile(state, operations):
            return arithmetic_profile(source, state, operations)
        for name, (journal, change) in changes.items():
            operations = copy.deepcopy(original)
            value = json.loads(bytes.fromhex(operations[operation_id]['before_native'][journal]))
            change(value)
            operations[operation_id]['before_native'][journal] = json.dumps(value).encode().hex()
            with pytest.raises(StorageError, match='PG_LEGACY_APPLYING_ORIGINAL_BASIS_REQUIRED'):
                funding_profile(profile(envelope, operations), command)
        receipt_changes = {
            'missing': lambda v: v.clear(),
            'incomplete': lambda v: v['file-prefix'].update(state='FILLING'),
            'hash': lambda v: v['file-prefix'].update(fingerprint='f' * 64),
            'cost': lambda v: v['file-prefix'].update(execution_loss_usdt='0'),
            'identity': lambda v: v.update(other=v.pop('file-prefix')),
        }
        for name, change in receipt_changes.items():
            state = copy.deepcopy(envelope)
            change(state['state']['resources']['fill_operations'])
            with pytest.raises(StorageError, match='PG_LEGACY_APPLYING_ORIGINAL_BASIS_REQUIRED'):
                funding_profile(profile(state, original), command)
    finally:
        await store.close()
        await financial.close()


@pytest.mark.asyncio
async def test_native_prefix_proof_requires_original_trade_fee_time_wire_session_and_source_receipt(database, tmp_path, monkeypatch):
    values, _, command, cut, _, _, _, store, _ = await setup(
        database, tmp_path, monkeypatch, spot_fill=('BUY', 'LIFE'), spot_prefix=True, book_native_prefix=True)
    financial, cfg = values[:2]
    c = database[0]
    raw = command.batch.json()
    try:
        async def valid(value):
            return await c.fetchval('SELECT life_financial.legacy_spot_prefix_valid($1,$2::jsonb,$3)', cfg.account_id, json.dumps(value), cut.checkpoint.revision)
        assert await valid(raw)
        for field, value in dict(signed_fee='-.002', fee_currency='USDT', quantity_base_life='.6', price_usdt='1.1',
                                 independent_price_usdt='.8', independent_value_at_ms=1, independent_observed_at_ms=1,
                                 fill_at_ms=1, trade_id='other').items():
            changed = copy.deepcopy(raw)
            changed['spot_fills'][0]['fills'][0][field] = value
            assert not await valid(changed), field
        for field in ('prepare_operation_id', 'intent_id', 'client_order_id', 'market', 'side', 'session_id', 'epoch', 'exchange_order_id'):
            changed = copy.deepcopy(raw)
            changed['spot_fills'][0][field] = 'changed'
            assert not await valid(changed), field
        missing = copy.deepcopy(raw)
        missing['spot_fills'][0]['fills'] = missing['spot_fills'][0]['fills'][1:]
        assert not await valid(missing)
        duplicate = copy.deepcopy(raw)
        duplicate['spot_fills'].append(copy.deepcopy(duplicate['spot_fills'][0]))
        assert not await valid(duplicate)
        # These transactions deliberately bypass immutable-record triggers in
        # the disposable DB, then roll back. No application role can do this.
        for statement in (
            "UPDATE life_financial.spot_fill_facts SET observation=jsonb_set(observation,'{signed_fee}','\"-.002\"') WHERE account_id=$1",
            "UPDATE life_financial.operations SET command=jsonb_set(command,'{command,client_order_id}','\"changed\"') WHERE account_id=$1 AND operation_id='file-prefix'",
            "UPDATE life_financial.operations SET receipt=jsonb_set(receipt,'{state}','\"RECOVERY_REQUIRED\"') WHERE account_id=$1 AND operation_id='file-prefix'",
        ):
            tx = c.transaction()
            await tx.start()
            try:
                for table in ('spot_fill_facts', 'operations'):
                    await c.execute(f'ALTER TABLE life_financial.{table} DISABLE TRIGGER USER')
                await c.execute(statement, cfg.account_id)
                assert not await valid(raw), statement
            finally:
                await tx.rollback()
        assert await valid(raw)
    finally:
        await store.close()
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['wallet_before', 'receipt'])
@pytest.mark.parametrize('side,fee_currency,cashflow', [('BUY', 'LIFE', ('USDT', D('2'), D('1'))),
                                                        ('SELL', 'USDT', ('LIFE', D('-2'), D('1.2')))])
async def test_completed_funding_after_booked_spot_and_transfer_prefix_preserves_every_original_basis(
        database, tmp_path, monkeypatch, stage, side, fee_currency, cashflow):
    await recover_case(database, tmp_path, monkeypatch, stage, side, fee_currency, cashflow,
                       spot_prefix=True, book_native_prefix=True, book_native_cashflow=True,
                       prior_funding=(D('-.03'), D('.02')), prior_after_accounting=True)


@pytest.mark.asyncio
@pytest.mark.parametrize('side,fee_currency,cashflow', [('BUY', 'LIFE', ('USDT', D('2'), D('1'))),
                                                        ('SELL', 'USDT', ('LIFE', D('-2'), D('1.2')))])
async def test_original_native_spot_transfer_funding_parent_recovers_only_the_missing_file_leaves(
        database, tmp_path, monkeypatch, side, fee_currency, cashflow):
    await recover_case(database, tmp_path, monkeypatch, 'wallet_after', side, fee_currency, cashflow,
                       spot_prefix=True, book_native_prefix=True, book_native_cashflow=True,
                       prior_funding=(D('-.03'), D('.02')), prior_after_accounting=True, native_parent=True)
