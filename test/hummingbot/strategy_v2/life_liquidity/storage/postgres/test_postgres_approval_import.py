"""Privileged signed approval import never credits an account or grants send."""
from datetime import timedelta
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_financial_commands import spot

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.capital import ApprovedSpotCashflow
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
from hummingbot.strategy_v2.life_liquidity.storage.control_plane import ApproveSpotCashflow, OperatorKeys
from hummingbot.strategy_v2.life_liquidity.storage.postgres.approval_import import PgCashflowApprovalImporter
from hummingbot.strategy_v2.life_liquidity.storage.recovery import SpotFinancialRecoveryFacts


async def setup(database, *, max_age=1000):
    keys = OperatorKeys({'operator': b'x' * 32}, {'operator': frozenset(('APPROVE_CASHFLOW',))})  # Synthetic test key only.
    verifier = keys.verifier()
    financial, cfg, owner = await spot(database, configuration_body=lambda _: {'operator_verifier': verifier.policy()})
    at = await database[0].fetchval('SELECT clock_timestamp()')
    await database[0].execute("INSERT INTO life_financial.spot_capital_policies VALUES($1,$2,5,10,1,$3,'100',4096)",
                              cfg.account_id, cfg.binding.policy_hash, at - timedelta(seconds=1))
    await database[0].execute('INSERT INTO life_financial.spot_fill_policies VALUES($1,$2,1000,4096)', cfg.account_id, cfg.binding.policy_hash)
    observed = await financial.observation_cut(cfg.binding)
    facts = SpotFinancialRecoveryFacts(cfg.binding, observed.observed_at, D('5'), D('12'), D('0'), D('0'), (), ('101',), (), (), True, True, True)
    command = ApproveSpotCashflow('approval-101', ApprovedSpotCashflow('101', 'USDT', D('2'), int(at.timestamp() * 1000)))
    approval = keys.sign('operator', cfg.binding, 'APPROVE_CASHFLOW', command.json(), observed.cut, facts,
                         issued_at=at - timedelta(seconds=1), expires_at=at + timedelta(seconds=10))
    importer = PgCashflowApprovalImporter(database[0], financial, verifier=verifier, max_fact_age_ms=max_age)
    return financial, cfg, owner, command, approval, observed.cut, facts, importer


@pytest.mark.asyncio
async def test_signed_import_and_original_replay_are_atomic_money_neutral_and_auditable(database):
    financial, cfg, _, command, approval, cut, facts, importer = await setup(database)
    try:
        before = await financial.balance(cfg.binding)
        receipt = await importer.approve(command, approval=approval, cut=cut, facts=facts)
        assert not receipt.replayed and not receipt.trading_permission
        assert (await importer.approve(command, approval=approval, cut=cut, facts=facts)).replayed
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.bill_approvals') == 1
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.spot_operator_approvals') == 1
        assert await financial.balance(cfg.binding) == before
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.cashflows') == 0
        assert (await importer.lookup(command.operation_id)).bill_id == '101'
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['signature', 'role', 'cut', 'facts', 'stale', 'policy'])
async def test_unqualified_operator_input_cannot_import_an_approval(database, fault):
    from dataclasses import replace

    from hummingbot.strategy_v2.life_liquidity.storage.control_plane import OperatorKeys
    financial, cfg, _, command, approval, cut, facts, importer = await setup(database)
    try:
        if fault == 'signature':
            approval = replace(approval, signature='0' * 128)
        elif fault == 'role':
            approval = replace(approval, role='ACTIVATE')
        elif fault == 'cut':
            cut = replace(cut, components=(('postgres_account', 'f' * 64),))
        elif fault == 'facts':
            facts = replace(facts, bill_scope_complete=False)
        elif fault == 'stale':
            facts = replace(facts, observed_at=facts.observed_at - timedelta(minutes=1))
        else:
            other = OperatorKeys({'operator': b'y' * 32}, {'operator': frozenset(('APPROVE_CASHFLOW',))})
            importer = PgCashflowApprovalImporter(database[0], financial, verifier=other.verifier(), max_fact_age_ms=1000)
        expected = {'signature': 'OPERATOR_APPROVAL_SIGNATURE_INVALID', 'role': 'OPERATOR_APPROVAL_UNQUALIFIED',
                    'cut': 'PG_APPROVAL_IMPORT_CUT_STALE', 'facts': 'PG_APPROVAL_IMPORT_FACTS_UNQUALIFIED',
                    'stale': 'PG_APPROVAL_IMPORT_FACTS_UNQUALIFIED', 'policy': 'PG_APPROVAL_IMPORT_KEY_POLICY_MISMATCH'}[fault]
        with pytest.raises(StorageError, match=expected):
            await importer.approve(command, approval=approval, cut=cut, facts=facts)
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.bill_approvals') == 0
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.spot_operator_approvals') == 0
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('committed', [False, True])
async def test_real_backend_termination_preserves_original_approval_identity(database, monkeypatch, committed):
    import asyncpg

    from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown
    financial, cfg, _, command, approval, cut, facts, importer = await setup(database, max_age=10000)
    admin = await asyncpg.connect(database[1])
    c = None
    original = importer._commit
    try:
        async def lost(tx):
            pid = tx._connection.get_server_pid()
            if committed:
                await original(tx)
            assert await admin.fetchval('SELECT pg_terminate_backend($1)', pid)
            raise ConnectionError('synthetic approval COMMIT reply loss')
        monkeypatch.setattr(importer, '_commit', lost)
        with pytest.raises(CommitUnknown, match='PG_APPROVAL_IMPORT_COMMIT_UNKNOWN'):
            await importer.approve(command, approval=approval, cut=cut, facts=facts)
        c = await asyncpg.connect(database[2])
        cold = PgCashflowApprovalImporter(c, financial, verifier=importer.verifier, max_fact_age_ms=10000)
        assert (await cold.lookup(command.operation_id) is not None) is committed
        await cold.approve(command, approval=approval, cut=cut, facts=facts)
        assert await c.fetchval('SELECT count(*) FROM life_financial.bill_approvals') == 1
        assert await c.fetchval('SELECT count(*) FROM life_financial.spot_operator_approvals') == 1
        assert (await financial.balance(cfg.binding)).total_quote_usdt == 10
        assert await c.fetchval('SELECT count(*) FROM life_financial.cashflows') == 0
    finally:
        if c is not None:
            await c.close()
        await admin.close()
        await financial.close()


