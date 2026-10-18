"""Interrupted real File funding -> native pending -> signed native accounting.

The original native opening basis is explicitly seeded by the isolated fixture.
This is not a general wallet/bootstrap importer or a configured-service test.
"""
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
from uuid import uuid4

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown, OwnerMode, StorageError
from hummingbot.strategy_v2.life_liquidity.storage.control_plane import OperatorKeys, OwnerTransition
from hummingbot.strategy_v2.life_liquidity.storage.financial import SpotFillPolicy
from hummingbot.strategy_v2.life_liquidity.storage.legacy_applying import RetireLegacyApplying
from hummingbot.strategy_v2.life_liquidity.storage.postgres.joint_batch import ApplyJointBatch, PgJointBatchRepository
from hummingbot.strategy_v2.life_liquidity.storage.postgres.joint_repair import (
    JointRepairFacts,
    PgJointRepairRepository,
    ResolveJointSources,
)
from hummingbot.strategy_v2.life_liquidity.storage.postgres.joint_repair_approval import PgJointRepairApprovalImporter
from hummingbot.strategy_v2.life_liquidity.storage.postgres.legacy_applying import (
    ImportLegacyApplying,
    PgLegacyApplyingImporter,
)
from hummingbot.strategy_v2.life_liquidity.storage.recovery import SpotFinancialRecoveryFacts

KEYS = OperatorKeys({'operator': b'i' * 32}, {'operator': frozenset(('IMPORT', 'RECOVER', 'DRAIN'))})


