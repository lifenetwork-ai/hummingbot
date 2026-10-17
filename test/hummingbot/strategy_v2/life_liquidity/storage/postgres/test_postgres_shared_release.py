"""Shared claim retirement retains original financial identities and positions."""
from dataclasses import replace
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_portfolio import refresh
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_swap_terminal import (
    setup as terminal_setup,
)

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
from hummingbot.strategy_v2.life_liquidity.storage.postgres.portfolio import MeasurePortfolio, PgPortfolioRepository
from hummingbot.strategy_v2.life_liquidity.storage.postgres.shared_release import (
    PgSharedReleaseRepository,
    ReleaseSharedClaim,
)
from hummingbot.strategy_v2.life_liquidity.storage.products import ReserveCapital


async def setup(database, *, terminal=True):
    values, terminals, cmd, fills, fill = await terminal_setup(database)
    financial, cfg, owner = values[:3]
    await database[0].execute('INSERT INTO life_financial.shared_release_policies VALUES($1,$2,64,1048576)', cfg.account_id, cfg.binding.policy_hash)
    if terminal:
        assert (await terminals.confirm(cmd, cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)).state == 'TERMINAL'
    return values, PgSharedReleaseRepository(terminals.swap.capital), ReleaseSharedClaim('release1', 'swap1', cmd.operation_id), terminals, cmd


