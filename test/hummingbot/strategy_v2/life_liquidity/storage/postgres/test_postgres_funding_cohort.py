"""An explicit signed cohort resolves all selected raw funding facts atomically."""
from dataclasses import replace
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_funding_repair import setup

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
from hummingbot.strategy_v2.life_liquidity.storage.postgres.joint_repair import (
    PgJointRepairRepository,
    ResolveJointSources,
)


@pytest.mark.asyncio
async def test_signed_funding_cohort_resolves_both_originals_and_debits_one_bill(database):
    values, funding, bad, command, facts, cut, approval, importer, keys = await setup(database, extra_pending=True, max_members=2)
    financial, cfg, owner = values[:3]
    try:
        command = replace(command, additional_pending_operation_ids=('another-pending',))
        assert ResolveJointSources.from_json(command.json()) == command
        approval = keys.sign('operator', cfg.binding, 'RECOVER', command.json(), cut, facts,
                             issued_at=approval.issued_at, expires_at=approval.expires_at)
        await importer.approve(command, approval=approval, cut=cut, facts=facts)
        repo = PgJointRepairRepository(importer.batch)
        result = await repo.resolve(command, cut=cut, owner=owner)
        assert result.state == 'REPAIRED' and result.additional_pending_operation_ids == ('another-pending',)
        assert not result.trading_permission
        assert (await repo.resolve(command, cut=cut, owner=owner)).replayed
        assert not (await repo.frame(cfg.binding))['pending']
        assert len((await funding.frame(cfg.binding))['pending']) == 2
        assert (await funding.lookup(bad.operation_id, cfg.binding)).state == 'RECOVERY_REQUIRED'
        assert (await funding.lookup('another-pending', cfg.binding)).state == 'RECOVERY_REQUIRED'
        assert (await financial.balance(cfg.binding)).total_quote_usdt == D('9.9')
        assert (await repo.lookup(command.operation_id, cfg.binding)).additional_pending_operation_ids == ('another-pending',)
        assert (await importer.lookup(command.operation_id)).replayed
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.funding_cash_payments') == 1
        assert await database[0].fetchval('SELECT halted FROM life_financial.accounts')
        assert len(await values[5].claims(cfg.binding)) == 1
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_four_selected_origins_share_one_unchanged_bill_and_explicit_member_limit(database):
    values, funding, bad, command, facts, cut, approval, importer, keys = await setup(database, extra_pending=True, max_members=4)
    financial, cfg, owner = values[:3]
    try:
        for operation_id in ('extra-b', 'extra-a'):
            assert (await funding.apply_funding(replace(bad, operation_id=operation_id),
                                                cut=(await financial.observation_cut(cfg.binding)).cut, owner=owner)).state == 'RECOVERY_REQUIRED'
        now = await database[0].fetchval('SELECT clock_timestamp()')
        at = int(now.timestamp() * 1000)
        b = command.replacement.funding.bundle
        b = replace(b, account=replace(b.account, snapshot=replace(b.account.snapshot, sequence=4, observed_at_ms=at)),
                    mark=replace(b.mark, sequence=4, observed_at_ms=at), index=replace(b.index, sequence=4, observed_at_ms=at),
                    funding=replace(b.funding, sequence=4, observed_at_ms=at, coverage_through_ms=at), terms=replace(b.terms, sequence=4, observed_at_ms=at))
        command = replace(command, additional_pending_operation_ids=('extra-b', 'another-pending', 'extra-a'),
                          replacement=replace(command.replacement, funding=replace(command.replacement.funding, bundle=b)))
        facts = replace(facts, account=replace(facts.account, observed_at=now), coverage_through_ms=at)
        cut = (await financial.observation_cut(cfg.binding)).cut
        approval = keys.sign('operator', cfg.binding, 'RECOVER', command.json(), cut, facts,
                             issued_at=approval.issued_at, expires_at=approval.expires_at)
        await importer.approve(command, approval=approval, cut=cut, facts=facts)
        repo = PgJointRepairRepository(importer.batch)
        result = await repo.resolve(command, cut=cut, owner=owner)
        assert result.additional_pending_operation_ids == ('another-pending', 'extra-a', 'extra-b')
        assert not (await repo.frame(cfg.binding))['pending']
        assert (await financial.balance(cfg.binding)).total_quote_usdt == D('9.9')
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.joint_repair_members') == 3
        assert len((await funding.frame(cfg.binding))['pending']) == 4
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_consumed_secondary_identity_cannot_be_repaired_again_as_single_parent(database):
    values, funding, bad, command, facts, cut, approval, importer, keys = await setup(database, extra_pending=True)
    financial, cfg, owner = values[:3]
    try:
        command = replace(command, additional_pending_operation_ids=('another-pending',))
        signature = keys.sign('operator', cfg.binding, 'RECOVER', command.json(), cut, facts,
                              issued_at=approval.issued_at, expires_at=approval.expires_at)
        await importer.approve(command, approval=signature, cut=cut, facts=facts)
        repo = PgJointRepairRepository(importer.batch)
        await repo.resolve(command, cut=cut, owner=owner)
        new_cut = (await financial.observation_cut(cfg.binding)).cut
        second = replace(command, operation_id='another-repair', pending_operation_id='another-pending', additional_pending_operation_ids=(),
                         replacement=replace(command.replacement, operation_id='another-batch',
                                             funding=replace(command.replacement.funding, operation_id='another-funding')))
        signature = keys.sign('operator', cfg.binding, 'RECOVER', second.json(), new_cut, facts,
                              issued_at=approval.issued_at, expires_at=approval.expires_at)
        await importer.approve(second, approval=signature, cut=new_cut, facts=facts)
        with pytest.raises(StorageError, match='PG_JOINT_REPAIR_APPROVAL_STALE'):
            await repo.resolve(second, cut=new_cut, owner=owner)
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.funding_cash_payments') == 1
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.joint_repair_facts') == 1
        assert (await financial.balance(cfg.binding)).total_quote_usdt == D('9.9')
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_cohort_includes_distinct_debit_and_credit_without_netting_gross_cost(database):
    values, funding, bad, command, facts, cut, approval, importer, keys = await setup(database)
    financial, cfg, owner = values[:3]
    try:
        b = bad.bundle
        credit = replace(b.funding.payments[0], bill_id='888', signed_payment_quote=D('.2'))
        other = replace(bad, operation_id='another-pending', bundle=replace(b,
                        account=replace(b.account, included_funding_bill_ids=('888',)), funding=replace(b.funding, payments=(credit,))))
        assert (await funding.apply_funding(other, cut=(await financial.observation_cut(cfg.binding)).cut, owner=owner)).state == 'RECOVERY_REQUIRED'
        b = command.replacement.funding.bundle
        b = replace(b, account=replace(b.account, included_funding_bill_ids=('777', '888'),
                                       snapshot=replace(b.account.snapshot, spot_cash_quote=D('10.1'), collateral_quote=D('10.1'))),
                    funding=replace(b.funding, payments=(*b.funding.payments, credit)))
        command = replace(command, additional_pending_operation_ids=('another-pending',),
                          replacement=replace(command.replacement, funding=replace(command.replacement.funding, bundle=b)))
        facts = replace(facts, account=replace(facts.account, account_quote_usdt=D('10.1')), funding_bill_ids=('777', '888'))
        cut = (await financial.observation_cut(cfg.binding)).cut
        approval = keys.sign('operator', cfg.binding, 'RECOVER', command.json(), cut, facts,
                             issued_at=approval.issued_at, expires_at=approval.expires_at)
        await importer.approve(command, approval=approval, cut=cut, facts=facts)
        await PgJointRepairRepository(importer.batch).resolve(command, cut=cut, owner=owner)
        frame = await funding.frame(cfg.binding)
        assert len(frame['payments']) == 2 and D(frame['gross_debits_usdt']) == D('.1')
        assert (await financial.balance(cfg.binding)).total_quote_usdt == D('10.1')
        assert len(frame['pending']) == 2 and await database[0].fetchval('SELECT halted FROM life_financial.accounts')
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['missing', 'non_funding', 'signature', 'capacity'])
async def test_invalid_or_unsigned_cohort_changes_no_money_or_member_links(database, fault):
    values, funding, bad, command, facts, cut, approval, importer, keys = await setup(
        database, extra_pending=True, max_members=1 if fault == 'capacity' else 32)
    financial, cfg, owner = values[:3]
    try:
        extra = dict(missing='missing', non_funding='prepare1').get(fault, 'another-pending')
        command = replace(command, additional_pending_operation_ids=(extra,))
        if fault != 'signature':
            approval = keys.sign('operator', cfg.binding, 'RECOVER', command.json(), cut, facts,
                                 issued_at=approval.issued_at, expires_at=approval.expires_at)
        with pytest.raises(StorageError, match='(PG_JOINT_REPAIR_IMPORT_PROFILE_INVALID|OPERATOR_APPROVAL)'):
            await importer.approve(command, approval=approval, cut=cut, facts=facts)
        assert (await financial.balance(cfg.binding)).total_quote_usdt == 10
        for table in ('joint_repair_approvals', 'joint_repair_facts', 'joint_repair_members', 'funding_cash_payments'):
            assert await database[0].fetchval('SELECT count(*) FROM life_financial.' + table) == 0
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_unlisted_third_pending_blocks_entire_cohort_even_with_matching_bills(database):
    values, funding, bad, command, facts, cut, approval, importer, keys = await setup(database, extra_pending=True)
    financial, cfg, owner = values[:3]
    try:
        assert (await funding.apply_funding(replace(bad, operation_id='unlisted'),
                                            cut=(await financial.observation_cut(cfg.binding)).cut, owner=owner)).state == 'RECOVERY_REQUIRED'
        command = replace(command, additional_pending_operation_ids=('another-pending',))
        cut = (await financial.observation_cut(cfg.binding)).cut
        approval = keys.sign('operator', cfg.binding, 'RECOVER', command.json(), cut, facts,
                             issued_at=approval.issued_at, expires_at=approval.expires_at)
        await importer.approve(command, approval=approval, cut=cut, facts=facts)
        with pytest.raises(StorageError, match='PG_JOINT_REPAIR_REPLACEMENT_REJECTED'):
            await PgJointRepairRepository(importer.batch).resolve(command, cut=cut, owner=owner)
        assert (await financial.balance(cfg.binding)).total_quote_usdt == 10
        assert len((await funding.frame(cfg.binding))['pending']) == 3
        for table in ('joint_repair_facts', 'joint_repair_members', 'joint_repair_staging', 'joint_batch_staging', 'funding_cash_payments'):
            assert await database[0].fetchval('SELECT count(*) FROM life_financial.' + table) == 0
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_every_selected_original_bill_must_match_the_signed_replacement(database):
    values, funding, bad, command, facts, cut, approval, importer, keys = await setup(database)
    financial, cfg, owner = values[:3]
    try:
        b = bad.bundle
        conflict = replace(bad, operation_id='another-pending', bundle=replace(b,
                           funding=replace(b.funding, payments=(replace(b.funding.payments[0], signed_payment_quote=D('1')),))))
        assert (await funding.apply_funding(conflict, cut=(await financial.observation_cut(cfg.binding)).cut, owner=owner)).state == 'RECOVERY_REQUIRED'
        command = replace(command, additional_pending_operation_ids=('another-pending',))
        cut = (await financial.observation_cut(cfg.binding)).cut
        approval = keys.sign('operator', cfg.binding, 'RECOVER', command.json(), cut, facts,
                             issued_at=approval.issued_at, expires_at=approval.expires_at)
        with pytest.raises(StorageError, match='PG_JOINT_REPAIR_IMPORT_PROFILE_INVALID'):
            await importer.approve(command, approval=approval, cut=cut, facts=facts)
        assert (await financial.balance(cfg.binding)).total_quote_usdt == 10
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.joint_repair_approvals') == 0
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('credit_first', [False, True])
async def test_cohort_uses_actual_economic_order_and_retains_gross_budget_breach(database, credit_first):
    values, funding, bad, command, facts, cut, approval, importer, keys = await setup(database)
    financial, cfg, owner = values[:3]
    try:
        b = bad.bundle
        debit = replace(b.funding.payments[0], bill_id='888', signed_payment_quote=D('-10.1'))
        other = replace(bad, operation_id='another-pending', bundle=replace(b,
                        account=replace(b.account, included_funding_bill_ids=('888',)), funding=replace(b.funding, payments=(debit,))))
        assert (await funding.apply_funding(other, cut=(await financial.observation_cut(cfg.binding)).cut, owner=owner)).state == 'RECOVERY_REQUIRED'
        credit = replace(debit, bill_id='999', signed_payment_quote=D('.3'), settled_at_ms=debit.settled_at_ms + (-1 if credit_first else 1))
        b = command.replacement.funding.bundle
        b = replace(b, account=replace(b.account, included_funding_bill_ids=('777', '888', '999'),
                                       snapshot=replace(b.account.snapshot, spot_cash_quote=D('.1'), collateral_quote=D('.1'))),
                    funding=replace(b.funding, payments=(*b.funding.payments, debit, credit)))
        command = replace(command, additional_pending_operation_ids=('another-pending',),
                          replacement=replace(command.replacement, funding=replace(command.replacement.funding, bundle=b)))
        facts = replace(facts, account=replace(facts.account, account_quote_usdt=D('.1')), funding_bill_ids=('777', '888', '999'))
        cut = (await financial.observation_cut(cfg.binding)).cut
        approval = keys.sign('operator', cfg.binding, 'RECOVER', command.json(), cut, facts,
                             issued_at=approval.issued_at, expires_at=approval.expires_at)
        await importer.approve(command, approval=approval, cut=cut, facts=facts)
        repo = PgJointRepairRepository(importer.batch)
        if credit_first:
            await repo.resolve(command, cut=cut, owner=owner)
            assert (await financial.balance(cfg.binding)).total_quote_usdt == D('.1')
            assert D((await funding.frame(cfg.binding))['gross_debits_usdt']) == D('10.2')
            assert not (await repo.frame(cfg.binding))['pending']
        else:
            with pytest.raises(StorageError, match='PG_JOINT_REPAIR_REPLACEMENT_REJECTED'):
                await repo.resolve(command, cut=cut, owner=owner)
            assert (await financial.balance(cfg.binding)).total_quote_usdt == 10
            assert await database[0].fetchval('SELECT count(*) FROM life_financial.funding_cash_payments') == 0
            assert await database[0].fetchval('SELECT count(*) FROM life_financial.joint_repair_members') == 0
        assert await database[0].fetchval('SELECT halted FROM life_financial.accounts')
        assert len((await funding.frame(cfg.binding))['pending']) == 2
        assert len(await values[5].claims(cfg.binding)) == 1
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('target', ['link', 'secondary_original'])
async def test_damaged_cohort_link_or_second_original_revokes_readback(database, target):
    values, funding, bad, command, facts, cut, approval, importer, keys = await setup(database, extra_pending=True)
    financial, cfg, owner = values[:3]
    try:
        command = replace(command, additional_pending_operation_ids=('another-pending',))
        approval = keys.sign('operator', cfg.binding, 'RECOVER', command.json(), cut, facts,
                             issued_at=approval.issued_at, expires_at=approval.expires_at)
        await importer.approve(command, approval=approval, cut=cut, facts=facts)
        repo = PgJointRepairRepository(importer.batch)
        await repo.resolve(command, cut=cut, owner=owner)
        table = 'joint_repair_members' if target == 'link' else 'funding_observations'
        c = database[0]
        await c.execute('ALTER TABLE life_financial.' + table + ' DISABLE TRIGGER USER')
        if target == 'link':
            await c.execute('DELETE FROM life_financial.joint_repair_members')
        else:
            await c.execute("UPDATE life_financial.funding_observations SET bundle=jsonb_set(bundle,'{funding,payments,0,signed_payment_quote}','\"1\"') WHERE operation_id='another-pending'")
        await c.execute('ALTER TABLE life_financial.' + table + ' ENABLE TRIGGER USER')
        with pytest.raises(StorageError, match='PG_JOINT_REPAIR_UNVERIFIED'):
            await repo.lookup(command.operation_id, cfg.binding)
        assert (await financial.snapshot(cfg.binding)).recovery_required
        assert await c.fetchval("SELECT total FROM life_financial.balances WHERE asset='USDT'") == D('9.9')
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('committed', [False, True])
async def test_process_crash_restores_both_original_pending_links_once(database, committed):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.joint_repair_process import exit_joint_repair

    from hummingbot.strategy_v2.life_liquidity.storage.postgres.joint_batch import PgJointBatchRepository
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.shared_capital import PgSharedCapitalRepository
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.swap import PgSwapRepository
    values, funding, bad, command, facts, cut, approval, importer, keys = await setup(database, extra_pending=True)
    financial, cfg, owner = values[:3]
    cold = None
    try:
        command = replace(command, additional_pending_operation_ids=('another-pending',))
        approval = keys.sign('operator', cfg.binding, 'RECOVER', command.json(), cut, facts,
                             issued_at=approval.issued_at, expires_at=approval.expires_at)
        await importer.approve(command, approval=approval, cut=cut, facts=facts)
        await exit_joint_repair(cfg, command, cut, owner, committed)
        cold = PgSpotRepository(await PgRuntime.open(cfg))
        capital = PgSharedCapitalRepository(cold, owner=owner, observation=lambda: None, max_rows=64, max_bytes=1048576)
        repo = PgJointRepairRepository(PgJointBatchRepository(PgSwapRepository(capital)))
        assert ((await repo.lookup(command.operation_id, cfg.binding)) is not None) is committed
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.joint_repair_members') == int(committed)
        receipt = await repo.resolve(command, cut=cut, owner=owner)
        assert receipt.replayed is committed and receipt.additional_pending_operation_ids == ('another-pending',)
        assert (await cold.balance(cfg.binding)).total_quote_usdt == D('9.9')
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.funding_cash_payments') == 1
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.joint_repair_members') == 1
        assert await database[0].fetchval('SELECT halted FROM life_financial.accounts')
    finally:
        await financial.close()
        if cold is not None:
            await cold.close()
