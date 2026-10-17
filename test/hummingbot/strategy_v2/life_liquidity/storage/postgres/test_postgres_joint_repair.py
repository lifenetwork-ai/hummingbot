"""Signed source-only resolution retains raw pending diagnostics and HALT."""
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_joint_batch import setup as batch_setup

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown, StorageError, StorageUnavailable
from hummingbot.strategy_v2.life_liquidity.storage.control_plane import OperatorKeys
from hummingbot.strategy_v2.life_liquidity.storage.postgres.joint_repair import (
    JointRepairFacts,
    PgJointRepairRepository,
    ResolveJointSources,
)
from hummingbot.strategy_v2.life_liquidity.storage.postgres.joint_repair_approval import PgJointRepairApprovalImporter
from hummingbot.strategy_v2.life_liquidity.storage.recovery import SpotFinancialRecoveryFacts


async def setup(database, *, extra_pending=False, max_total_bytes=1048576):
    keys = OperatorKeys({'operator': b'r' * 32}, {'operator': frozenset(('RECOVER',))})  # Synthetic test key only.
    values, batch, original = await batch_setup(database, carry_options=dict(refresh_initial_facts=True),
                                                spot_options=dict(configuration_body=lambda _: {'operator_verifier': keys.verifier().policy()}))
    financial, cfg, owner = values[:3]
    await database[0].execute('INSERT INTO life_financial.joint_repair_policies VALUES($1,$2,64,64,$3,5000)', cfg.account_id, cfg.binding.policy_hash, max_total_bytes)
    b = original.funding.bundle
    bad = replace(original, funding=replace(original.funding, bundle=replace(b, account=replace(b.account, snapshot=replace(b.account.snapshot, spot_cash_quote=D('11'))))))
    assert (await batch.apply(bad, cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)).state == 'RECOVERY_REQUIRED'
    if extra_pending:
        assert (await batch.apply(replace(bad, operation_id='also-pending'), cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)).state == 'RECOVERY_REQUIRED'
    now = await database[0].fetchval('SELECT clock_timestamp()')
    at = int(now.timestamp() * 1000)
    b = replace(b, account=replace(b.account, snapshot=replace(b.account.snapshot, sequence=3, observed_at_ms=at)),
                mark=replace(b.mark, sequence=3, observed_at_ms=at), index=replace(b.index, sequence=3, observed_at_ms=at),
                funding=replace(b.funding, sequence=3, observed_at_ms=at, coverage_through_ms=at), terms=replace(b.terms, sequence=3, observed_at_ms=at))
    replacement = replace(original, operation_id='batch-repaired', funding=replace(original.funding, operation_id='funding-repaired', bundle=b))
    command = ResolveJointSources('repair1', bad.operation_id, replacement)
    account = SpotFinancialRecoveryFacts(cfg.binding, now, D('5'), D('9.89'), D('0'), D('0'), ('trade1',), (), ('SwapWire1',), (), True, True, True)
    opening = await database[0].fetchval('SELECT floor(extract(epoch FROM opened_at)*1000)::bigint FROM life_financial.spot_capital_policies')
    facts = JointRepairFacts(account, (('SWAP', 'trade1'),), ('777',), True, opening, at)
    cut = (await financial.observation_cut(cfg.binding)).cut
    approval = keys.sign('operator', cfg.binding, 'RECOVER', command.json(), cut, facts,
                         issued_at=now - timedelta(seconds=1), expires_at=now + timedelta(seconds=10))
    importer = PgJointRepairApprovalImporter(database[0], batch, verifier=keys.verifier(), max_fact_age_ms=5000)
    return values, batch, bad, command, facts, cut, approval, importer


