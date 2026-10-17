"""Terminal bookkeeping requires original complete financial/source cuts.

Every account/order/value is synthetic; shared claims remain held.
"""
from dataclasses import replace
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_portfolio import (
    refresh,
    setup as portfolio_setup,
)

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.postgres.portfolio import MeasurePortfolio
from hummingbot.strategy_v2.life_liquidity.storage.postgres.swap_terminal import (
    ConfirmSwapTerminal,
    PgSwapTerminalRepository,
)


async def setup(database, *, filled=True, spot_options=None):
    values, portfolio, fills, fill = await portfolio_setup(database, spot_options=spot_options)
    financial, cfg, owner = values[:3]
    await database[0].execute('INSERT INTO life_financial.swap_terminal_policies VALUES($1,$2,64,1048576)', cfg.account_id, cfg.binding.policy_hash)
    if filled:
        await fills.apply_fills(fill, cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
    await refresh(database, values, short='1' if filled else '0', cash='9.99' if filled else '10')
    await portfolio.measure(MeasurePortfolio('nav1'), cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
    prepared = await database[0].fetchval("SELECT prepared_at_ms FROM life_financial.swap_preparations WHERE operation_id='prepare1'")
    command = ConfirmSwapTerminal('terminal1', 'prepare1', '987654321', 'filled' if filled else 'canceled',
                                  D('1') if filled else D('0'), ('trade1',) if filled else (), values[4][0].observed_at_ms,
                                  prepared, 'okx_joint_reconciled')
    return values, PgSwapTerminalRepository(portfolio.swap), command, fills, fill


@pytest.mark.asyncio
@pytest.mark.parametrize('filled', [True, False])
async def test_terminal_and_original_receipt_commit_once_without_refund_or_shared_release(database, filled):
    values, repo, cmd, _, _ = await setup(database, filled=filled)
    financial, cfg, owner = values[:3]
    try:
        before = await financial.balance(cfg.binding)
        cut = (await financial.snapshot(cfg.binding)).cut
        result = await repo.confirm(cmd, cut=cut, owner=owner)
        assert result.state == 'TERMINAL' and not result.trading_permission and not result.claim_released
        assert await database[0].fetchval("SELECT state FROM life_financial.send_attempts WHERE account_id=$1 AND intent_id='swap1'", cfg.account_id) == 'TERMINAL'
        assert (await repo.confirm(cmd, cut=cut, owner=owner)).replayed
        assert (await repo.lookup(cmd.operation_id, cfg.binding)).state == 'TERMINAL'
        assert await financial.balance(cfg.binding) == before
        assert len(await values[5].claims(cfg.binding)) == 1
        assert not (await financial.snapshot(cfg.binding)).recovery_required
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['quantity', 'fills', 'order', 'coverage', 'source', 'time'])
async def test_unqualified_terminal_keeps_original_order_and_money_and_retains_pending_halt(database, fault):
    values, repo, cmd, _, _ = await setup(database)
    financial, cfg, owner = values[:3]
    changes = dict(quantity={'cumulative_base_life': D('.5')}, fills={'trade_ids': ()}, order={'exchange_order_id': '123'},
                   coverage={'coverage_from_ms': cmd.observed_at_ms + 1}, source={'source': 'cancel_ack'}, time={'observed_at_ms': cmd.observed_at_ms - 10000})
    try:
        before = await financial.balance(cfg.binding)
        result = await repo.confirm(replace(cmd, **changes[fault]), cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        assert result.state == 'RECOVERY_REQUIRED' and not result.claim_released
        assert await database[0].fetchval("SELECT state FROM life_financial.send_attempts WHERE account_id=$1 AND intent_id='swap1'", cfg.account_id) == 'ACKED'
        assert await financial.balance(cfg.binding) == before
        frame = await repo.frame(cfg.binding)
        assert len(frame['pending']) == 1
        assert frame['pending'][0]['command'] == replace(cmd, **changes[fault]).json()
        assert (await financial.snapshot(cfg.binding)).recovery_required
        assert len(await values[5].claims(cfg.binding)) == 1
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_new_fill_after_terminal_is_permanent_pending_without_silent_rewrite_or_release(database):
    values, repo, cmd, fills, fill = await setup(database)
    financial, cfg, owner = values[:3]
    try:
        original_cut = (await financial.snapshot(cfg.binding)).cut
        await repo.confirm(cmd, cut=original_cut, owner=owner)
        before = await financial.balance(cfg.binding)
        result = await fills.apply_fills(replace(fill, operation_id='late'), cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        assert result.state == 'RECOVERY_REQUIRED'
        pending = (await fills.frame(cfg.binding))['pending']
        assert len(pending) == 1 and pending[0]['reason'] == 'SWAP_TERMINAL_RECONCILIATION_REQUIRED'
        assert await financial.balance(cfg.binding) == before
        assert (await financial.snapshot(cfg.binding)).recovery_required
        assert (await repo.lookup('terminal1', cfg.binding)).state == 'TERMINAL'
        assert len(await values[5].claims(cfg.binding)) == 1
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['backend', 'process'])
@pytest.mark.parametrize('committed', [False, True])
async def test_lost_terminal_commit_cold_original_readback_keeps_claim_and_never_refunds(database, monkeypatch, fault, committed):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.swap_terminal_process import exit_swap_terminal

    import asyncpg

    from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.shared_capital import PgSharedCapitalRepository
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.swap import PgSwapRepository
    values, repo, command, _, _ = await setup(database)
    financial, cfg, owner = values[:3]
    admin, cold = await asyncpg.connect(database[1]), None
    try:
        cut = (await financial.snapshot(cfg.binding)).cut
        if fault == 'process':
            await exit_swap_terminal(cfg, command, cut, owner, committed)
        else:
            original = financial._runtime._commit

            async def lost(tx):
                pid = tx._connection.get_server_pid()
                if committed:
                    await original(tx)
                assert await admin.fetchval('SELECT pg_terminate_backend($1)', pid)
                raise ConnectionError('synthetic SWAP terminal COMMIT reply loss')
            monkeypatch.setattr(financial._runtime, '_commit', lost)
            with pytest.raises(CommitUnknown):
                await repo.confirm(command, cut=cut, owner=owner)
        cold = PgSpotRepository(await PgRuntime.open(cfg))
        capital = PgSharedCapitalRepository(cold, owner=owner, observation=lambda: None, max_rows=64, max_bytes=1048576)
        restored = PgSwapTerminalRepository(PgSwapRepository(capital))
        assert ((await restored.lookup('terminal1', cfg.binding)) is not None) is committed
        if not committed:
            await restored.confirm(command, cut=cut, owner=owner)
        assert (await restored.confirm(command, cut=cut, owner=owner)).replayed
        assert (await cold.balance(cfg.binding)).total_quote_usdt == D('9.99')
        assert len(await capital.claims(cfg.binding)) == 1
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.swap_terminal_facts') == 1
    finally:
        await admin.close()
        if cold is not None:
            await cold.close()
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['source', 'receipt', 'state'])
async def test_corrupt_terminal_cannot_support_cold_read_or_risk(database, fault):
    from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
    values, repo, cmd, _, _ = await setup(database)
    financial, cfg, owner = values[:3]
    try:
        await repo.confirm(cmd, cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        table, query = dict(source=('swap_terminal_facts', "UPDATE life_financial.swap_terminal_facts SET command=jsonb_set(command,'{cumulative_base_life}','\"2\"')"),
                            receipt=('operations', "UPDATE life_financial.operations SET receipt=jsonb_set(receipt,'{claim_released}','true') WHERE operation_id='terminal1'"),
                            state=('send_attempts', "UPDATE life_financial.send_attempts SET state='ACKED' WHERE product='SWAP'"))[fault]
        await database[0].execute(f'ALTER TABLE life_financial.{table} DISABLE TRIGGER USER')
        await database[0].execute(query)
        await database[0].execute(f'ALTER TABLE life_financial.{table} ENABLE TRIGGER USER')
        with pytest.raises(StorageError, match='PG_SWAP_TERMINAL_UNVERIFIED'):
            await repo.frame(cfg.binding)
        assert (await financial.snapshot(cfg.binding)).recovery_required
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_physical_generation_change_rolls_back_terminal_state_and_keeps_claim(database, monkeypatch):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.helpers import install_generation_fault

    from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
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
            await repo.confirm(cmd, cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        assert await repo.lookup(cmd.operation_id, cfg.binding) is None
        assert await database[0].fetchval("SELECT state FROM life_financial.send_attempts WHERE intent_id='swap1'") == 'ACKED'
        assert len(await values[5].claims(cfg.binding)) == 1
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_context_change_after_confirmed_commit_requires_original_readback(database, monkeypatch):
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
            await repo.confirm(cmd, cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        repo.owner = owner
        assert (await repo.lookup('terminal1', cfg.binding)).state == 'TERMINAL'
        assert len(await values[5].claims(cfg.binding)) == 1
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_terminal_observation_limits_and_role_separation_never_enable_release(database):
    import asyncpg

    from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
    values, repo, cmd, _, _ = await setup(database)
    financial, cfg, owner = values[:3]
    await database[0].execute('ALTER TABLE life_financial.swap_terminal_policies DISABLE TRIGGER USER')
    await database[0].execute('UPDATE life_financial.swap_terminal_policies SET max_observations=1')
    await database[0].execute('ALTER TABLE life_financial.swap_terminal_policies ENABLE TRIGGER USER')
    runtime = await asyncpg.connect(cfg.dsn)
    reporter = await asyncpg.connect(cfg.dsn.replace('life_pg2_runtime@', 'life_pg2_reporter@'))
    try:
        invalid = replace(cmd, trade_ids=())
        assert (await repo.confirm(invalid, cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)).state == 'RECOVERY_REQUIRED'
        with pytest.raises(StorageError, match='PG_SWAP_TERMINAL_OBSERVATION_BOUND'):
            await repo.confirm(replace(cmd, operation_id='terminal2'), cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        assert not repo.runtime.healthy
        for c in (runtime, reporter):
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await c.execute('DELETE FROM life_financial.swap_terminal_facts')
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await c.fetchval('SELECT life_financial.swap_terminal_verified($1)', cfg.account_id)
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await reporter.fetchval('SELECT life_financial.swap_terminal_command($1,$2,$3,$4,$5,$6,$7)', cfg.account_id, '{}', '{}', 0, '0' * 64, '0' * 64, '{}')
        assert len(await values[5].claims(cfg.binding)) == 1
    finally:
        await runtime.close()
        await reporter.close()
        await financial.close()


@pytest.mark.asyncio
async def test_order_still_in_complete_pending_scope_cannot_be_confirmed_terminal(database):
    values, repo, cmd, _, _ = await setup(database)
    financial, cfg, owner = values[:3]
    try:
        claim = (await values[5].claims(cfg.binding))[0]
        values[4][0] = replace(values[4][0], pending=(claim,))
        await refresh(database, values, short='1', cash='9.99')
        from hummingbot.strategy_v2.life_liquidity.storage.postgres.portfolio import PgPortfolioRepository
        await PgPortfolioRepository(repo.swap).measure(MeasurePortfolio('still_open'), cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        updated = replace(cmd, observed_at_ms=values[4][0].observed_at_ms)
        result = await repo.confirm(updated, cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        assert result.state == 'RECOVERY_REQUIRED'
        assert (await repo.frame(cfg.binding))['pending'][0]['reason'] == 'SWAP_TERMINAL_ORDER_STILL_PENDING'
        assert len(await values[5].claims(cfg.binding)) == 1
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_current_source_expiring_during_commit_does_not_mark_order_terminal(database, monkeypatch):
    import asyncio

    from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.portfolio import PgPortfolioRepository
    values, repo, cmd, _, _ = await setup(database)
    financial, cfg, owner = values[:3]
    await database[0].execute('ALTER TABLE life_financial.portfolio_policies DISABLE TRIGGER USER')
    await database[0].execute('UPDATE life_financial.portfolio_policies SET max_age_ms=2000')
    await database[0].execute('ALTER TABLE life_financial.portfolio_policies ENABLE TRIGGER USER')
    await refresh(database, values, short='1', cash='9.99')
    await PgPortfolioRepository(repo.swap).measure(MeasurePortfolio('fresh'), cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
    cmd = replace(cmd, observed_at_ms=values[4][0].observed_at_ms)
    original = financial._runtime._commit
    try:
        async def delayed(tx):
            staged = await tx._connection.fetchval("SELECT receipt->>'state' FROM life_financial.operations WHERE operation_id='terminal1'")
            assert staged == 'TERMINAL', 'source must be accepted before injecting COMMIT expiry'
            now_ms = await tx._connection.fetchval('SELECT floor(extract(epoch FROM clock_timestamp())*1000)::bigint')
            await asyncio.sleep(max(0, (values[4][0].observed_at_ms + 2000 - now_ms) / 1000) + .1)
            await original(tx)
        monkeypatch.setattr(financial._runtime, '_commit', delayed)
        with pytest.raises(StorageError, match='PG_SWAP_TERMINAL_SOURCE_STALE_AT_COMMIT'):
            await repo.confirm(cmd, cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        assert await repo.lookup('terminal1', cfg.binding) is None
        assert await database[0].fetchval("SELECT state FROM life_financial.send_attempts WHERE intent_id='swap1'") == 'ACKED'
        assert len(await values[5].claims(cfg.binding)) == 1
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_truthful_late_terminal_after_halt_and_expiry_never_reopens_or_releases_risk(database):
    import asyncio
    from datetime import timedelta
    values, repo, cmd, _, _ = await setup(database, spot_options=dict(session_duration=timedelta(seconds=4)))
    financial, cfg, owner = values[:3]
    try:
        now = await database[0].fetchval('SELECT clock_timestamp()')
        await asyncio.sleep(max(0, (owner.expires_at - now).total_seconds()) + .05)
        assert await database[0].fetchval('SELECT clock_timestamp()>$1', owner.expires_at)
        await database[0].execute("UPDATE life_financial.accounts SET halted=true,mode='RECOVERY_ONLY' WHERE account_id=$1", cfg.account_id)
        before = await financial.balance(cfg.binding)
        result = await repo.confirm(cmd, cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        assert result.state == 'TERMINAL' and not result.claim_released and not result.trading_permission
        assert await financial.balance(cfg.binding) == before
        assert len(await values[5].claims(cfg.binding)) == 1
        assert await database[0].fetchval('SELECT halted FROM life_financial.accounts WHERE account_id=$1', cfg.account_id)
        from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
        from hummingbot.strategy_v2.life_liquidity.storage.products import ReserveCapital
        claim = replace((await values[5].claims(cfg.binding))[0], intent_id='newrisk', wire_id='NewWire')
        with pytest.raises(StorageError):
            await values[5].reserve(ReserveCapital('newrisk', claim), await values[5].cut(cfg.binding), owner)
        assert len(await values[5].claims(cfg.binding)) == 1
    finally:
        await financial.close()