async def setup(database, tmp_path, monkeypatch, stage='wallet_after'):
    from test.hummingbot.strategy_v2.life_liquidity.storage import test_spot_financial_contracts as file_fixture
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_funding import setup as funding_setup

    from hummingbot.strategy_v2.life_liquidity.storage.capital import SpotAccountingPolicy
    from hummingbot.strategy_v2.life_liquidity.storage.joint_settlement import JointCashEvent, JointCashSpot
    from hummingbot.strategy_v2.life_liquidity.storage.legacy_spot import open_legacy_spot
    from hummingbot.strategy_v2.life_liquidity.storage.recovery import SpotFinancialRecoveryPolicy
    values, funding, payment = await funding_setup(database, carry_options=dict(refresh_initial_facts=True),
                                                   spot_options=dict(configuration_body=lambda _: {'operator_verifier': KEYS.verifier().policy(),
                                                                                                   'legacy_source_verifier': KEYS.verifier().policy()}))
    financial, cfg, owner = values[:3]
    admin = database[0]
    await admin.execute('INSERT INTO life_financial.joint_batch_policies VALUES($1,$2,32,64,1048576)', cfg.account_id, cfg.binding.policy_hash)
    await admin.execute('INSERT INTO life_financial.joint_repair_policies VALUES($1,$2,64,64,1048576,5000)', cfg.account_id, cfg.binding.policy_hash)
    await admin.execute('INSERT INTO life_financial.legacy_applying_policies VALUES($1,$2,4,4194304,5000)', cfg.account_id, cfg.binding.policy_hash)
    now = await admin.fetchval('SELECT clock_timestamp()')
    opened = await admin.fetchval('SELECT opened_at FROM life_financial.spot_capital_policies WHERE account_id=$1', cfg.account_id)
    with monkeypatch.context() as patches:
        patches.setattr(file_fixture, 'NOW', opened)
        files, _, file_owner = file_fixture.create_fixture(tmp_path)
    binding = replace(cfg.binding, lineage=uuid4())
    clock = [now]
    accounting = SpotAccountingPolicy(D('5'), D('10'), D('1'), opened, '100', ())

    async def open_file(*, create=False):
        return await open_legacy_spot(files, binding=binding, limits=file_fixture.CAPACITY,
                                      risk_limits=file_fixture.RISK, subsidy_policy=file_fixture.BUDGET, loss_policy=file_fixture.BUDGET,
                                      config_version=1, account_lock_root=tmp_path / 'locks', owner=file_owner if create else None,
                                      create_binding=create, clock=lambda: clock[0], fill_policy=SpotFillPolicy(5000),
                                      accounting_policy=accounting, recovery_policy=SpotFinancialRecoveryPolicy(5000), operator_keys=KEYS)

    store = await open_file(create=True)
    paid = payment.bundle.funding.payments[0]
    joint = JointCashSpot('file-joint', binding, 'c' * 64, now,
                          (JointCashEvent(f'JOINT:{paid.settled_at_ms}:FUNDING:{paid.bill_id}', paid.signed_payment_quote),),
                          D('5'), D('9.9'), True, True, 'okx_joint_reconciled')
    target, method = ((store._ledger, 'apply_joint_snapshot') if stage.startswith('wallet') else (store, '_publish_joint_cash'))
    original = getattr(target, method)

    def crash(*args, **kwargs):
        if stage == 'receipt' and not args[0].startswith('joint-done:'):
            return original(*args, **kwargs)
        if stage == 'wallet_after':
            original(*args, **kwargs)
        raise OSError('synthetic File saga interruption')

    with monkeypatch.context() as patches:
        patches.setattr(target, method, crash)
        with pytest.raises(CommitUnknown):
            await store.apply_joint_cash(joint, cut=(await store.snapshot(binding)).cut, owner=file_owner)
    await store.close()
    store = await open_file()
    now = await admin.fetchval('SELECT clock_timestamp()')
    clock[0] = now
    facts = SpotFinancialRecoveryFacts(binding, now, D('5'), D('9.9'), D('0'), D('0'), (), (), (), (), True, True, True)
    drain = OwnerTransition('file-drain', OwnerMode.DRAINING, file_owner)
    cut = (await store.snapshot(binding)).cut
    signature = KEYS.sign('operator', binding, 'DRAIN', drain.json(), cut, facts, issued_at=now, expires_at=now + timedelta(seconds=10))
    await store.transition_owner(drain, approval=signature, cut=cut, facts=facts)
    cut = (await store.snapshot(binding)).cut
    source = await store.legacy_applying_source(binding, facts=facts, cut=cut)
    retire = RetireLegacyApplying('file-retire', source.content_digest, cfg.binding, cfg.database_lineage)
    signature = KEYS.sign('operator', binding, 'IMPORT', retire.json(), cut, facts, issued_at=now, expires_at=now + timedelta(seconds=10))
    await store.retire_legacy_applying(retire, source=source, approval=signature)
    batch = PgJointBatchRepository(funding.swap)
    command = ImportLegacyApplying(source.content_digest, ApplyJointBatch('legacy-pending', (), (), (), payment))
    await admin.execute('UPDATE life_financial.accounts SET halted=true WHERE account_id=$1', cfg.account_id)
    cut = (await financial.observation_cut(cfg.binding)).cut
    now = await admin.fetchval('SELECT clock_timestamp()')
    facts = replace(facts, binding=cfg.binding, observed_at=now)
    approval = KEYS.sign('operator', cfg.binding, 'IMPORT', command.json(), cut, facts, issued_at=now, expires_at=now + timedelta(seconds=10))
    importer = PgLegacyApplyingImporter(admin, batch, verifier=KEYS.verifier(), source_verifier=KEYS.verifier(), max_fact_age_ms=5000)
    return values, batch, command, cut, facts, approval, importer, store, source


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['wallet_before', 'wallet_after', 'receipt'])
async def test_actual_interrupted_file_import_then_signed_recovery_books_original_bill_once(database, tmp_path, monkeypatch, stage):
    values, batch, command, cut, facts, approval, importer, store, source = await setup(database, tmp_path, monkeypatch, stage)
    financial, cfg, owner = values[:3]
    try:
        before_file = await store.snapshot(store.binding)
        assert ImportLegacyApplying.from_json(command.json()) == command
        holds = await values[5].claims(cfg.binding)
        assert not (await importer.import_pending(command, source_store=store, approval=approval, cut=cut, facts=facts)).trading_permission
        assert (await financial.balance(cfg.binding)).total_quote_usdt == 10
        assert (await batch.lookup(command.operation_id, cfg.binding)).state == 'RECOVERY_REQUIRED'
        assert await database[0].fetchval('SELECT halted FROM life_financial.accounts WHERE account_id=$1', cfg.account_id)
        assert (await importer.import_pending(command, source_store=None, approval=approval, cut=cut, facts=facts)).replayed
        assert (await importer.lookup(command.operation_id)).replayed
        assert await store.frozen_legacy_applying_source(store.binding) == source
        assert (await store.snapshot(store.binding)).balance == before_file.balance
        b = command.batch.funding.bundle
        now = await database[0].fetchval('SELECT clock_timestamp()')
        at = int(now.timestamp() * 1000)
        fresh = replace(b, account=replace(b.account, snapshot=replace(b.account.snapshot, sequence=3, observed_at_ms=at)),
                        mark=replace(b.mark, sequence=3, observed_at_ms=at), index=replace(b.index, sequence=3, observed_at_ms=at),
                        funding=replace(b.funding, sequence=3, observed_at_ms=at, coverage_through_ms=at), terms=replace(b.terms, sequence=3, observed_at_ms=at))
        replacement = replace(command.batch, operation_id='legacy-applied', funding=replace(command.batch.funding, operation_id='funding-applied', bundle=fresh))
        repair = ResolveJointSources('legacy-recover', command.operation_id, replacement)
        opening = await database[0].fetchval('SELECT floor(extract(epoch FROM opened_at)*1000)::bigint FROM life_financial.spot_capital_policies WHERE account_id=$1', cfg.account_id)
        account = replace(facts, observed_at=now, accepted_wire_ids=('SwapWire1',))
        recovery_facts = JointRepairFacts(account, (), ('777',), True, opening, at)
        repair_cut = (await financial.observation_cut(cfg.binding)).cut
        signed = KEYS.sign('operator', cfg.binding, 'RECOVER', repair.json(), repair_cut, recovery_facts,
                           issued_at=now, expires_at=now + timedelta(seconds=10))
        await PgJointRepairApprovalImporter(database[0], batch, verifier=KEYS.verifier(), max_fact_age_ms=5000).approve(
            repair, approval=signed, cut=repair_cut, facts=recovery_facts)
        repo = PgJointRepairRepository(batch)
        assert (await repo.resolve(repair, cut=repair_cut, owner=owner)).state == 'REPAIRED'
        assert (await repo.resolve(repair, cut=repair_cut, owner=owner)).replayed
        assert (await financial.balance(cfg.binding)).total_quote_usdt == D('9.9')
        assert (await batch.lookup(command.operation_id, cfg.binding)).state == 'RECOVERY_REQUIRED'
        assert await values[5].claims(cfg.binding) == holds
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.funding_cash_payments WHERE account_id=$1', cfg.account_id) == 1
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.cashflows WHERE account_id=$1', cfg.account_id) == 0
        assert (await importer.lookup(command.operation_id)).replayed
    finally:
        await store.close()
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['signature', 'role', 'liability', 'cash', 'incomplete', 'source_digest', 'bill', 'basis', 'target_active', 'missing_source', 'stale_cut', 'source_verifier'])
async def test_import_denials_cannot_publish_pending_or_adopt_partial_file_cash(database, tmp_path, monkeypatch, fault):
    values, batch, command, cut, facts, approval, importer, store, _ = await setup(database, tmp_path, monkeypatch)
    financial, cfg, _ = values[:3]
    try:
        if fault == 'signature':
            approval = replace(approval, signature='0' * 128)
        elif fault == 'role':
            approval = replace(approval, role='RECOVER')
        elif fault == 'liability':
            facts = replace(facts, liability_quote_usdt=D('1'))
        elif fault == 'cash':
            facts = replace(facts, account_quote_usdt=D('10'))
        elif fault == 'incomplete':
            facts = replace(facts, runner_scope_complete=False)
        elif fault == 'source_digest':
            command = replace(command, source_digest='d' * 64)
        elif fault == 'bill':
            b = command.batch.funding.bundle
            b = replace(b, funding=replace(b.funding, payments=(replace(b.funding.payments[0], bill_id='888'),)))
            command = replace(command, batch=replace(command.batch, funding=replace(command.batch.funding, bundle=b)))
        elif fault == 'basis':
            await database[0].execute('ALTER TABLE life_financial.spot_capital_policies DISABLE TRIGGER USER')
            await database[0].execute("UPDATE life_financial.spot_capital_policies SET opened_at=opened_at-interval '1 second' WHERE account_id=$1", cfg.account_id)
            await database[0].execute('ALTER TABLE life_financial.spot_capital_policies ENABLE TRIGGER USER')
            cut = (await financial.observation_cut(cfg.binding)).cut
        elif fault == 'target_active':
            await database[0].execute('UPDATE life_financial.accounts SET halted=false WHERE account_id=$1', cfg.account_id)
            cut = (await financial.observation_cut(cfg.binding)).cut
        elif fault == 'source_verifier':
            importer.source_verifier = OperatorKeys({'operator': b'x' * 32}, KEYS.roles).verifier()
        elif fault == 'stale_cut':
            cut = replace(cut, checkpoint=replace(cut.checkpoint, state_digest='d' * 64))
        if fault not in ('signature', 'role'):
            approval = KEYS.sign('operator', cfg.binding, 'IMPORT', command.json(), cut, facts,
                                 issued_at=approval.issued_at, expires_at=approval.expires_at)
        before = await financial.balance(cfg.binding)
        with pytest.raises(StorageError):
            await importer.import_pending(command, source_store=None if fault == 'missing_source' else store, approval=approval, cut=cut, facts=facts)
        assert await financial.balance(cfg.binding) == before
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.legacy_applying_imports WHERE account_id=$1', cfg.account_id) == 0
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.joint_financial_batches WHERE account_id=$1', cfg.account_id) == 0
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.funding_cash_payments WHERE account_id=$1', cfg.account_id) == 0
    finally:
        await store.close()
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['reply', 'backend'])
@pytest.mark.parametrize('boundary', ['source', 'pending'])
@pytest.mark.parametrize('committed', [False, True])
async def test_lost_import_commit_cold_original_id_never_duplicates_source_or_money(database, tmp_path, monkeypatch, boundary, committed, fault):
    import asyncpg

    from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.shared_capital import PgSharedCapitalRepository
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.swap import PgSwapRepository
    values, batch, command, cut, facts, approval, importer, store, _ = await setup(database, tmp_path, monkeypatch)
    financial, cfg, owner = values[:3]
    cold, connection = None, None
    admin = await asyncpg.connect(database[1])
    try:
        target = importer if boundary == 'source' else financial._runtime
        original = target._commit

        async def lost(tx):
            pid = tx._connection.get_server_pid()
            if committed:
                await original(tx)
            if fault == 'backend':
                assert await admin.fetchval('SELECT pg_terminate_backend($1)', pid)
            raise ConnectionError('synthetic import COMMIT reply lost')

        with monkeypatch.context() as patches:
            patches.setattr(target, '_commit', lost)
            with pytest.raises(CommitUnknown):
                await importer.import_pending(command, source_store=store, approval=approval, cut=cut, facts=facts)
        await financial.close()
        cold = PgSpotRepository(await PgRuntime.open(cfg))
        capital = PgSharedCapitalRepository(cold, owner=owner, observation=lambda: None, max_rows=64, max_bytes=1048576)
        restored_batch = PgJointBatchRepository(PgSwapRepository(capital))
        connection = await asyncpg.connect(database[2])
        restored = PgLegacyApplyingImporter(connection, restored_batch, verifier=KEYS.verifier(), source_verifier=KEYS.verifier(), max_fact_age_ms=5000)
        found = await restored.lookup(command.operation_id)
        assert (found is not None) is (boundary == 'pending' and committed)
        assert (await cold.balance(cfg.binding)).total_quote_usdt == 10
        receipt = await restored.import_pending(command, source_store=store, approval=approval, cut=cut, facts=facts)
        assert receipt.state == 'RECOVERY_REQUIRED' and receipt.replayed is (boundary == 'pending' and committed)
        assert (await restored.import_pending(command, source_store=None, approval=approval, cut=cut, facts=facts)).replayed
        assert await connection.fetchval('SELECT count(*) FROM life_financial.legacy_applying_imports WHERE account_id=$1', cfg.account_id) == 1
        assert await connection.fetchval('SELECT count(*) FROM life_financial.joint_financial_batches WHERE account_id=$1', cfg.account_id) == 1
        assert (await cold.balance(cfg.binding)).total_quote_usdt == 10
    finally:
        await store.close()
        await admin.close()
        if connection is not None:
            await connection.close()
        if cold is not None:
            await cold.close()
        await financial.close()