@pytest.mark.asyncio
async def test_signed_source_resolution_applies_actual_economics_once_keeps_raw_pending_and_never_activates(database):
    values, batch, bad, command, facts, cut, approval, importer = await setup(database)
    financial, cfg, owner = values[:3]
    try:
        assert ResolveJointSources.from_json(command.json()) == command
        assert JointRepairFacts.from_json(facts.json()) == facts
        assert not (await importer.approve(command, approval=approval, cut=cut, facts=facts)).trading_permission
        assert (await financial.balance(cfg.binding)).total_quote_usdt == 10
        assert (await batch.lookup(bad.operation_id, cfg.binding)).state == 'RECOVERY_REQUIRED'
        assert (await financial.observation_cut(cfg.binding)).cut == cut  # Unconsumed approval is not financial publication.
        repo = PgJointRepairRepository(batch)
        result = await repo.resolve(command, cut=cut, owner=owner)
        assert result.state == 'REPAIRED' and not result.trading_permission
        assert (await repo.resolve(command, cut=cut, owner=owner)).replayed
        assert (await repo.lookup(command.operation_id, cfg.binding)).replayed
        assert (await batch.lookup(bad.operation_id, cfg.binding)).state == 'RECOVERY_REQUIRED'  # Original history never changes.
        assert (await batch.lookup(command.replacement.operation_id, cfg.binding)).state == 'APPLIED'
        assert (await financial.balance(cfg.binding)).total_quote_usdt == D('9.89')
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.swap_fill_facts') == 1
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.funding_cash_payments') == 1
        assert await database[0].fetchval("SELECT incurred FROM life_financial.budget_buckets WHERE family='LOSS' AND scope='SESSION'") == D('.01')
        assert await database[0].fetchval('SELECT halted FROM life_financial.accounts')
        assert len(await values[5].claims(cfg.binding)) == 1
        assert not (await repo.frame(cfg.binding))['pending']
        assert (await importer.lookup(command.operation_id)).replayed
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['signature', 'role', 'scope', 'liability', 'cash', 'coverage', 'extra_trade', 'missing_bill', 'fill', 'funding_bill'])
async def test_invalid_signed_source_profile_changes_no_money_or_approval(database, fault):
    values, batch, bad, command, facts, cut, approval, importer = await setup(database)
    financial, cfg, owner = values[:3]
    try:
        if fault == 'scope':
            facts = replace(facts, swap_scope_complete=False)
        elif fault == 'liability':
            facts = replace(facts, account=replace(facts.account, liability_quote_usdt=D('1')))
        elif fault == 'cash':
            facts = replace(facts, account=replace(facts.account, account_quote_usdt=D('10')))
        elif fault == 'coverage':
            facts = replace(facts, coverage_from_ms=facts.coverage_through_ms)
        elif fault == 'extra_trade':
            facts = replace(facts, account=replace(facts.account, account_trade_ids=('trade1', 'foreign')),
                            product_trade_ids=(('SWAP', 'trade1'), ('SWAP', 'foreign')))
        elif fault == 'missing_bill':
            facts = replace(facts, funding_bill_ids=())
        elif fault == 'fill':
            fill = command.replacement.swap_fills[0]
            command = replace(command, replacement=replace(command.replacement, swap_fills=(replace(fill, fills=(replace(fill.fills[0], signed_fee=D('0')),)),)))
        elif fault == 'funding_bill':
            b = command.replacement.funding.bundle
            b = replace(b, funding=replace(b.funding, payments=(replace(b.funding.payments[0], signed_payment_quote=D('0')),)))
            command = replace(command, replacement=replace(command.replacement, funding=replace(command.replacement.funding, bundle=b)))
        if fault not in ('signature', 'role'):
            keys = OperatorKeys({'operator': b'r' * 32}, {'operator': frozenset(('RECOVER',))})
            approval = keys.sign('operator', cfg.binding, 'RECOVER', command.json(), cut, facts,
                                 issued_at=approval.issued_at, expires_at=approval.expires_at)
        elif fault == 'signature':
            approval = replace(approval, signature='00' * 64)
        else:
            approval = replace(approval, role='ACTIVATE')
        with pytest.raises(StorageError, match='(OPERATOR_APPROVAL|PG_JOINT_REPAIR_IMPORT_PROFILE)'):
            await importer.approve(command, approval=approval, cut=cut, facts=facts)
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.joint_repair_approvals') == 0
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.swap_fill_facts') == 0
        assert (await financial.balance(cfg.binding)).total_quote_usdt == 10
        assert (await batch.lookup(bad.operation_id, cfg.binding)).state == 'RECOVERY_REQUIRED'
        assert await database[0].fetchval('SELECT halted FROM life_financial.accounts')
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_unapproved_repair_and_changed_approved_command_are_denied(database):
    values, batch, bad, command, facts, cut, approval, importer = await setup(database)
    financial, cfg, owner = values[:3]
    repo = PgJointRepairRepository(batch)
    try:
        with pytest.raises(StorageError, match='PG_JOINT_REPAIR_APPROVAL_REQUIRED'):
            await repo.resolve(command, cut=cut, owner=owner)
        await importer.approve(command, approval=approval, cut=cut, facts=facts)
        assert (await importer.approve(command, approval=approval, cut=cut, facts=facts)).replayed
        altered = replace(command, replacement=replace(command.replacement, operation_id='another-root'))
        with pytest.raises(StorageError, match='PG_JOINT_REPAIR_APPROVAL_STALE'):
            await repo.resolve(altered, cut=cut, owner=owner)
        assert await repo.lookup(command.operation_id, cfg.binding) is None
        assert (await financial.balance(cfg.binding)).total_quote_usdt == 10
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_one_repair_never_clears_another_retained_parent(database):
    values, batch, bad, command, facts, cut, approval, importer = await setup(database, extra_pending=True)
    financial, cfg, owner = values[:3]
    try:
        await importer.approve(command, approval=approval, cut=cut, facts=facts)
        repo = PgJointRepairRepository(batch)
        await repo.resolve(command, cut=cut, owner=owner)
        assert (await repo.frame(cfg.binding))['pending']
        assert (await batch.lookup('also-pending', cfg.binding)).state == 'RECOVERY_REQUIRED'
        assert (await financial.balance(cfg.binding)).total_quote_usdt == D('9.89')
        assert (await financial.snapshot(cfg.binding)).recovery_required
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_consumed_repair_cannot_reuse_the_retired_original_funding_identity(database):
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.funding import PgFundingRepository
    values, batch, bad, command, facts, cut, approval, importer = await setup(database)
    financial, cfg, owner = values[:3]
    try:
        await importer.approve(command, approval=approval, cut=cut, facts=facts)
        await PgJointRepairRepository(batch).resolve(command, cut=cut, owner=owner)
        old_id = replace(command.replacement.funding, operation_id=bad.funding.operation_id)
        with pytest.raises(StorageError, match='PG_JOINT_MEMBER_ID_RETIRED'):
            await PgFundingRepository(batch.swap).apply_funding(old_id, cut=(await financial.observation_cut(cfg.binding)).cut, owner=owner)
        assert (await financial.balance(cfg.binding)).total_quote_usdt == D('9.89')
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.funding_cash_payments') == 1
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['approval', 'application'])
async def test_expiry_at_physical_commit_rolls_back_without_erasing_pending(database, monkeypatch, stage):
    import asyncio
    values, batch, bad, command, facts, cut, approval, importer = await setup(database)
    financial, cfg, owner = values[:3]
    now = await database[0].fetchval('SELECT clock_timestamp()')
    keys = OperatorKeys({'operator': b'r' * 32}, {'operator': frozenset(('RECOVER',))})
    approval = keys.sign('operator', cfg.binding, 'RECOVER', command.json(), cut, facts,
                         issued_at=now - timedelta(seconds=1), expires_at=now + timedelta(seconds=2))
    target = importer if stage == 'approval' else batch.runtime
    original = target._commit
    try:
        if stage == 'application':
            await importer.approve(command, approval=approval, cut=cut, facts=facts)

        async def expired(tx):
            c = tx._connection
            if stage == 'approval':
                assert await c.fetchval('SELECT count(*) FROM life_financial.joint_repair_approvals') == 1
            else:
                assert await c.fetchval("SELECT receipt->>'state' FROM life_financial.operations WHERE operation_id='repair1'") == 'REPAIRED'
            at = await c.fetchval('SELECT clock_timestamp()')
            await asyncio.sleep(max(0, (approval.expires_at - at).total_seconds()) + .05)
            await original(tx)

        monkeypatch.setattr(target, '_commit', expired)
        with pytest.raises(StorageError, match='PG_JOINT_REPAIR_APPROVAL_EXPIRED_AT_COMMIT'):
            if stage == 'approval':
                await importer.approve(command, approval=approval, cut=cut, facts=facts)
            else:
                await PgJointRepairRepository(batch).resolve(command, cut=cut, owner=owner)
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.joint_repair_facts') == 0
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.joint_repair_staging') == 0
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.swap_fill_facts') == 0
        assert (await financial.balance(cfg.binding)).total_quote_usdt == 10
        assert (await batch.lookup(bad.operation_id, cfg.binding)).state == 'RECOVERY_REQUIRED'
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('committed', [False, True])
async def test_ambiguous_repair_requires_original_readback_not_another_identity(database, monkeypatch, committed):
    values, batch, bad, command, facts, cut, approval, importer = await setup(database)
    financial, cfg, owner = values[:3]
    original = batch.runtime._commit
    repo = PgJointRepairRepository(batch)
    try:
        await importer.approve(command, approval=approval, cut=cut, facts=facts)

        async def uncertain(tx):
            if committed:
                await original(tx)
            raise ConnectionError('synthetic lost COMMIT result')

        monkeypatch.setattr(batch.runtime, '_commit', uncertain)
        with pytest.raises(CommitUnknown):
            await repo.resolve(command, cut=cut, owner=owner)
        assert not batch.runtime.healthy
        found = await repo.lookup(command.operation_id, cfg.binding)
        assert (found is not None) is committed
        assert (await financial.balance(cfg.binding)).total_quote_usdt == (D('9.89') if committed else 10)
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.swap_fill_facts') == int(committed)
        assert (await batch.lookup(bad.operation_id, cfg.binding)).state == 'RECOVERY_REQUIRED'
        assert await database[0].fetchval('SELECT halted FROM life_financial.accounts')
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_runtime_reporter_cannot_import_approval_or_read_private_repair_capability(database):
    import asyncpg
    values, batch, bad, command, facts, cut, approval, importer = await setup(database)
    financial, cfg, owner = values[:3]
    try:
        for principal in ('life_pg2_runtime', 'life_pg2_reporter'):
            c = await asyncpg.connect(database[1].replace('postgres@', principal + '@'))
            try:
                for query in ('SELECT * FROM life_financial.joint_repair_staging',
                              'INSERT INTO life_financial.joint_repair_approvals DEFAULT VALUES',
                              'SELECT life_financial.joint_repair_profile(NULL,NULL,NULL,NULL)',
                              'SELECT life_financial.joint_repair_approval_integrity(NULL)'):
                    with pytest.raises(asyncpg.InsufficientPrivilegeError):
                        await c.execute(query)
                if principal.endswith('reporter'):
                    with pytest.raises(asyncpg.InsufficientPrivilegeError):
                        await c.execute('SELECT life_financial.joint_repair_command(NULL,NULL,NULL,NULL,NULL,NULL,NULL)')
                else:
                    denied = PgJointRepairApprovalImporter(c, batch, verifier=importer.verifier, max_fact_age_ms=5000)
                    with pytest.raises(StorageError, match='PG_JOINT_REPAIR_IMPORT_OWNER_REQUIRED'):
                        await denied.approve(command, approval=approval, cut=cut, facts=facts)
            finally:
                await c.close()
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_signed_but_economically_wrong_replacement_rolls_back_every_child(database):
    values, batch, bad, command, facts, cut, approval, importer = await setup(database)
    financial, cfg, owner = values[:3]
    try:
        b = command.replacement.funding.bundle
        b = replace(b, account=replace(b.account, snapshot=replace(b.account.snapshot, spot_cash_quote=D('11'))))
        command = replace(command, replacement=replace(command.replacement, funding=replace(command.replacement.funding, bundle=b)))
        facts = replace(facts, account=replace(facts.account, account_quote_usdt=D('11')))
        keys = OperatorKeys({'operator': b'r' * 32}, {'operator': frozenset(('RECOVER',))})
        approval = keys.sign('operator', cfg.binding, 'RECOVER', command.json(), cut, facts,
                             issued_at=approval.issued_at, expires_at=approval.expires_at)
        await importer.approve(command, approval=approval, cut=cut, facts=facts)
        with pytest.raises(StorageError, match='PG_JOINT_REPAIR_REPLACEMENT_REJECTED'):
            await PgJointRepairRepository(batch).resolve(command, cut=cut, owner=owner)
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.joint_financial_batches') == 1
        for table in ('joint_repair_facts', 'joint_repair_staging', 'joint_batch_staging', 'swap_fill_facts', 'funding_cash_payments', 'budget_events'):
            assert await database[0].fetchval('SELECT count(*) FROM life_financial.' + table) == 0
        assert (await financial.balance(cfg.binding)).total_quote_usdt == 10
        assert (await importer.lookup(command.operation_id)).replayed
        assert (await batch.lookup(bad.operation_id, cfg.binding)).state == 'RECOVERY_REQUIRED'
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_equivalent_second_pending_parent_can_be_signed_separately_without_double_money(database):
    values, batch, bad, command, facts, cut, approval, importer = await setup(database, extra_pending=True)
    financial, cfg, owner = values[:3]
    repo = PgJointRepairRepository(batch)
    try:
        await importer.approve(command, approval=approval, cut=cut, facts=facts)
        await repo.resolve(command, cut=cut, owner=owner)
        replacement = replace(command.replacement, operation_id='second-replacement', funding=replace(command.replacement.funding, operation_id='second-funding'))
        second = ResolveJointSources('second-repair', 'also-pending', replacement)
        new_cut = (await financial.observation_cut(cfg.binding)).cut
        keys = OperatorKeys({'operator': b'r' * 32}, {'operator': frozenset(('RECOVER',))})
        signature = keys.sign('operator', cfg.binding, 'RECOVER', second.json(), new_cut, facts,
                              issued_at=approval.issued_at, expires_at=approval.expires_at)
        await importer.approve(second, approval=signature, cut=new_cut, facts=facts)
        assert (await repo.resolve(second, cut=new_cut, owner=owner)).state == 'REPAIRED'
        assert not (await repo.frame(cfg.binding))['pending']
        assert (await repo.lookup(command.operation_id, cfg.binding)).replayed
        assert (await financial.balance(cfg.binding)).total_quote_usdt == D('9.89')
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.swap_fill_facts') == 1
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.funding_cash_payments') == 1
        assert await database[0].fetchval('SELECT halted FROM life_financial.accounts')
        assert (await batch.lookup('also-pending', cfg.binding)).state == 'RECOVERY_REQUIRED'
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('target', ['approval', 'receipt'])
async def test_corrupted_repair_history_denies_readback_and_new_publication(database, target):
    values, batch, bad, command, facts, cut, approval, importer = await setup(database)
    financial, cfg, owner = values[:3]
    try:
        await importer.approve(command, approval=approval, cut=cut, facts=facts)
        repo = PgJointRepairRepository(batch)
        await repo.resolve(command, cut=cut, owner=owner)
        table = 'joint_repair_approvals' if target == 'approval' else 'operations'
        await database[0].execute('ALTER TABLE life_financial.' + table + ' DISABLE TRIGGER USER')
        if target == 'approval':
            await database[0].execute("UPDATE life_financial.joint_repair_approvals SET source_hash=repeat('f',64)")
        else:
            await database[0].execute("UPDATE life_financial.operations SET receipt=jsonb_set(receipt,'{trading_permission}','true') WHERE operation_id='repair1'")
        await database[0].execute('ALTER TABLE life_financial.' + table + ' ENABLE TRIGGER USER')
        with pytest.raises(StorageError, match='PG_JOINT_REPAIR_UNVERIFIED'):
            await repo.lookup(command.operation_id, cfg.binding)
        assert await database[0].fetchval("SELECT total FROM life_financial.balances WHERE asset='USDT'") == D('9.89')
        assert await database[0].fetchval('SELECT halted FROM life_financial.accounts')
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['process', 'backend'])
@pytest.mark.parametrize('committed', [False, True])
async def test_actual_crash_keeps_signed_repair_original_identity_and_every_financial_member(database, monkeypatch, fault, committed):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.joint_repair_process import exit_joint_repair

    import asyncpg

    from hummingbot.strategy_v2.life_liquidity.storage.postgres.joint_batch import PgJointBatchRepository
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.shared_capital import PgSharedCapitalRepository
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.swap import PgSwapRepository
    values, batch, bad, command, facts, cut, approval, importer = await setup(database)
    financial, cfg, owner = values[:3]
    admin, cold = await asyncpg.connect(database[1]), None
    try:
        await importer.approve(command, approval=approval, cut=cut, facts=facts)
        if fault == 'process':
            await exit_joint_repair(cfg, command, cut, owner, committed)
        else:
            original = batch.runtime._commit

            async def killed(tx):
                assert await tx._connection.fetchval("SELECT receipt->>'state' FROM life_financial.operations WHERE operation_id='repair1'") == 'REPAIRED'
                if committed:
                    await original(tx)
                assert await admin.fetchval('SELECT pg_terminate_backend($1)', tx._connection.get_server_pid())
                if not committed:
                    await original(tx)
                raise ConnectionError('synthetic lost terminated COMMIT result')

            monkeypatch.setattr(batch.runtime, '_commit', killed)
            with pytest.raises(CommitUnknown):
                await PgJointRepairRepository(batch).resolve(command, cut=cut, owner=owner)
        cold = PgSpotRepository(await PgRuntime.open(cfg))
        capital = PgSharedCapitalRepository(cold, owner=owner, observation=lambda: None, max_rows=64, max_bytes=1048576)
        recovered = PgJointRepairRepository(PgJointBatchRepository(PgSwapRepository(capital)))
        found = await recovered.lookup(command.operation_id, cfg.binding)
        assert (found is not None) is committed
        if committed:
            assert found.replayed and not found.trading_permission
            assert (await recovered.resolve(command, cut=cut, owner=owner)).replayed
        assert (await cold.balance(cfg.binding)).total_quote_usdt == (D('9.89') if committed else 10)
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.swap_fill_facts') == int(committed)
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.funding_cash_payments') == int(committed)
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.joint_repair_facts') == int(committed)
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.joint_repair_staging') == 0
        assert await database[0].fetchval('SELECT halted FROM life_financial.accounts')
        assert (await recovered.batch.lookup(bad.operation_id, cfg.binding)).state == 'RECOVERY_REQUIRED'
        assert len(await capital.claims(cfg.binding)) == 1
    finally:
        try:
            try:
                await financial.close()
            except StorageUnavailable as error:
                # A deliberately severed COMMIT can leave protocol cleanup
                # outstanding beyond the unchanged bounded shutdown deadline.
                # The runtime must terminate every pool, retain revocation and
                # explicitly require readback; it cannot report clean shutdown.
                if fault != 'backend' or str(error) != 'PG_SHUTDOWN_READBACK_REQUIRED':
                    raise
                assert batch.runtime._closing and not batch.runtime.healthy
                assert not batch.runtime._tasks and not any(batch.runtime._counts.values())
                assert batch.runtime._close_task.done()
                assert all(pool._closed for pool in batch.runtime._pools.values())
                assert await database[0].fetchval('SELECT count(*) FROM life_financial.joint_repair_facts') == int(committed)
                assert await database[0].fetchval("SELECT total FROM life_financial.balances WHERE asset='USDT'") == (D('9.89') if committed else 10)
        finally:
            if cold is not None:
                await cold.close()
            await admin.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['admission', 'commit'])
