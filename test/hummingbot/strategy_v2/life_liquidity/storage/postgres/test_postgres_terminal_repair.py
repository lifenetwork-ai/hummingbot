"""Synthetic signed terminal reconciliation; original orders and claims survive."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_portfolio import refresh
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_swap_terminal import (
    setup as terminal_setup,
)

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown, StorageError
from hummingbot.strategy_v2.life_liquidity.storage.control_plane import OperatorKeys
from hummingbot.strategy_v2.life_liquidity.storage.postgres.joint_repair import JointRepairFacts
from hummingbot.strategy_v2.life_liquidity.storage.postgres.portfolio import MeasurePortfolio, PgPortfolioRepository
from hummingbot.strategy_v2.life_liquidity.storage.postgres.shared_release import (
    PgSharedReleaseRepository,
    ReleaseSharedClaim,
)
from hummingbot.strategy_v2.life_liquidity.storage.postgres.terminal_repair import (
    PgTerminalRepairRepository,
    ResolveSwapTerminals,
)
from hummingbot.strategy_v2.life_liquidity.storage.postgres.terminal_repair_approval import (
    PgTerminalRepairApprovalImporter,
)
from hummingbot.strategy_v2.life_liquidity.storage.recovery import SpotFinancialRecoveryFacts


async def setup(database, *, filled=True, cohort=False, source_age_ms=None):
    keys = OperatorKeys({'operator': b'r' * 32}, {'operator': frozenset(('RECOVER',))})  # Synthetic only.
    values, terminals, original, fills, fill = await terminal_setup(
        database, filled=filled, spot_options=dict(configuration_body=lambda _: {'operator_verifier': keys.verifier().policy()}))
    financial, cfg, owner = values[:3]
    c = database[0]
    if source_age_ms is not None:
        # Explicit narrower synthetic boundary profile; never widen a runtime limit.
        assert 0 < source_age_ms < 5000
        await c.execute('ALTER TABLE life_financial.portfolio_policies DISABLE TRIGGER USER')
        await c.execute('UPDATE life_financial.portfolio_policies SET max_age_ms=$1', source_age_ms)
        await c.execute('ALTER TABLE life_financial.portfolio_policies ENABLE TRIGGER USER')
    await c.execute('INSERT INTO life_financial.terminal_repair_policies VALUES($1,$2,64,64,1048576,5000)', cfg.account_id, cfg.binding.policy_hash)
    await c.execute('INSERT INTO life_financial.shared_release_policies VALUES($1,$2,64,1048576)', cfg.account_id, cfg.binding.policy_hash)
    bad = replace(original, source='cancel_ack')
    assert (await terminals.confirm(bad, cut=(await financial.observation_cut(cfg.binding)).cut, owner=owner)).state == 'RECOVERY_REQUIRED'
    additional = ()
    if cohort:
        additional = ('another-pending',)
        assert (await terminals.confirm(replace(original, operation_id=additional[0]), cut=(await financial.observation_cut(cfg.binding)).cut, owner=owner)).state == 'RECOVERY_REQUIRED'
    await refresh(database, values, short='1' if filled else '0', cash='9.99' if filled else '10')
    await PgPortfolioRepository(terminals.swap).measure(MeasurePortfolio('nav-repair'), cut=(await financial.observation_cut(cfg.binding)).cut, owner=owner)
    at = values[4][0].observed_at_ms
    now = datetime.fromtimestamp(at / 1000, timezone.utc)
    fresh = replace(original, operation_id='terminal-fresh', observed_at_ms=at)
    command = ResolveSwapTerminals('resolve-terminals', bad.operation_id, (fresh,), additional)
    facts = JointRepairFacts(SpotFinancialRecoveryFacts(cfg.binding, now, D('5'), D('9.99') if filled else D('10'), D('0'), D('0'),
                                                        ('trade1',) if filled else (), (), ('SwapWire1',), (), True, True, True),
                             (('SWAP', 'trade1'),) if filled else (), (), True,
                             await c.fetchval('SELECT floor(extract(epoch FROM opened_at)*1000)::bigint FROM life_financial.spot_capital_policies'), at)
    cut = (await financial.observation_cut(cfg.binding)).cut
    approval = keys.sign('operator', cfg.binding, 'RECOVER', command.json(), cut, facts,
                         issued_at=now - timedelta(seconds=1), expires_at=now + timedelta(seconds=10))
    importer = PgTerminalRepairApprovalImporter(c, terminals, verifier=keys.verifier(), max_fact_age_ms=5000)
    return values, terminals, bad, command, facts, cut, approval, importer, keys, fills, fill


@pytest.mark.asyncio
@pytest.mark.parametrize('filled,cohort', [(True, False), (False, False), (True, True)])
async def test_signed_terminal_reconciliation_preserves_pending_money_halt_and_independent_release(database, filled, cohort):
    values, terminals, bad, command, facts, cut, approval, importer, _, _, _ = await setup(database, filled=filled, cohort=cohort)
    financial, cfg, owner = values[:3]
    c = database[0]
    try:
        raw = await c.fetchval('SELECT jsonb_agg(to_jsonb(f) ORDER BY operation_id) FROM life_financial.swap_terminal_facts f')
        balance = await financial.balance(cfg.binding)
        repo = PgTerminalRepairRepository(terminals)
        await importer.approve(command, approval=approval, cut=cut, facts=facts)
        assert (await financial.observation_cut(cfg.binding)).cut == cut
        result = await repo.resolve(command, cut=cut, owner=owner)
        assert result.state == 'RECONCILED' and not result.trading_permission and not result.claim_released
        assert (await repo.resolve(command, cut=cut, owner=owner)).replayed
        assert (await repo.lookup(command.operation_id, cfg.binding)).replayed
        assert (await importer.lookup(command.operation_id)).replayed
        assert await c.fetchval('SELECT jsonb_agg(to_jsonb(f) ORDER BY operation_id) FROM life_financial.swap_terminal_facts f') == raw
        assert await financial.balance(cfg.binding) == balance
        assert (await terminals.lookup(bad.operation_id, cfg.binding)).state == 'RECOVERY_REQUIRED'
        assert await c.fetchval("SELECT state FROM life_financial.send_attempts WHERE intent_id='swap1'") == 'ACKED'
        assert len(await values[5].claims(cfg.binding)) == 1
        assert await c.fetchval('SELECT halted FROM life_financial.accounts')
        assert await c.fetchval('SELECT life_financial.terminal_pending_unresolved($1,$2,$3)', cfg.account_id, bad.operation_id, cut.checkpoint.revision)
        assert not await c.fetchval('SELECT life_financial.terminal_pending_unresolved($1,$2,NULL)', cfg.account_id, bad.operation_id)
        assert not (await repo.frame(cfg.binding))['pending']
        # Reconciliation alone never stands in for native terminal/release.
        with pytest.raises(StorageError, match='SHARED_RELEASE_PROOF_INVALID'):
            await PgSharedReleaseRepository(values[5]).release(ReleaseSharedClaim('too-early', 'swap1', command.operation_id),
                                                               cut=(await financial.observation_cut(cfg.binding)).cut, owner=owner)
        await refresh(database, values, short='1' if filled else '0', cash='9.99' if filled else '10')
        await PgPortfolioRepository(terminals.swap).measure(MeasurePortfolio('nav-terminal'), cut=(await financial.observation_cut(cfg.binding)).cut, owner=owner)
        fresh = replace(command.terminals[0], observed_at_ms=values[4][0].observed_at_ms)
        assert (await terminals.confirm(fresh, cut=(await financial.observation_cut(cfg.binding)).cut, owner=owner)).state == 'TERMINAL'
        assert len(await values[5].claims(cfg.binding)) == 1
        assert (await PgSharedReleaseRepository(values[5]).release(ReleaseSharedClaim('release', 'swap1', fresh.operation_id),
                                                                   cut=(await financial.observation_cut(cfg.binding)).cut, owner=owner)).released
        assert await values[5].claims(cfg.binding) == ()
        assert await financial.balance(cfg.binding) == balance
        assert await c.fetchval('SELECT life_financial.terminal_repair_verified($1)', cfg.account_id)
        assert await c.fetchval('SELECT life_financial.shared_release_verified($1)', cfg.account_id)
        assert (await terminals.lookup(bad.operation_id, cfg.binding)).state == 'RECOVERY_REQUIRED'
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['unsigned', 'wrong-cash', 'liability', 'scope', 'trades', 'order', 'quantity', 'source', 'coverage', 'omitted-pending'])
async def test_terminal_repair_rejects_unqualified_or_incomplete_signed_facts(database, fault):
    values, terminals, bad, command, facts, cut, approval, importer, keys, _, _ = await setup(database, cohort=fault == 'omitted-pending')
    financial, cfg, owner = values[:3]
    try:
        if fault in ('wrong-cash', 'liability', 'scope'):
            changes = {'wrong-cash': dict(account_quote_usdt=D('12')), 'liability': dict(liability_quote_usdt=D('1')), 'scope': dict(order_scope_complete=False)}
            facts = replace(facts, account=replace(facts.account, **changes[fault]))
        elif fault == 'trades':
            facts = replace(facts, account=replace(facts.account, account_trade_ids=()), product_trade_ids=())
        elif fault == 'omitted-pending':
            command = replace(command, additional_pending_operation_ids=())
        elif fault != 'unsigned':
            changes = {'order': dict(exchange_order_id='123'), 'quantity': dict(cumulative_base_life=D('.5')),
                       'source': dict(source='cancel_ack'), 'coverage': dict(coverage_from_ms=facts.coverage_through_ms + 1)}
            command = replace(command, terminals=(replace(command.terminals[0], **changes[fault]),))
        approval = keys.sign('operator', cfg.binding, 'RECOVER', command.json(), cut, facts, issued_at=approval.issued_at, expires_at=approval.expires_at)
        with pytest.raises(StorageError, match='TERMINAL_REPAIR_(APPROVAL_REQUIRED|IMPORT_PROFILE_INVALID)'):
            if fault != 'unsigned':
                await importer.approve(command, approval=approval, cut=cut, facts=facts)
            await PgTerminalRepairRepository(terminals).resolve(command, cut=cut, owner=owner)
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.terminal_repair_facts') == 0
        assert await database[0].fetchval("SELECT state FROM life_financial.send_attempts WHERE intent_id='swap1'") == 'ACKED'
        assert len(await values[5].claims(cfg.binding)) == 1
        assert (await terminals.lookup(bad.operation_id, cfg.binding)).state == 'RECOVERY_REQUIRED'
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('committed', [False, True])
@pytest.mark.parametrize('fault', ['reply', 'process'])
async def test_terminal_repair_unknown_commit_cold_readback_never_releases_claim(database, monkeypatch, committed, fault):
    values, terminals, bad, command, facts, cut, approval, importer, _, _, _ = await setup(database)
    financial, cfg, owner = values[:3]
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.shared_capital import PgSharedCapitalRepository
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.swap import PgSwapRepository
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.swap_terminal import PgSwapTerminalRepository
    cold = None
    try:
        await importer.approve(command, approval=approval, cut=cut, facts=facts)
        original = importer.runtime._commit

        async def lost(tx):
            if committed:
                await original(tx)
            raise ConnectionError('Synthetic missing terminal reconciliation COMMIT reply')

        if fault == 'process':
            from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.terminal_repair_process import (
                exit_terminal_repair,
            )
            await exit_terminal_repair(cfg, command, cut, owner, committed)
        else:
            monkeypatch.setattr(importer.runtime, '_commit', lost)
            with pytest.raises(CommitUnknown):
                await PgTerminalRepairRepository(terminals).resolve(command, cut=cut, owner=owner)
        cold = PgSpotRepository(await PgRuntime.open(cfg))
        capital = PgSharedCapitalRepository(cold, owner=owner, observation=lambda: None, max_rows=64, max_bytes=1048576)
        repo = PgTerminalRepairRepository(PgSwapTerminalRepository(PgSwapRepository(capital)))
        assert ((await repo.lookup(command.operation_id, cfg.binding)) is not None) is committed
        if not committed:
            await repo.resolve(command, cut=cut, owner=owner)
        assert (await repo.resolve(command, cut=cut, owner=owner)).replayed
        assert len(await capital.claims(cfg.binding)) == 1
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.terminal_repair_facts') == 1
        assert await database[0].fetchval('SELECT halted FROM life_financial.accounts')
    finally:
        if cold is not None:
            await cold.close()
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['approval', 'application'])
async def test_terminal_repair_approval_expiry_at_physical_commit_keeps_pending_and_claim(database, monkeypatch, stage):
    import asyncio
    values, terminals, _, command, facts, cut, approval, importer, keys, _, _ = await setup(database)
    financial, cfg, owner = values[:3]
    now = await database[0].fetchval('SELECT clock_timestamp()')
    approval = keys.sign('operator', cfg.binding, 'RECOVER', command.json(), cut, facts,
                         issued_at=now - timedelta(seconds=1), expires_at=now + timedelta(seconds=2))
    target = importer if stage == 'approval' else importer.runtime
    original = target._commit
    try:
        if stage == 'application':
            await importer.approve(command, approval=approval, cut=cut, facts=facts)

        async def expired(tx):
            at = await tx._connection.fetchval('SELECT clock_timestamp()')
            await asyncio.sleep(max(0, (approval.expires_at - at).total_seconds()) + .05)
            await original(tx)

        monkeypatch.setattr(target, '_commit', expired)
        with pytest.raises(StorageError, match='PG_TERMINAL_REPAIR_APPROVAL_EXPIRED_AT_COMMIT'):
            if stage == 'approval':
                await importer.approve(command, approval=approval, cut=cut, facts=facts)
            else:
                await PgTerminalRepairRepository(terminals).resolve(command, cut=cut, owner=owner)
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.terminal_repair_facts') == 0
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.terminal_repair_members') == 0
        assert len(await values[5].claims(cfg.binding)) == 1
        assert await database[0].fetchval('SELECT halted FROM life_financial.accounts')
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['signature', 'key-policy', 'owner', 'cut', 'bound'])
async def test_terminal_repair_authority_and_cut_are_explicit(database, fault):
    values, terminals, _, command, facts, cut, approval, importer, _, _, _ = await setup(database)
    financial, cfg, owner = values[:3]
    try:
        if fault == 'signature':
            approval = replace(approval, signature='0' * 128)
        elif fault == 'key-policy':
            wrong = OperatorKeys({'operator': b's' * 32}, {'operator': frozenset(('RECOVER',))})
            importer = PgTerminalRepairApprovalImporter(database[0], terminals, verifier=wrong.verifier(), max_fact_age_ms=5000)
        elif fault == 'bound':
            await database[0].execute('ALTER TABLE life_financial.terminal_repair_policies DISABLE TRIGGER USER')
            await database[0].execute('UPDATE life_financial.terminal_repair_policies SET max_total_bytes=1')
            await database[0].execute('ALTER TABLE life_financial.terminal_repair_policies ENABLE TRIGGER USER')
        if fault in ('owner', 'cut'):
            await importer.approve(command, approval=approval, cut=cut, facts=facts)
            with pytest.raises(StorageError):
                await PgTerminalRepairRepository(terminals).resolve(command, cut=cut if fault == 'owner' else replace(cut, checkpoint=replace(cut.checkpoint, state_digest='0' * 64), components=(('postgres_account', '0' * 64),)),
                                                                    owner=replace(owner, epoch=owner.epoch + 1) if fault == 'owner' else owner)
        else:
            with pytest.raises(StorageError):
                await importer.approve(command, approval=approval, cut=cut, facts=facts)
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.terminal_repair_facts') == 0
        assert len(await values[5].claims(cfg.binding)) == 1
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['approval', 'membership', 'receipt', 'original'])
async def test_corrupt_terminal_resolution_fails_cold_proof_without_releasing_claim(database, fault):
    values, terminals, _, command, facts, cut, approval, importer, _, _, _ = await setup(database)
    financial, cfg, owner = values[:3]
    c = database[0]
    try:
        await importer.approve(command, approval=approval, cut=cut, facts=facts)
        repo = PgTerminalRepairRepository(terminals)
        await repo.resolve(command, cut=cut, owner=owner)
        table, query = {
            'approval': ('terminal_repair_approvals', "UPDATE life_financial.terminal_repair_approvals SET body=jsonb_set(body,'{facts,account,account_quote_usdt}','\"12\"')"),
            'membership': ('terminal_repair_members', 'DELETE FROM life_financial.terminal_repair_members'),
            'receipt': ('operations', "UPDATE life_financial.operations SET receipt=jsonb_set(receipt,'{claim_released}','true') WHERE operation_id='resolve-terminals'"),
            'original': ('swap_terminal_facts', "UPDATE life_financial.swap_terminal_facts SET command=jsonb_set(command,'{source}','\"invented\"') WHERE state='RECOVERY_REQUIRED'"),
        }[fault]
        await c.execute(f'ALTER TABLE life_financial.{table} DISABLE TRIGGER USER')
        await c.execute(query)
        await c.execute(f'ALTER TABLE life_financial.{table} ENABLE TRIGGER USER')
        with pytest.raises(StorageError, match='TERMINAL_REPAIR_UNVERIFIED'):
            await repo.lookup(command.operation_id, cfg.binding)
        assert (await financial.snapshot(cfg.binding)).recovery_required
        assert await c.fetchval('SELECT count(*) FROM life_financial.shared_claim_releases') == 0
        assert await c.fetchval('SELECT halted FROM life_financial.accounts')
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_unused_alias_approval_is_not_resolution_and_later_pending_still_blocks_terminal(database):
    values, terminals, bad, command, facts, cut, approval, importer, keys, _, _ = await setup(database)
    financial, cfg, owner = values[:3]
    try:
        alias = replace(command, operation_id='unused-alias')
        signed = keys.sign('operator', cfg.binding, 'RECOVER', alias.json(), cut, facts, issued_at=approval.issued_at, expires_at=approval.expires_at)
        await importer.approve(alias, approval=signed, cut=cut, facts=facts)
        assert (await financial.snapshot(cfg.binding)).recovery_required
        assert await database[0].fetchval('SELECT life_financial.terminal_pending_unresolved($1,$2,NULL)', cfg.account_id, bad.operation_id)
        await importer.approve(command, approval=approval, cut=cut, facts=facts)
        repo = PgTerminalRepairRepository(terminals)
        await repo.resolve(command, cut=cut, owner=owner)
        assert await repo.lookup(alias.operation_id, cfg.binding) is None
        later = replace(bad, operation_id='later-pending')
        assert (await terminals.confirm(later, cut=(await financial.observation_cut(cfg.binding)).cut, owner=owner)).state == 'RECOVERY_REQUIRED'
        await refresh(database, values, short='1', cash='9.99')
        await PgPortfolioRepository(terminals.swap).measure(MeasurePortfolio('nav-later'), cut=(await financial.observation_cut(cfg.binding)).cut, owner=owner)
        fresh = replace(command.terminals[0], observed_at_ms=values[4][0].observed_at_ms)
        assert (await terminals.confirm(fresh, cut=(await financial.observation_cut(cfg.binding)).cut, owner=owner)).state == 'RECOVERY_REQUIRED'
        assert (await repo.lookup(command.operation_id, cfg.binding)).replayed
        assert len(await values[5].claims(cfg.binding)) == 1
        assert await database[0].fetchval('SELECT life_financial.terminal_pending_unresolved($1,$2,NULL)', cfg.account_id, later.operation_id)
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_terminal_repair_roles_cannot_forge_approval_or_call_private_bypass(database):
    import asyncpg
    values, terminals, _, _, _, _, _, _, _, _, _ = await setup(database)
    financial, cfg, _ = values[:3]
    try:
        for dsn in (cfg.dsn, cfg.dsn.replace('life_pg2_runtime@', 'life_pg2_reporter@')):
            c = await asyncpg.connect(dsn)
            try:
                for table in ('terminal_repair_policies', 'terminal_repair_approvals', 'terminal_repair_facts', 'terminal_repair_members'):
                    with pytest.raises(asyncpg.InsufficientPrivilegeError):
                        await c.execute('DELETE FROM life_financial.' + table)
                for query in ('SELECT life_financial.terminal_pending_unresolved($1,\'terminal1\',NULL)',
                              'SELECT life_financial.terminal_repair_verified($1)',
                              "SELECT life_financial.terminal_repair_reason($1,'{}',0,1,'[]')"):
                    with pytest.raises(asyncpg.InsufficientPrivilegeError):
                        await c.fetchval(query, cfg.account_id)
                if 'reporter@' in dsn:
                    with pytest.raises(asyncpg.InsufficientPrivilegeError):
                        await c.fetchval("SELECT life_financial.terminal_repair_command($1,'{}','{}',0,$2,$2,'{}')", cfg.account_id, '0' * 64)
            finally:
                await c.close()
        assert len(await values[5].claims(cfg.binding)) == 1
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_terminal_repair_generation_change_at_physical_commit_rolls_back_membership(database, monkeypatch):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.helpers import install_generation_fault
    values, terminals, _, command, facts, cut, approval, importer, _, _, _ = await setup(database)
    financial, cfg, owner = values[:3]
    await install_generation_fault(database)
    try:
        await importer.approve(command, approval=approval, cut=cut, facts=facts)
        original = importer.runtime._commit

        async def changed(tx):
            await tx._connection.execute('SELECT life_financial.test_change_generation($1)', cfg.account_id)
            await original(tx)

        monkeypatch.setattr(importer.runtime, '_commit', changed)
        with pytest.raises(StorageError, match='OWNER_EXPIRED_OR_CHANGED_AT_COMMIT'):
            await PgTerminalRepairRepository(terminals).resolve(command, cut=cut, owner=owner)
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.terminal_repair_facts') == 0
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.terminal_repair_members') == 0
        assert len(await values[5].claims(cfg.binding)) == 1
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_terminal_repair_source_expiry_at_commit_does_not_clear_pending(database, monkeypatch):
    import asyncio
    values, terminals, bad, command, facts, cut, approval, importer, _, _, _ = await setup(database, source_age_ms=1000)
    financial, cfg, owner = values[:3]
    try:
        await importer.approve(command, approval=approval, cut=cut, facts=facts)
        original = importer.runtime._commit

        async def stale(tx):
            at = await tx._connection.fetchval('SELECT clock_timestamp()')
            # This explicit synthetic source age is narrower than the 5000-ms facts bound.
            await asyncio.sleep(max(0, (facts.observed_at + timedelta(milliseconds=1100) - at).total_seconds()))
            await original(tx)

        monkeypatch.setattr(importer.runtime, '_commit', stale)
        with pytest.raises(StorageError, match='PG_TERMINAL_REPAIR_SOURCE_STALE_AT_COMMIT'):
            await PgTerminalRepairRepository(terminals).resolve(command, cut=cut, owner=owner)
        assert await database[0].fetchval('SELECT life_financial.terminal_pending_unresolved($1,$2,NULL)', cfg.account_id, bad.operation_id)
        assert len(await values[5].claims(cfg.binding)) == 1
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_terminal_only_refresh_exception_cannot_bypass_pending_fill(database):
    values, terminals, bad, command, facts, _, approval, importer, keys, fills, fill = await setup(database)
    financial, cfg, owner = values[:3]
    try:
        pending = replace(fill, operation_id='other-financial-pending', fills=(replace(fill.fills[0], signed_fee=D('-.02')),))
        assert (await fills.apply_fills(pending, cut=(await financial.observation_cut(cfg.binding)).cut, owner=owner)).state == 'RECOVERY_REQUIRED'
        with pytest.raises(StorageError, match='PG_CAPITAL_FINANCIAL_RECOVERY_REQUIRED'):
            await values[5].check_capital(cfg.binding)
        cut = (await financial.observation_cut(cfg.binding)).cut
        signed = keys.sign('operator', cfg.binding, 'RECOVER', command.json(), cut, facts, issued_at=approval.issued_at, expires_at=approval.expires_at)
        with pytest.raises(StorageError, match='PG_TERMINAL_REPAIR_IMPORT_PROFILE_INVALID'):
            await importer.approve(command, approval=signed, cut=cut, facts=facts)
        assert await database[0].fetchval('SELECT life_financial.terminal_pending_unresolved($1,$2,NULL)', cfg.account_id, bad.operation_id)
        assert len(await values[5].claims(cfg.binding)) == 1
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.shared_claim_releases') == 0
    finally:
        await financial.close()