@pytest.mark.asyncio
async def test_terminal_release_retires_only_original_claim_keeps_position_cash_budget_and_receipts(database):
    values, repo, cmd, terminals, terminal = await setup(database)
    financial, cfg, owner = values[:3]
    try:
        before = await financial.snapshot(cfg.binding)
        claim = (await values[5].claims(cfg.binding))[0]
        cut = before.cut
        result = await repo.release(cmd, cut=cut, owner=owner)
        assert result.released and not result.trading_permission
        assert await values[5].claims(cfg.binding) == ()
        assert (await financial.balance(cfg.binding)) == before.balance
        assert (await financial.snapshot(cfg.binding)).budget == before.budget
        assert (await repo.release(cmd, cut=cut, owner=owner)).replayed
        assert (await repo.lookup('release1', cfg.binding)).released
        assert (await values[5].lookup('claim', cfg.binding)).decision.allowed
        assert (await terminals.lookup(terminal.operation_id, cfg.binding)).state == 'TERMINAL'
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.shared_capital_claims') == 1
        assert await database[0].fetchval("SELECT (life_financial.swap_fill_replay($1)->>'short_base')::numeric", cfg.account_id) == 1
        # Settled permanent IDs can never be allocated as fresh risk again.
        with pytest.raises(StorageError, match='PG_CAPITAL_ID_RETIRED'):
            await values[5].reserve(ReserveCapital('reallocate', claim), await values[5].cut(cfg.binding), owner)
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['live', 'wrong_terminal', 'unknown_claim', 'pending'])
async def test_release_requires_complete_original_terminal_without_pending_or_foreign_proofs(database, fault):
    values, repo, cmd, terminals, terminal = await setup(database, terminal=fault != 'live')
    financial, cfg, owner = values[:3]
    try:
        if fault == 'wrong_terminal':
            cmd = replace(cmd, terminal_operation_id='other')
        elif fault == 'unknown_claim':
            cmd = replace(cmd, intent_id='other')
        elif fault == 'pending':
            # A new post-terminal financial observation requires explicit repair.
            from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_swap_fills import FillSwap
            row = await database[0].fetchval("SELECT command->'command' FROM life_financial.operations WHERE operation_id='fill1'")
            import json
            fill = FillSwap.from_json(json.loads(row))
            from hummingbot.strategy_v2.life_liquidity.storage.postgres.swap_fills import PgSwapFillRepository
            await PgSwapFillRepository(terminals.swap).apply_fills(replace(fill, operation_id='late'), cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        before = await financial.balance(cfg.binding)
        with pytest.raises(StorageError, match='PG_SHARED_RELEASE_(PROOF_INVALID|PENDING|SOURCE_UNQUALIFIED)'):
            await repo.release(cmd, cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        assert len(await values[5].claims(cfg.binding)) == 1
        assert await financial.balance(cfg.binding) == before
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.shared_claim_releases') == 0
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_post_release_capital_and_carry_use_active_claims_without_changing_historical_decisions(database):
    values, repo, cmd, _, _ = await setup(database)
    financial, cfg, owner = values[:3]
    try:
        old = await values[5].lookup('claim', cfg.binding)
        await repo.release(cmd, cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        # Release revokes pre-release readiness; an explicit new account/carry
        # observation counts remaining actual positions, not pending proceeds.
        assert not await database[0].fetchval('SELECT life_financial.carry_current_ready($1)', cfg.account_id)
        await refresh(database, values, short='1', cash='9.99')
        assert (await values[5].lookup('claim', cfg.binding)).decision == old.decision
        assert (await values[8].check_carry(cfg.binding)).allowed
        from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_shared_capital import (
            native_claim,
        )
        new = native_claim(owner, 'swap2', product='SWAP', side='SELL', amount='1')
        result = await values[5].reserve(ReserveCapital('newclaim', new), await values[5].cut(cfg.binding), owner)
        assert result.decision.allowed and result.changed
        assert len(await values[5].claims(cfg.binding)) == 1
        assert await database[0].fetchval('SELECT life_financial.shared_capital_verified($1)', cfg.account_id)
        assert await database[0].fetchval('SELECT life_financial.carry_verified($1)', cfg.account_id)
        assert await database[0].fetchval('SELECT life_financial.portfolio_verified($1)', cfg.account_id)
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['backend', 'process'])
@pytest.mark.parametrize('committed', [False, True])
async def test_release_commit_loss_cold_readback_does_not_repeat_refund_or_reopen_claim(database, monkeypatch, fault, committed):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.shared_release_process import exit_shared_release

    import asyncpg

    from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.shared_capital import PgSharedCapitalRepository
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
    values, repo, cmd, _, _ = await setup(database)
    financial, cfg, owner = values[:3]
    admin, cold = await asyncpg.connect(database[1]), None
    try:
        cut = (await financial.snapshot(cfg.binding)).cut
        if fault == 'process':
            await exit_shared_release(cfg, cmd, cut, owner, committed)
        else:
            original = financial._runtime._commit

            async def lost(tx):
                pid = tx._connection.get_server_pid()
                if committed:
                    await original(tx)
                assert await admin.fetchval('SELECT pg_terminate_backend($1)', pid)
                raise ConnectionError('synthetic shared release COMMIT reply loss')
            monkeypatch.setattr(financial._runtime, '_commit', lost)
            with pytest.raises(CommitUnknown):
                await repo.release(cmd, cut=cut, owner=owner)
        cold = PgSpotRepository(await PgRuntime.open(cfg))
        capital = PgSharedCapitalRepository(cold, owner=owner, observation=lambda: None, max_rows=64, max_bytes=1048576)
        restored = PgSharedReleaseRepository(capital)
        assert ((await restored.lookup(cmd.operation_id, cfg.binding)) is not None) is committed
        assert len(await capital.claims(cfg.binding)) == (0 if committed else 1)
        if not committed:
            await restored.release(cmd, cut=cut, owner=owner)
        assert (await restored.release(cmd, cut=cut, owner=owner)).replayed
        assert await capital.claims(cfg.binding) == ()
        assert (await cold.balance(cfg.binding)).total_quote_usdt == D('9.99')
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.shared_capital_claims') == 1
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.shared_claim_releases') == 1
    finally:
        await admin.close()
        if cold is not None:
            await cold.close()
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['basis', 'receipt', 'terminal'])
async def test_corrupt_release_is_not_accepted_as_free_capital(database, fault):
    values, repo, cmd, _, _ = await setup(database)
    financial, cfg, owner = values[:3]
    try:
        await repo.release(cmd, cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        table, query = dict(basis=('shared_claim_releases', "UPDATE life_financial.shared_claim_releases SET basis=jsonb_set(basis,'{money,quote}','\"20\"')"),
                            receipt=('operations', "UPDATE life_financial.operations SET receipt=jsonb_set(receipt,'{released}','false') WHERE operation_id='release1'"),
                            terminal=('shared_claim_releases', "UPDATE life_financial.shared_claim_releases SET terminal_operation_id='fill1'"))[fault]
        await database[0].execute(f'ALTER TABLE life_financial.{table} DISABLE TRIGGER USER')
        await database[0].execute(query)
        await database[0].execute(f'ALTER TABLE life_financial.{table} ENABLE TRIGGER USER')
        with pytest.raises(StorageError, match='PG_SHARED_RELEASE_UNVERIFIED'):
            await repo.frame(cfg.binding)
        with pytest.raises(StorageError, match='PG_CAPITAL_UNVERIFIED'):
            await values[5].claims(cfg.binding)
        assert (await financial.snapshot(cfg.binding)).recovery_required
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_generation_changes_at_physical_release_commit_roll_back_the_entire_retirement(database, monkeypatch):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.helpers import install_generation_fault
    values, repo, cmd, _, _ = await setup(database)
    financial, cfg, owner = values[:3]
    await install_generation_fault(database)
    original = financial._runtime._commit
    try:
        async def changed(tx):
            await tx._connection.execute('SELECT life_financial.test_change_generation($1)', cfg.account_id)
            await original(tx)
        monkeypatch.setattr(financial._runtime, '_commit', changed)
        with pytest.raises(StorageError, match='OWNER_EXPIRED_OR_CHANGED_AT_COMMIT'):
            await repo.release(cmd, cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        assert await repo.lookup(cmd.operation_id, cfg.binding) is None
        assert len(await values[5].claims(cfg.binding)) == 1
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.shared_claim_releases') == 0
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_release_source_expiry_during_commit_keeps_all_capital(database, monkeypatch):
    import asyncio
    values, repo, cmd, _, _ = await setup(database)
    financial, cfg, owner = values[:3]
    await database[0].execute('ALTER TABLE life_financial.portfolio_policies DISABLE TRIGGER USER')
    await database[0].execute('UPDATE life_financial.portfolio_policies SET max_age_ms=1000')
    await database[0].execute('ALTER TABLE life_financial.portfolio_policies ENABLE TRIGGER USER')
    await refresh(database, values, short='1', cash='9.99')
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.swap import PgSwapRepository
    await PgPortfolioRepository(PgSwapRepository(values[5])).measure(MeasurePortfolio('fresh'), cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
    original = financial._runtime._commit
    try:
        async def delayed(tx):
            await asyncio.sleep(1.1)
            await original(tx)
        monkeypatch.setattr(financial._runtime, '_commit', delayed)
        with pytest.raises(StorageError, match='PG_SHARED_RELEASE_SOURCE_UNQUALIFIED'):
            await repo.release(cmd, cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        assert len(await values[5].claims(cfg.binding)) == 1
        assert await repo.lookup(cmd.operation_id, cfg.binding) is None
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_release_context_mutation_after_commit_requires_original_readback(database, monkeypatch):
    from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown
    values, repo, cmd, _, _ = await setup(database)
    financial, cfg, owner = values[:3]
    original = financial._runtime._commit
    try:
        async def changed(tx):
            await original(tx)
            repo.owner = replace(owner, epoch=owner.epoch + 1)
        monkeypatch.setattr(financial._runtime, '_commit', changed)
        with pytest.raises(CommitUnknown):
            await repo.release(cmd, cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        repo.owner = owner
        assert (await repo.lookup(cmd.operation_id, cfg.binding)).released
        assert await values[5].claims(cfg.binding) == ()
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_retained_claim_capacity_does_not_become_unbounded_after_release(database):
    import json
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_shared_capital import native_claim

    from hummingbot.strategy_v2.life_liquidity.storage.contracts import fingerprint
    values, repo, cmd, _, _ = await setup(database)
    financial, cfg, owner = values[:3]
    # Explicit test-owner import retains a one-claim history budget; release
    # frees risk allocation, not the permanent deduplication storage bound.
    body = json.loads(await database[0].fetchval('SELECT body FROM life_financial.shared_capital_policies'))
    await database[0].execute('ALTER TABLE life_financial.shared_capital_policies DISABLE TRIGGER USER')
    await database[0].execute('UPDATE life_financial.shared_capital_policies SET max_claims=1,model_hash=$1', fingerprint({'policy': body, 'max_claims': 1}))
    await database[0].execute('ALTER TABLE life_financial.shared_capital_policies ENABLE TRIGGER USER')
    try:
        await repo.release(cmd, cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        await refresh(database, values, short='1', cash='9.99')
        with pytest.raises(StorageError, match='PG_CAPITAL_CLAIM_BOUND'):
            await values[5].reserve(ReserveCapital('newclaim', native_claim(owner, 'swap2', product='SWAP', side='SELL', amount='1')),
                                    await values[5].cut(cfg.binding), owner)
        assert await values[5].claims(cfg.binding) == ()
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.shared_capital_claims') == 1
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_native_spot_terminal_can_retire_its_shared_claim_without_touching_remaining_swap_exposure(database):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_financial_commands import order
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_lifecycle import terminal
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_shared_capital import native_claim
    values, repo, _, terminals, _ = await setup(database)
    financial, cfg, owner = values[:3]
    try:
        candidate = replace(native_claim(owner, 'intent1', amount='2'), wire_id='wire1')
        await values[5].reserve(ReserveCapital('spotclaim', candidate), await values[5].cut(cfg.binding), owner)
        await values[8].check_carry(cfg.binding)
        await financial.prepare(order(owner, risk_epoch=1, fee_buffer_base_life=D('0'), fee_buffer_quote_usdt=D('.002')),
                                cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        await financial.arm('operation1', cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        # Complete account identities cover the other product's permanent fill.
        cmd = replace(await terminal(financial, cfg, owner, account_trade_ids=('trade1',)), operation_id='spotterminal')
        result = await financial.confirm_terminal(cmd, cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        assert result.state == 'TERMINAL'
        await refresh(database, values, short='1', cash='9.99')
        await PgPortfolioRepository(terminals.swap).measure(MeasurePortfolio('spotnav'), cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        release = ReleaseSharedClaim('spotrelease', 'intent1', cmd.operation_id)
        before = await financial.snapshot(cfg.binding)
        assert (await repo.release(release, cut=before.cut, owner=owner)).released
        assert [c.intent_id for c in await values[5].claims(cfg.binding)] == ['swap1']
        assert await financial.balance(cfg.binding) == before.balance
        assert (await financial.snapshot(cfg.binding)).budget == before.budget
        assert await database[0].fetchval('SELECT life_financial.spot_lifecycle_verified($1)', cfg.account_id)
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_duplicate_release_identity_race_keeps_one_tombstone_and_no_money_delta(database):
    import asyncio
    values, repo, cmd, _, _ = await setup(database)
    financial, cfg, owner = values[:3]
    try:
        cut = (await financial.snapshot(cfg.binding)).cut
        first, second = await asyncio.gather(repo.release(cmd, cut=cut, owner=owner), repo.release(cmd, cut=cut, owner=owner))
        assert first.released and second.released and first.replayed != second.replayed
        assert await values[5].claims(cfg.binding) == ()
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.shared_claim_releases') == 1
        assert (await financial.balance(cfg.binding)).total_quote_usdt == D('9.99')
        with pytest.raises(StorageError, match='PG_CAPITAL_ID_RETIRED'):
            await repo.release(replace(cmd, operation_id='another'), cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_retirement_capacity_and_private_role_permissions_do_not_free_risk_implicitly(database):
    import asyncpg
    values, repo, cmd, _, _ = await setup(database)
    financial, cfg, owner = values[:3]
    runtime = await asyncpg.connect(cfg.dsn)
    reporter = await asyncpg.connect(cfg.dsn.replace('life_pg2_runtime@', 'life_pg2_reporter@'))
    try:
        for c in (runtime, reporter):
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await c.execute('DELETE FROM life_financial.shared_claim_releases')
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await c.fetchval('SELECT life_financial.shared_claim_active_at($1,$2,$3)', cfg.account_id, 'swap1', 9223372036854775807)
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await reporter.fetchval('SELECT life_financial.shared_release_command($1,$2,$3,$4,$5,$6,$7)', cfg.account_id, '{}', '{}', 0, '0' * 64, '0' * 64, '{}')
        await database[0].execute('ALTER TABLE life_financial.shared_release_policies DISABLE TRIGGER USER')
        await database[0].execute('UPDATE life_financial.shared_release_policies SET max_total_bytes=1')
        await database[0].execute('ALTER TABLE life_financial.shared_release_policies ENABLE TRIGGER USER')
        with pytest.raises(StorageError, match='PG_SHARED_RELEASE_BOUND'):
            await repo.release(cmd, cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        assert not repo.runtime.healthy
        assert len(await values[5].claims(cfg.binding)) == 1
    finally:
        await runtime.close()
        await reporter.close()
        await financial.close()