async def test_clock_before_retained_approval_capture_cannot_publish_a_repair(database, monkeypatch, stage):
    values, batch, bad, command, facts, cut, approval, importer = await setup(database)
    financial, cfg, owner = values[:3]
    try:
        await importer.approve(command, approval=approval, cut=cut, facts=facts)
        # Test-only fault models the primary clock falling behind a previously
        # retained capture. It changes no OS/DB clock and grants no production API.
        await database[0].execute('''CREATE FUNCTION life_financial.test_future_repair_capture(p_account uuid) RETURNS void
            LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$ BEGIN
                ALTER TABLE life_financial.joint_repair_approvals DISABLE TRIGGER immutable_history;
                UPDATE life_financial.joint_repair_approvals SET captured_at=clock_timestamp()+interval '1 second' WHERE account_id=p_account;
                ALTER TABLE life_financial.joint_repair_approvals ENABLE TRIGGER immutable_history;
            END; $$;
            REVOKE ALL ON FUNCTION life_financial.test_future_repair_capture(uuid) FROM PUBLIC;
            GRANT EXECUTE ON FUNCTION life_financial.test_future_repair_capture(uuid) TO life_pg2_runtime''')
        original = batch.runtime._commit
        if stage == 'admission':
            await database[0].execute('SELECT life_financial.test_future_repair_capture($1)', cfg.account_id)
        else:
            async def changed(tx):
                assert await tx._connection.fetchval("SELECT receipt->>'state' FROM life_financial.operations WHERE operation_id='repair1'") == 'REPAIRED'
                await tx._connection.execute('SELECT life_financial.test_future_repair_capture($1)', cfg.account_id)
                await original(tx)

            monkeypatch.setattr(batch.runtime, '_commit', changed)
        with pytest.raises(StorageError, match='PG_JOINT_REPAIR_APPROVAL_(STALE|EXPIRED_AT_COMMIT)'):
            await PgJointRepairRepository(batch).resolve(command, cut=cut, owner=owner)
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.joint_repair_facts') == 0
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.swap_fill_facts') == 0
        assert (await financial.balance(cfg.binding)).total_quote_usdt == 10
        assert (await batch.lookup(bad.operation_id, cfg.binding)).state == 'RECOVERY_REQUIRED'
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_original_owner_change_at_repair_commit_rolls_back_every_member(database, monkeypatch):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.helpers import install_generation_fault
    values, batch, bad, command, facts, cut, approval, importer = await setup(database)
    financial, cfg, owner = values[:3]
    original = batch.runtime._commit
    try:
        await importer.approve(command, approval=approval, cut=cut, facts=facts)
        await install_generation_fault(database)

        async def changed(tx):
            assert await tx._connection.fetchval("SELECT receipt->>'state' FROM life_financial.operations WHERE operation_id='repair1'") == 'REPAIRED'
            await tx._connection.execute('SELECT life_financial.test_change_generation($1)', cfg.account_id)
            await original(tx)

        monkeypatch.setattr(batch.runtime, '_commit', changed)
        with pytest.raises(StorageError, match='PG_OWNER_EXPIRED_OR_CHANGED_AT_COMMIT'):
            await PgJointRepairRepository(batch).resolve(command, cut=cut, owner=owner)
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.joint_repair_facts') == 0
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.swap_fill_facts') == 0
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.funding_cash_payments') == 0
        assert (await financial.balance(cfg.binding)).total_quote_usdt == 10
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_changed_local_receipt_binding_after_commit_requires_original_readback(database, monkeypatch):
    values, batch, bad, command, facts, cut, approval, importer = await setup(database)
    financial, cfg, owner = values[:3]
    original = batch.runtime._commit
    repo = PgJointRepairRepository(batch)
    try:
        await importer.approve(command, approval=approval, cut=cut, facts=facts)

        async def changed(tx):
            await original(tx)
            repo.binding = replace(cfg.binding, config_hash='f' * 64)

        monkeypatch.setattr(batch.runtime, '_commit', changed)
        with pytest.raises(CommitUnknown):
            await repo.resolve(command, cut=cut, owner=owner)
        assert not batch.runtime.healthy
        restored = PgJointRepairRepository(batch)
        assert (await restored.lookup(command.operation_id, cfg.binding)).replayed
        assert (await financial.balance(cfg.binding)).total_quote_usdt == D('9.89')
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_approval_retention_bound_denies_without_publishing_or_dropping_raw_pending(database):
    values, batch, bad, command, facts, cut, approval, importer = await setup(database, max_total_bytes=1)
    financial, cfg, owner = values[:3]
    try:
        with pytest.raises(StorageError, match='PG_JOINT_REPAIR_BOUND'):
            await importer.approve(command, approval=approval, cut=cut, facts=facts)
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.joint_repair_approvals') == 0
        assert (await batch.lookup(bad.operation_id, cfg.binding)).state == 'RECOVERY_REQUIRED'
        assert (await financial.balance(cfg.binding)).total_quote_usdt == 10
        assert await database[0].fetchval('SELECT halted FROM life_financial.accounts')
    finally:
        await financial.close()