@pytest.mark.asyncio
async def test_missing_or_changed_import_proof_blocks_common_financial_history_and_release_readiness(database, tmp_path, monkeypatch):
    values, batch, command, cut, facts, approval, importer, store, _ = await setup(database, tmp_path, monkeypatch)
    financial, cfg, _ = values[:3]
    try:
        await importer.import_pending(command, source_store=store, approval=approval, cut=cut, facts=facts)
        await database[0].execute('ALTER TABLE life_financial.legacy_applying_imports DISABLE TRIGGER USER')
        await database[0].execute("UPDATE life_financial.legacy_applying_imports SET body=jsonb_set(body,'{payload,source_digest}',to_jsonb($2::text)) WHERE account_id=$1", cfg.account_id, 'd' * 64)
        await database[0].execute('ALTER TABLE life_financial.legacy_applying_imports ENABLE TRIGGER USER')
        with pytest.raises(StorageError, match='LEGACY_APPLYING_UNVERIFIED'):
            await importer.lookup(command.operation_id)
        with pytest.raises(StorageError, match='JOINT_BATCH_UNVERIFIED'):
            await batch.lookup(command.operation_id, cfg.binding)
        assert (await financial.snapshot(cfg.binding)).recovery_required
    finally:
        await store.close()
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['signature', 'key_policy', 'owner', 'expiry', 'bound', 'file_changed'])
async def test_physical_import_publication_rechecks_original_context_and_fails_closed(database, tmp_path, monkeypatch, fault):
    import asyncio
    values, batch, command, cut, facts, approval, importer, store, source = await setup(database, tmp_path, monkeypatch)
    financial, cfg, _ = values[:3]
    original = importer._commit
    committed_attempt = []
    try:
        if fault == 'signature':
            approval = replace(approval, signature='0' * 128)
        elif fault == 'key_policy':
            importer.verifier = OperatorKeys({'operator': b'x' * 32}, KEYS.roles).verifier()
        elif fault == 'bound':
            await database[0].execute('ALTER TABLE life_financial.legacy_applying_policies DISABLE TRIGGER USER')
            await database[0].execute('UPDATE life_financial.legacy_applying_policies SET max_total_bytes=1 WHERE account_id=$1', cfg.account_id)
            await database[0].execute('ALTER TABLE life_financial.legacy_applying_policies ENABLE TRIGGER USER')
        elif fault == 'file_changed':
            path = store._path.parent / 'legacy_applying_sources' / (source.content_digest + '.json')
            path.write_bytes(path.read_bytes() + b'\n')
        else:
            if fault == 'expiry':
                now = await database[0].fetchval('SELECT clock_timestamp()')
                facts = replace(facts, observed_at=now)
                approval = KEYS.sign('operator', cfg.binding, 'IMPORT', command.json(), cut, facts,
                                     issued_at=now, expires_at=now + timedelta(milliseconds=300))

            async def changed(tx):
                committed_attempt.append(True)
                if fault == 'owner':
                    await tx._connection.execute('UPDATE life_financial.accounts SET owner_generation=gen_random_uuid() WHERE account_id=$1', cfg.account_id)
                else:
                    await asyncio.sleep(.35)  # Real expiry; no clock/settings change.
                await original(tx)
            monkeypatch.setattr(importer, '_commit', changed)
        with pytest.raises(StorageError):
            await importer.import_pending(command, source_store=store, approval=approval, cut=cut, facts=facts)
        if fault in ('owner', 'expiry'):
            assert committed_attempt == [True]
        assert (await financial.balance(cfg.binding)).total_quote_usdt == 10
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.legacy_applying_imports WHERE account_id=$1', cfg.account_id) == 0
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.joint_financial_batches WHERE account_id=$1', cfg.account_id) == 0
    finally:
        await store.close()
        await financial.close()


