"""Late account facts are observations, never allocation or activation."""
import asyncio
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_shared_capital import native_claim, setup
from test.hummingbot.strategy_v2.life_liquidity.test_shared_capital import policy

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
from hummingbot.strategy_v2.life_liquidity.storage.products import ReserveCapital


async def refresh(database, feed):
    at = int((await database[0].fetchval('SELECT clock_timestamp()')).timestamp() * 1000)
    feed[0] = replace(feed[0], sequence=feed[0].sequence + 1, observed_at_ms=at)


@pytest.mark.asyncio
@pytest.mark.parametrize('state', ['halted', 'draining', 'recovery_only', 'expired'])
async def test_current_original_owner_can_refresh_facts_after_stop_without_allocating_or_healing(database, state):
    duration = timedelta(seconds=2 if state == 'expired' else 60)
    financial, cfg, owner, _, feed, repo = await setup(database, configured=policy(max_age_ms=5000), spot_options=dict(session_duration=duration))
    fresh = None
    try:
        claim = native_claim(owner)
        assert (await repo.reserve(ReserveCapital('claim1', claim), await repo.cut(cfg.binding), owner)).changed
        before = await financial.snapshot(cfg.binding)
        if state == 'expired':
            now = await database[0].fetchval('SELECT clock_timestamp()')
            await asyncio.sleep(max(0, (owner.expires_at - now).total_seconds()) + .02)
        elif state == 'halted':
            await database[0].execute('UPDATE life_financial.accounts SET halted=true')
        else:
            await database[0].execute('UPDATE life_financial.accounts SET mode=$1', state.upper())
        repo.runtime._healthy = False  # Prior local revocation must remain sticky.
        await refresh(database, feed)
        assert (await repo.check_capital(cfg.binding)).allowed
        assert not repo.runtime.healthy
        assert await financial.balance(cfg.binding) == before.balance
        assert (await financial.snapshot(cfg.binding)).budget == before.budget
        assert await repo.claims(cfg.binding) == (claim,)
        assert await database[0].fetchval('SELECT life_financial.shared_capital_verified($1)', cfg.account_id)
        from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
        from hummingbot.strategy_v2.life_liquidity.storage.postgres.shared_capital import PgSharedCapitalRepository
        from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
        fresh = PgSpotRepository(await PgRuntime.open(cfg))
        original = PgSharedCapitalRepository(fresh, owner=owner, observation=lambda: feed[0], max_rows=64, max_bytes=1048576)
        assert fresh._runtime.healthy
        with pytest.raises(StorageError, match='PG_OWNER_OR_BINDING_INVALID|PG_OWNER_EXPIRED'):
            await original.reserve(ReserveCapital('newclaim', native_claim(owner, 'newclaim')), await original.cut(cfg.binding), owner)
        status = await database[0].fetchrow('SELECT mode,halted FROM life_financial.accounts')
        assert status['mode'] == ('ACTIVE' if state in ('halted', 'expired') else state.upper())
        assert status['halted'] is (state == 'halted')
        assert await repo.lookup('newclaim', cfg.binding) is None
        assert (await repo.lookup('claim1', cfg.binding)).changed
    finally:
        if fresh is not None:
            await fresh.close()
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['uid', 'scope', 'wallet', 'stale', 'old_generation'])
async def test_late_refresh_cannot_adopt_foreign_partial_changed_or_stale_facts(database, fault):
    financial, cfg, owner, _, feed, repo = await setup(database, configured=policy(max_age_ms=5000))
    try:
        await repo.reserve(ReserveCapital('claim1', native_claim(owner)), await repo.cut(cfg.binding), owner)
        await database[0].execute('UPDATE life_financial.accounts SET halted=true')
        await refresh(database, feed)
        if fault == 'uid':
            feed[0] = replace(feed[0], account_uid='99999')
        elif fault == 'scope':
            feed[0] = replace(feed[0], scope_complete=False)
        elif fault == 'wallet':
            feed[0] = replace(feed[0], spot_cash_quote=D('100'))
        elif fault == 'stale':
            feed[0] = replace(feed[0], observed_at_ms=feed[0].observed_at_ms - 6000)
        else:
            await database[0].execute('UPDATE life_financial.accounts SET owner_generation=gen_random_uuid()')
        before = await database[0].fetchval('SELECT count(*) FROM life_financial.shared_capital_observations')
        with pytest.raises(StorageError):
            await repo.check_capital(cfg.binding)
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.shared_capital_observations') == before
        assert await database[0].fetchval('SELECT halted FROM life_financial.accounts')
        assert len(await repo.claims(cfg.binding)) == 1
        assert (await financial.balance(cfg.binding)).total_quote_usdt == 10
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_late_refresh_generation_change_at_physical_commit_preserves_original_snapshot(database, monkeypatch):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.helpers import install_generation_fault
    financial, cfg, owner, _, feed, repo = await setup(database, configured=policy(max_age_ms=5000))
    await install_generation_fault(database)
    try:
        await repo.check_capital(cfg.binding)
        await database[0].execute('UPDATE life_financial.accounts SET halted=true')
        await refresh(database, feed)
        before = await database[0].fetchval('SELECT count(*) FROM life_financial.shared_capital_observations')
        original = repo.runtime._commit

        async def changed(tx):
            await tx._connection.execute('SELECT life_financial.test_change_generation($1)', cfg.account_id)
            await original(tx)

        monkeypatch.setattr(repo.runtime, '_commit', changed)
        with pytest.raises(StorageError, match='PG_OWNER_EXPIRED_OR_CHANGED_AT_COMMIT'):
            await repo.check_capital(cfg.binding)
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.shared_capital_observations') == before
        assert await database[0].fetchval('SELECT halted FROM life_financial.accounts')
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_budget_halt_can_refresh_value_confirm_terminal_and_release_original_claim_without_activation(database):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_joint_batch import (
        setup as batch_setup,
    )
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_portfolio import (
        refresh as refresh_portfolio,
    )

    from hummingbot.strategy_v2.life_liquidity.storage.postgres.portfolio import MeasurePortfolio, PgPortfolioRepository
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.shared_release import (
        PgSharedReleaseRepository,
        ReleaseSharedClaim,
    )
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.swap_terminal import (
        ConfirmSwapTerminal,
        PgSwapTerminalRepository,
    )
    values, batch, command = await batch_setup(database, funding_budget='.1')
    financial, cfg, owner = values[:3]
    try:
        await database[0].execute('INSERT INTO life_financial.portfolio_policies VALUES($1,$2,1,1000,5000,64,1048576)', cfg.account_id, cfg.binding.policy_hash)
        await database[0].execute('INSERT INTO life_financial.swap_terminal_policies VALUES($1,$2,64,1048576)', cfg.account_id, cfg.binding.policy_hash)
        await database[0].execute('INSERT INTO life_financial.shared_release_policies VALUES($1,$2,64,1048576)', cfg.account_id, cfg.binding.policy_hash)
        assert (await batch.apply(command, cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)).state == 'APPLIED'
        assert await database[0].fetchval('SELECT halted FROM life_financial.accounts')
        before = await financial.snapshot(cfg.binding)
        values[7][0] = command.funding.bundle
        await refresh_portfolio(database, values, short='1', cash='9.89')
        assert not (await values[8].check_carry(cfg.binding)).allowed  # Exhausted cost remains exhausted.
        await PgPortfolioRepository(batch.swap).measure(MeasurePortfolio('nav1'), cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        prepared = await database[0].fetchval("SELECT prepared_at_ms FROM life_financial.swap_preparations WHERE operation_id='prepare1'")
        terminal = ConfirmSwapTerminal('terminal1', 'prepare1', '987654321', 'filled', D('1'), ('trade1',),
                                       values[4][0].observed_at_ms, prepared, 'okx_joint_reconciled')
        assert (await PgSwapTerminalRepository(batch.swap).confirm(terminal, cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)).state == 'TERMINAL'
        release = ReleaseSharedClaim('release1', 'swap1', terminal.operation_id)
        assert (await PgSharedReleaseRepository(values[5]).release(release, cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)).released
        assert await values[5].claims(cfg.binding) == ()
        assert await financial.balance(cfg.binding) == before.balance
        assert (await financial.snapshot(cfg.binding)).budget == before.budget
        assert await database[0].fetchval('SELECT halted FROM life_financial.accounts')
        assert await database[0].fetchval('SELECT gross_debits_usdt FROM life_financial.funding_observations') == D('.1')
        assert await database[0].fetchval("SELECT (life_financial.swap_fill_replay($1)->>'short_base')::numeric", cfg.account_id) == 1
        with pytest.raises(StorageError):
            await values[5].reserve(ReserveCapital('newclaim', native_claim(owner, 'newclaim')), await values[5].cut(cfg.binding), owner)
    finally:
        await financial.close()