@pytest.mark.asyncio
async def test_runtime_role_cannot_import_or_forge_operator_approval(database):
    import asyncpg
    financial, cfg, _, command, approval, cut, facts, importer = await setup(database)
    c = await asyncpg.connect(cfg.dsn)
    try:
        forbidden = PgCashflowApprovalImporter(c, financial, verifier=importer.verifier, max_fact_age_ms=1000)
        with pytest.raises(StorageError, match='PG_APPROVAL_IMPORT_OWNER_REQUIRED'):
            await forbidden.approve(command, approval=approval, cut=cut, facts=facts)
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await c.execute("INSERT INTO life_financial.bill_approvals VALUES($1,'101','USDT',2,clock_timestamp(),$2)", cfg.account_id, b'forged')
        for signature in ('spot_operator_approval_integrity(uuid)', 'spot_capital_verified_v19(uuid)'):
            assert not await c.fetchval("SELECT has_function_privilege(current_user,$1,'EXECUTE')", 'life_financial.' + signature)
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.spot_operator_approvals') == 0
    finally:
        await c.close()
        await financial.close()


@pytest.mark.asyncio
async def test_signature_terms_cannot_round_through_postgres_money_domain(database):
    from dataclasses import replace
    financial, cfg, _, command, approval, cut, facts, importer = await setup(database)
    try:
        command = replace(command, bill=replace(command.bill, signed_amount=D('2.0000000000000000001')))
        keys = OperatorKeys({'operator': b'x' * 32}, {'operator': frozenset(('APPROVE_CASHFLOW',))})
        signed = keys.sign('operator', cfg.binding, 'APPROVE_CASHFLOW', command.json(), cut, facts,
                           issued_at=approval.issued_at, expires_at=approval.expires_at)
        with pytest.raises(StorageError, match='PG_CONSTRAINT_REJECTED'):
            await importer.approve(command, approval=signed, cut=cut, facts=facts)
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.bill_approvals') == 0
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.spot_operator_approvals') == 0
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('target', ['signature', 'audit'])
async def test_corrupt_approval_blocks_lookup_and_financial_admission(database, target):
    financial, cfg, _, command, approval, cut, facts, importer = await setup(database)
    try:
        await importer.approve(command, approval=approval, cut=cut, facts=facts)
        table = 'bill_approvals' if target == 'signature' else 'spot_operator_approvals'
        await database[0].execute('ALTER TABLE life_financial.' + table + ' DISABLE TRIGGER USER')
        if target == 'signature':
            await database[0].execute("UPDATE life_financial.bill_approvals SET signature=$1", b'corrupt')
        else:
            await database[0].execute("UPDATE life_financial.spot_operator_approvals SET body=jsonb_set(body,'{payload,bill,signed_amount}','\"99\"')")
        await database[0].execute('ALTER TABLE life_financial.' + table + ' ENABLE TRIGGER USER')
        with pytest.raises(StorageError, match='PG_APPROVAL_HISTORY_UNVERIFIED'):
            await importer.lookup(command.operation_id)
        assert (await financial.snapshot(cfg.binding)).recovery_required
        assert (await financial.balance(cfg.binding)).total_quote_usdt == 10
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('committed', [False, True])
async def test_real_process_exit_recovers_only_original_signed_approval(database, committed):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.approval_process import exit_approval
    financial, cfg, _, command, approval, cut, facts, importer = await setup(database, max_age=10000)
    try:
        await exit_approval(cfg, database[2], command, approval, cut, facts, importer.verifier, committed)
        assert (await importer.lookup(command.operation_id) is not None) is committed
        await importer.approve(command, approval=approval, cut=cut, facts=facts)
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.bill_approvals') == 1
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.spot_operator_approvals') == 1
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.cashflows') == 0
        assert (await financial.balance(cfg.binding)).total_quote_usdt == 10
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['generation', 'expiry'])
async def test_approval_commit_rechecks_capture_and_operator_expiry(database, monkeypatch, fault):
    import asyncio
    financial, cfg, _, command, approval, cut, facts, importer = await setup(database, max_age=10000)
    original = importer._commit
    try:
        if fault == 'expiry':
            now = await database[0].fetchval('SELECT clock_timestamp()')
            keys = OperatorKeys({'operator': b'x' * 32}, {'operator': frozenset(('APPROVE_CASHFLOW',))})
            approval = keys.sign('operator', cfg.binding, 'APPROVE_CASHFLOW', command.json(), cut, facts,
                                 issued_at=approval.issued_at, expires_at=now + timedelta(milliseconds=400))

        async def changed(tx):
            if fault == 'generation':
                await tx._connection.execute('UPDATE life_financial.accounts SET risk_epoch=risk_epoch+1,version=version+1 WHERE account_id=$1', cfg.account_id)
            else:
                await asyncio.sleep(.45)
            await original(tx)
        monkeypatch.setattr(importer, '_commit', changed)
        with pytest.raises(StorageError, match='PG_OPERATOR_APPROVAL_CONTEXT_CHANGED'):
            await importer.approve(command, approval=approval, cut=cut, facts=facts)
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.bill_approvals') == 0
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.spot_operator_approvals') == 0
        assert (await financial.snapshot(cfg.binding)).cut == cut
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_imported_signature_allows_separate_cashflow_application_without_renewing_approval(database):
    import asyncio

    from hummingbot.strategy_v2.life_liquidity.storage.capital import CashflowSpot
    financial, cfg, owner, command, approval, cut, facts, importer = await setup(database)
    try:
        now = await database[0].fetchval('SELECT clock_timestamp()')
        keys = OperatorKeys({'operator': b'x' * 32}, {'operator': frozenset(('APPROVE_CASHFLOW',))})
        signed = keys.sign('operator', cfg.binding, 'APPROVE_CASHFLOW', command.json(), cut, facts,
                           issued_at=approval.issued_at, expires_at=now + timedelta(milliseconds=400))
        await importer.approve(command, approval=signed, cut=cut, facts=facts)
        observed = await financial.observation_cut(cfg.binding)
        at = command.bill.transfer_at_ms
        cashflow = CashflowSpot('apply-bill-101', '101', 'USDT', D('2'), at, D('1'), at, at, 'quote_unit', observed.observed_at)
        receipt = await financial.apply_cashflow(cashflow, cut=observed.cut, owner=owner)
        assert receipt.state == 'APPLIED' and (await financial.balance(cfg.binding)).total_quote_usdt == 12
        await asyncio.sleep(.45)
        # This is historical readback, not renewal of the expired signature.
        assert (await importer.approve(command, approval=signed, cut=cut, facts=facts)).replayed
        assert (await importer.lookup(command.operation_id)).replayed
        assert (await financial.balance(cfg.binding)).total_quote_usdt == 12
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.cashflows') == 1
    finally:
        await financial.close()