@pytest.mark.asyncio
async def test_runtime_and_reporter_have_no_source_proof_or_import_approval_privileges(database, tmp_path, monkeypatch):
    values, _, _, _, _, _, _, store, _ = await setup(database, tmp_path, monkeypatch)
    financial = values[0]
    try:
        for role in ('life_pg2_runtime', 'life_pg2_reporter'):
            for table in ('legacy_applying_policies', 'legacy_applying_imports'):
                assert not await database[0].fetchval("SELECT has_table_privilege($1,$2,'SELECT,INSERT,UPDATE,DELETE')", role, 'life_financial.' + table)
            for function in ('legacy_applying_verified(uuid)', 'legacy_applying_fresh(uuid,text)', 'legacy_applying_commit_guard()'):
                assert not await database[0].fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", role, 'life_financial.' + function)
            assert await database[0].fetchval("SELECT has_function_privilege($1,'life_financial.legacy_applying_pending(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb)','EXECUTE')", role) is (role == 'life_pg2_runtime')
    finally:
        await store.close()
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('committed', [False, True])
async def test_actual_process_exit_around_pending_commit_preserves_original_bill_and_source(database, tmp_path, monkeypatch, committed):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.legacy_pending_process import exit_pending

    from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.shared_capital import PgSharedCapitalRepository
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.swap import PgSwapRepository
    values, _, command, cut, facts, approval, importer, store, source = await setup(database, tmp_path, monkeypatch)
    financial, cfg, owner = values[:3]
    cold = None
    try:
        async def capture():
            fresh_cut = (await financial.observation_cut(cfg.binding)).cut
            now = await database[0].fetchval('SELECT clock_timestamp()')
            fresh_facts = replace(facts, observed_at=now)
            signed = KEYS.sign('operator', cfg.binding, 'IMPORT', command.json(), fresh_cut, fresh_facts,
                               issued_at=now, expires_at=now + timedelta(seconds=10))
            await importer._approve(command, store, signed, fresh_cut, fresh_facts)
            return fresh_cut, fresh_facts, signed

        cut, facts, approval = await exit_pending(cfg, database[2], owner, command, KEYS.verifier(), committed, capture)
        await financial.close()
        cold = PgSpotRepository(await PgRuntime.open(cfg))
        capital = PgSharedCapitalRepository(cold, owner=owner, observation=lambda: None, max_rows=64, max_bytes=1048576)
        batch = PgJointBatchRepository(PgSwapRepository(capital))
        restored = PgLegacyApplyingImporter(database[0], batch, verifier=KEYS.verifier(), source_verifier=KEYS.verifier(), max_fact_age_ms=5000)
        assert ((await restored.lookup(command.operation_id)) is not None) is committed
        assert (await cold.balance(cfg.binding)).total_quote_usdt == 10
        receipt = await restored.import_pending(command, source_store=None, approval=approval, cut=cut, facts=facts)
        assert receipt.replayed is committed and receipt.state == 'RECOVERY_REQUIRED'
        assert await store.frozen_legacy_applying_source(store.binding) == source
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.funding_cash_payments WHERE account_id=$1', cfg.account_id) == 0
        assert len(await capital.claims(cfg.binding)) == 1
    finally:
        if cold is not None:
            await cold.close()
        await store.close()
        await financial.close()


@pytest.mark.asyncio
async def test_unpublished_import_cannot_be_bypassed_by_an_ordinary_financial_writer(database, tmp_path, monkeypatch):
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.funding import PgFundingRepository
    values, batch, command, cut, facts, approval, importer, store, _ = await setup(database, tmp_path, monkeypatch)
    financial, cfg, _ = values[:3]
    try:
        await importer._approve(command, store, approval, cut, facts)
        with pytest.raises(StorageError, match='LEGACY_APPLYING_PENDING_REQUIRED'):
            await batch.apply(command.batch, cut=cut, owner=batch.owner)
        with pytest.raises(StorageError, match='LEGACY_APPLYING_PENDING_REQUIRED'):
            await batch.apply(replace(command.batch, operation_id='bypass-import'), cut=cut, owner=batch.owner)
        with pytest.raises(StorageError, match='LEGACY_APPLYING_PENDING_REQUIRED'):
            await PgFundingRepository(batch.swap).apply_funding(command.batch.funding, cut=cut, owner=batch.owner)
        assert (await financial.balance(cfg.binding)).total_quote_usdt == 10
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.funding_cash_payments WHERE account_id=$1', cfg.account_id) == 0
        assert (await importer.import_pending(command, source_store=None, approval=approval, cut=cut, facts=facts)).state == 'RECOVERY_REQUIRED'
    finally:
        await store.close()
        await financial.close()
