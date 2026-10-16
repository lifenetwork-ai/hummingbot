"""Actual DB ACK/fill/fee accounting over original protected SPOT identities."""
from dataclasses import replace
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_financial_commands import order, spot

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
from hummingbot.strategy_v2.life_liquidity.storage.financial import AckSpot, AttemptState, FillSpot, SpotFillObservation


async def sent(database):
    repo, cfg, owner = await spot(database)
    await database[0].execute('INSERT INTO life_financial.spot_fill_policies VALUES($1,$2,1000,4096)', cfg.account_id, cfg.binding.policy_hash)
    await repo.prepare(order(owner), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
    await repo.arm('operation1', cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
    return repo, cfg, owner


def ack(owner, at, **changes):
    return replace(AckSpot('ack1', 'operation1', 'intent1', 'wire1', 'LIFE-USDT', 'BUY',
                           str(owner.session_id), owner.epoch, '123456789', at), **changes)


def filled(owner, at, **changes):
    ms = int(at.timestamp() * 1000)
    row = SpotFillObservation('trade1', D('1'), D('1'), 'USDT', D('-.02'), ms, D('.9'), ms, ms, 'independent_market')
    return replace(FillSpot('fill1', 'operation1', 'intent1', 'wire1', 'LIFE-USDT', 'BUY',
                            str(owner.session_id), owner.epoch, '123456789', D('1'), (row,), at), **changes)


@pytest.mark.asyncio
async def test_ack_binds_order_and_replays_with_fresh_cut_without_releasing_holds(database):
    repo, cfg, owner = await sent(database)
    try:
        at = await database[0].fetchval('SELECT clock_timestamp()')
        cmd = ack(owner, at)
        result = await repo.acknowledge(cmd, cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        assert result.state == 'ACKED' and not result.replayed
        assert (await repo.acknowledge(cmd, cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)).replayed
        assert (await repo.lookup_ack('ack1', cfg.binding)).exchange_order_id == '123456789'
        assert (await repo.attempt('intent1', cfg.binding)).state is AttemptState.ACKED
        assert (await repo.balance(cfg.binding)).reserved_quote_usdt == D('2.2')
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('late', [False, True])
async def test_fill_before_ack_and_late_ack_apply_truth_without_rearm(database, late):
    repo, cfg, owner = await sent(database)
    try:
        if late:
            await database[0].execute("UPDATE life_financial.accounts SET mode='DRAINING',halted=true")
        at = await database[0].fetchval('SELECT clock_timestamp()')
        cmd = filled(owner, at)
        result = await repo.apply_fills(cmd, cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        assert result.state == 'APPLIED' and result.execution_loss_usdt == D('.12')
        view = await repo.snapshot(cfg.binding)
        assert view.balance.total_base_life == 6 and view.balance.total_quote_usdt == D('8.98')
        assert view.balance.reserved_quote_usdt == D('1.18') and view.balance.fee_reserved_quote_usdt == D('.18')
        assert view.budget.session_execution_loss_usdt == D('.12') and view.budget.session_subsidy_committed_usdt == D('.12')
        assert (await repo.reservation('intent1', cfg.binding)).remaining_base_life == 1
        assert view.recovery_required  # SEND_UNKNOWN until ACK, even with accounted fills.
        assert (await repo.apply_fills(cmd, cut=view.cut, owner=owner)).replayed
        await repo.acknowledge(ack(owner, at), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        assert (await repo.attempt('intent1', cfg.binding)).state is AttemptState.ACKED
        assert (await repo.lookup_fill('fill1', cfg.binding)).state == 'APPLIED'
        if late:
            with pytest.raises(StorageError):
                await repo.prepare(order(owner, operation_id='new', intent_id='new', client_order_id='wire2', quote_level=1),
                                   cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_progressive_fill_rebate_does_not_refund_fee_buffer_or_prior_loss(database):
    repo, cfg, owner = await sent(database)
    try:
        at = await database[0].fetchval('SELECT clock_timestamp()')
        await repo.acknowledge(ack(owner, at), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        cmd = filled(owner, at)
        await repo.apply_fills(cmd, cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        second = replace(cmd.fills[0], trade_id='trade2', independent_price_usdt=D('1.1'), signed_fee=D('.01'))
        next_cmd = replace(cmd, operation_id='fill2', cumulative_base_life=D('2'), fills=(cmd.fills[0], second))
        await repo.apply_fills(next_cmd, cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        view = await repo.snapshot(cfg.binding)
        assert view.balance.total_base_life == 7 and view.balance.total_quote_usdt == D('7.99')
        assert view.balance.fee_reserved_quote_usdt == D('.18') and view.balance.reserved_quote_usdt == D('.18')
        assert view.budget.session_execution_loss_usdt == D('.12') and view.budget.session_subsidy_committed_usdt == D('.12')
        assert (await repo.reservation('intent1', cfg.binding)).remaining_base_life == 0
        assert (await repo.attempt('intent1', cfg.binding)).state is AttemptState.ACKED
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_life_fee_is_debited_in_life_and_independently_valued(database):
    repo, cfg, owner = await sent(database)
    try:
        at = await database[0].fetchval('SELECT clock_timestamp()')
        cmd = filled(owner, at)
        cmd = replace(cmd, fills=(replace(cmd.fills[0], fee_currency='LIFE'),))
        await repo.apply_fills(cmd, cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        view = await repo.snapshot(cfg.binding)
        assert view.balance.total_base_life == D('5.98') and view.balance.total_quote_usdt == 9
        assert view.balance.fee_reserved_base_life == D('.08')
        assert view.budget.session_execution_loss_usdt == D('.118')
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_conflicting_fee_and_incomplete_cumulative_snapshot_leave_every_projection_unchanged(database):
    repo, cfg, owner = await sent(database)
    try:
        at = await database[0].fetchval('SELECT clock_timestamp()')
        cmd = filled(owner, at)
        await repo.apply_fills(cmd, cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        before = await repo.snapshot(cfg.binding)
        for row in (replace(cmd.fills[0], signed_fee=D('-.03')), replace(cmd.fills[0], trade_id='missing_old')):
            with pytest.raises(StorageError):
                await repo.apply_fills(replace(cmd, operation_id='other', fills=(row,)), cut=before.cut, owner=owner)
            assert (await repo.snapshot(cfg.binding)).cut == before.cut
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.fills') == 1
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('field,value', [('client_order_id', 'foreign'), ('exchange_order_id', '999'), ('side', 'SELL'), ('epoch', 2)])
async def test_observations_reject_foreign_source_identity_without_changing_money(database, field, value):
    repo, cfg, owner = await sent(database)
    try:
        at = await database[0].fetchval('SELECT clock_timestamp()')
        await repo.acknowledge(ack(owner, at), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        before = await repo.snapshot(cfg.binding)
        with pytest.raises(StorageError):
            await repo.apply_fills(replace(filled(owner, at), **{field: value}), cut=before.cut, owner=owner)
        assert (await repo.snapshot(cfg.binding)).cut == before.cut
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_actual_losses_beyond_policy_limit_are_booked_and_deny_new_risk(database):
    repo, cfg, owner = await sent(database)
    try:
        at = await database[0].fetchval('SELECT clock_timestamp()')
        cmd = filled(owner, at)
        row = replace(cmd.fills[0], quantity_base_life=D('2'), independent_price_usdt=D('.001'))
        cmd = replace(cmd, cumulative_base_life=D('2'), fills=(row,))
        await repo.apply_fills(cmd, cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        view = await repo.snapshot(cfg.binding)
        assert view.budget.loss_exhausted and view.budget.session_execution_loss_usdt == D('2.018')
        assert view.budget.session_subsidy_committed_usdt == D('2.018')
        assert view.balance.total_quote_usdt == D('7.98')
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_fee_overrun_books_funding_deficit_without_releasing_other_order_holds(database):
    repo, cfg, owner = await spot(database)
    try:
        await database[0].execute('INSERT INTO life_financial.spot_fill_policies VALUES($1,$2,1000,4096)', cfg.account_id, cfg.binding.policy_hash)
        await repo.prepare(order(owner), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        await repo.prepare(order(owner, operation_id='other', intent_id='other', client_order_id='wire2', quote_level=1, quantity_base=D('7')),
                           cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        await repo.arm('operation1', cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        at = await database[0].fetchval('SELECT clock_timestamp()')
        cmd = filled(owner, at)
        cmd = replace(cmd, fills=(replace(cmd.fills[0], signed_fee=D('-1')),))
        await repo.apply_fills(cmd, cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        view = await repo.snapshot(cfg.binding)
        assert view.balance.total_quote_usdt == 8 and view.balance.reserved_quote_usdt == D('8.2')
        assert view.balance.available_quote_usdt == 0 and view.recovery_required
        assert await database[0].fetchval("SELECT funding_deficit FROM life_financial.balances WHERE asset='USDT'") == D('.2')
        assert await database[0].fetchval('SELECT halted FROM life_financial.accounts')
        assert (await repo.reservation('other', cfg.binding)).remaining_base_life == 7
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_observations_use_reserved_capacity_when_new_risk_admission_is_closed(database):
    repo, cfg, owner = await sent(database)
    try:
        runtime = repo._runtime
        runtime._healthy = False
        runtime._counts[False] = cfg.max_pending
        at = await database[0].fetchval('SELECT clock_timestamp()')
        await repo.acknowledge(ack(owner, at), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        assert not runtime.healthy
        assert (await repo.lookup_ack('ack1', cfg.binding)).state == 'ACKED'
    finally:
        repo._runtime._counts[False] = 0
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['ACK', 'FILL'])
async def test_original_actor_can_account_after_owner_expiry_but_cannot_allocate(database, kind):
    repo, cfg, owner = await sent(database)
    try:
        expiry = await database[0].fetchval("SELECT clock_timestamp()-interval '1 millisecond'")
        owner = replace(owner, expires_at=expiry)
        await database[0].execute("UPDATE life_financial.accounts SET owner_until=$1,mode='RECOVERY_ONLY',halted=true", expiry)
        at = await database[0].fetchval('SELECT clock_timestamp()')
        if kind == 'ACK':
            await repo.acknowledge(ack(owner, at), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        else:
            await repo.apply_fills(filled(owner, at), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        with pytest.raises(StorageError):
            await repo.prepare(order(owner, operation_id='new', intent_id='new', client_order_id='wire2', quote_level=1),
                               cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['ACK', 'FILL'])
async def test_generation_changed_at_commit_aborts_entire_observation(database, monkeypatch, kind):
    from uuid import uuid4
    repo, cfg, owner = await sent(database)
    original = repo._runtime._commit

    async def changed(tx):
        await tx._connection.execute('UPDATE life_financial.accounts SET owner_generation=$1', uuid4())
        await original(tx)
    monkeypatch.setattr(repo._runtime, '_commit', changed)
    try:
        at = await database[0].fetchval('SELECT clock_timestamp()')
        with pytest.raises(StorageError):
            if kind == 'ACK':
                await repo.acknowledge(ack(owner, at), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
            else:
                await repo.apply_fills(filled(owner, at), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        assert await repo.lookup_ack('ack1', cfg.binding) is None
        assert await repo.lookup_fill('fill1', cfg.binding) is None
        assert (await repo.balance(cfg.binding)).total_quote_usdt == 10
        assert (await repo.balance(cfg.binding)).reserved_quote_usdt == D('2.2')
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['ACK', 'FILL'])
@pytest.mark.parametrize('committed', [False, True])
async def test_lost_observation_commit_readback_and_exact_retry_never_double_account(database, monkeypatch, kind, committed):
    import asyncpg

    from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
    repo, cfg, owner = await sent(database)
    admin = await asyncpg.connect(database[1])
    at = await admin.fetchval('SELECT clock_timestamp()')
    cmd = ack(owner, at) if kind == 'ACK' else filled(owner, at)
    original = repo._runtime._commit

    async def lost(tx):
        pid = tx._connection.get_server_pid()
        if committed:
            await original(tx)
        await admin.fetchval('SELECT pg_terminate_backend($1)', pid)
        raise ConnectionError('synthetic lost observation reply')
    monkeypatch.setattr(repo._runtime, '_commit', lost)
    try:
        with pytest.raises(CommitUnknown):
            method = repo.acknowledge if kind == 'ACK' else repo.apply_fills
            await method(cmd, cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        assert not repo._runtime.healthy
        lookup = repo.lookup_ack if kind == 'ACK' else repo.lookup_fill
        assert ((await lookup(cmd.operation_id, cfg.binding)) is not None) is committed
    finally:
        await admin.close()
        await repo.close()
    cold = PgSpotRepository(await PgRuntime.open(cfg))
    try:
        method = cold.acknowledge if kind == 'ACK' else cold.apply_fills
        result = await method(cmd, cut=(await cold.snapshot(cfg.binding)).cut, owner=owner)
        assert result.replayed is committed
        view = await cold.snapshot(cfg.binding)
        assert view.balance.total_quote_usdt == (10 if kind == 'ACK' else D('8.98'))
        assert view.balance.reserved_quote_usdt == (D('2.2') if kind == 'ACK' else D('1.18'))
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.fills') == (0 if kind == 'ACK' else 1)
    finally:
        await cold.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['ACK', 'FILL'])
@pytest.mark.parametrize('committed', [False, True])
async def test_actual_process_exit_before_after_observation_commit_preserves_funds(database, kind, committed):
    import asyncio
    import json
    import sys
    from dataclasses import fields

    from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
    repo, cfg, owner = await sent(database)
    at = await database[0].fetchval('SELECT clock_timestamp()')
    cmd = ack(owner, at) if kind == 'ACK' else filled(owner, at)
    try:
        config = {f.name: getattr(cfg, f.name) for f in fields(cfg)}
        config.update(account_id=str(cfg.account_id), database_lineage=str(cfg.database_lineage), binding=cfg.binding.json())
        payload = json.dumps(dict(config=config, owner=owner.json(), cut=(await repo.snapshot(cfg.binding)).cut.json(), command=cmd.json(), kind=kind, committed=committed))
        child = r'''
import asyncio, json, os, sys
from datetime import datetime
from uuid import UUID
from hummingbot.strategy_v2.life_liquidity.storage.contracts import OwnerToken, StateBinding
from hummingbot.strategy_v2.life_liquidity.storage.financial import AckSpot, FillSpot, FinancialCut
from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgConfig, PgRuntime
from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
async def run():
    data=json.load(sys.stdin); config=data['config']
    config['binding']=StateBinding.from_json(config['binding'])
    for key in ('account_id','database_lineage'): config[key]=UUID(config[key])
    runtime=await PgRuntime.open(PgConfig(**config)); repo=PgSpotRepository(runtime)
    original=runtime._commit
    async def die(tx):
        if data['committed']: await original(tx)
        os._exit(73)
    runtime._commit=die
    owner=OwnerToken.from_json(data['owner']); cut=FinancialCut.from_json(data['cut'])
    if data['kind']=='ACK':
        command=data['command']; command['observed_at']=datetime.fromisoformat(command['observed_at'])
        await repo.acknowledge(AckSpot(**command),cut=cut,owner=owner)
    else: await repo.apply_fills(FillSpot.from_json(data['command']),cut=cut,owner=owner)
asyncio.run(run())
'''
        process = await asyncio.create_subprocess_exec(sys.executable, '-c', child, stdin=asyncio.subprocess.PIPE,
                                                       stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        try:
            _, stderr = await asyncio.wait_for(process.communicate(payload.encode()), 15)
        except BaseException:
            process.kill()
            await process.wait()
            raise
        assert process.returncode == 73, stderr.decode()
    finally:
        await repo.close()
    cold = PgSpotRepository(await PgRuntime.open(cfg))
    try:
        lookup = cold.lookup_ack if kind == 'ACK' else cold.lookup_fill
        assert ((await lookup(cmd.operation_id, cfg.binding)) is not None) is committed
        view = await cold.snapshot(cfg.binding)
        assert view.balance.total_quote_usdt == (D('8.98') if committed and kind == 'FILL' else 10)
        assert view.balance.reserved_quote_usdt == (D('1.18') if committed and kind == 'FILL' else D('2.2'))
        assert (await cold.attempt('intent1', cfg.binding)).state is (AttemptState.ACKED if committed and kind == 'ACK' else AttemptState.SEND_UNKNOWN)
    finally:
        await cold.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('side', ['BUY', 'SELL'])
@pytest.mark.parametrize('currency', ['LIFE', 'USDT'])
async def test_file_and_postgres_ack_fill_fee_balances_budget_and_states_match(database, tmp_path, side, currency):
    from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_financial_contracts import (
        BUDGET,
        CAPACITY,
        NOW,
        RISK,
        create_fixture,
    )

    from hummingbot.strategy_v2.life_liquidity.storage.financial import SpotFillPolicy
    from hummingbot.strategy_v2.life_liquidity.storage.legacy_spot import open_legacy_spot
    repo, cfg, owner = await spot(database)
    files, binding, native_owner = create_fixture(tmp_path)
    native = await open_legacy_spot(files, binding=binding, limits=CAPACITY, risk_limits=RISK,
                                    subsidy_policy=BUDGET, loss_policy=BUDGET, config_version=1, fill_policy=SpotFillPolicy(1000),
                                    account_lock_root=tmp_path / 'locks', owner=native_owner, create_binding=True, clock=lambda: NOW)
    try:
        await database[0].execute('INSERT INTO life_financial.spot_fill_policies VALUES($1,$2,1000,4096)', cfg.account_id, cfg.binding.policy_hash)
        for store, scope, token in ((repo, cfg.binding, owner), (native, binding, native_owner)):
            await store.prepare(order(token, side=side), cut=(await store.snapshot(scope)).cut, owner=token)
            await store.arm('operation1', cut=(await store.snapshot(scope)).cut, owner=token)
            at = await database[0].fetchval('SELECT clock_timestamp()') if store is repo else NOW
            await store.acknowledge(ack(token, at, side=side), cut=(await store.snapshot(scope)).cut, owner=token)
            cmd = filled(token, at, side=side)
            row = replace(cmd.fills[0], fee_currency=currency, independent_price_usdt=D('.9') if side == 'BUY' else D('1.1'))
            await store.apply_fills(replace(cmd, fills=(row,)), cut=(await store.snapshot(scope)).cut, owner=token)
        pg, legacy = await repo.snapshot(cfg.binding), await native.snapshot(binding)
        assert pg.balance == legacy.balance and pg.budget == legacy.budget
        assert (await repo.reservation('intent1', cfg.binding)).remaining_base_life == (await native.reservation('intent1', binding)).remaining_base_life
        assert (await repo.attempt('intent1', cfg.binding)).state == (await native.attempt('intent1', binding)).state
    finally:
        await repo.close()
        await native.close()


@pytest.mark.asyncio
async def test_invalid_later_row_rolls_back_earlier_fill_fee_and_budget_writes(database):
    repo, cfg, owner = await sent(database)
    try:
        at = await database[0].fetchval('SELECT clock_timestamp()')
        cmd = filled(owner, at)
        second = replace(cmd.fills[0], trade_id='trade2', price_usdt=D('1.01'))
        cmd = replace(cmd, cumulative_base_life=D('2'), fills=(cmd.fills[0], second))
        before = await repo.snapshot(cfg.binding)
        with pytest.raises(StorageError):
            await repo.apply_fills(cmd, cut=before.cut, owner=owner)
        assert (await repo.snapshot(cfg.binding)).cut == before.cut
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.fills') == 0
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.fees') == 0
        assert await repo.lookup_fill('fill1', cfg.binding) is None
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_unrepresentable_negative_wallet_is_durably_pending_and_cold_halted(database):
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
    repo, cfg, owner = await sent(database)
    at = await database[0].fetchval('SELECT clock_timestamp()')
    cmd = filled(owner, at)
    cmd = replace(cmd, fills=(replace(cmd.fills[0], signed_fee=D('-20')),))
    try:
        result = await repo.apply_fills(cmd, cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        assert result.state == 'RECOVERY_REQUIRED'
        assert await database[0].fetchval('SELECT halted FROM life_financial.accounts')
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.fills') == 0
        assert await database[0].fetchval("SELECT command#>>'{fills,0,signed_fee}' FROM life_financial.spot_pending_observations") == '-20'
        assert (await repo.balance(cfg.binding)).total_quote_usdt == 10
        assert (await repo.balance(cfg.binding)).reserved_quote_usdt == D('2.2')
    finally:
        await repo.close()
    cold = PgSpotRepository(await PgRuntime.open(cfg))
    try:
        assert (await cold.lookup_fill('fill1', cfg.binding)).state == 'RECOVERY_REQUIRED'
        assert (await cold.snapshot(cfg.binding)).recovery_required
        assert (await cold.apply_fills(cmd, cut=(await cold.snapshot(cfg.binding)).cut, owner=owner)).replayed
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.spot_pending_observations') == 1
        await cold.acknowledge(ack(owner, at), cut=(await cold.snapshot(cfg.binding)).cut, owner=owner)
        later = replace(cmd, operation_id='fill2', cumulative_base_life=D('2'),
                        fills=(cmd.fills[0], replace(cmd.fills[0], trade_id='trade2', signed_fee=D('.01'))))
        assert (await cold.apply_fills(later, cut=(await cold.snapshot(cfg.binding)).cut, owner=owner)).state == 'RECOVERY_REQUIRED'
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.spot_pending_observations') == 2
        assert (await cold.balance(cfg.binding)).total_quote_usdt == 10
        with pytest.raises(StorageError):
            await cold.prepare(order(owner, operation_id='new', intent_id='new', client_order_id='wire2', quote_level=1),
                               cut=(await cold.snapshot(cfg.binding)).cut, owner=owner)
    finally:
        await cold.close()


@pytest.mark.asyncio
async def test_unmapped_observation_role_is_rejected_before_waiting_on_foreign_account_lock(database):
    import json

    import asyncpg

    repo, cfg, owner = await sent(database)
    c = database[0]
    await c.execute("DELETE FROM life_financial.access_bindings WHERE principal='life_pg2_runtime'")
    tx = c.transaction()
    await tx.start()
    await c.execute('SELECT 1 FROM life_financial.accounts WHERE account_id=$1 FOR UPDATE', cfg.account_id)
    untrusted = await asyncpg.connect(cfg.dsn)
    try:
        await untrusted.execute("SET statement_timeout='150ms'")
        at = await c.fetchval('SELECT clock_timestamp()')
        with pytest.raises(asyncpg.RaiseError):
            await untrusted.fetchval('SELECT life_financial.spot_observe($1,$2,$3,$4,$5,$6,$7,$8)', cfg.account_id,
                                     json.dumps(cfg.binding.json()), json.dumps(owner.json()), 2, 'a' * 64, 'b' * 64,
                                     'SPOT_ACK', json.dumps(ack(owner, at).json()))
    finally:
        await untrusted.close()
        await tx.rollback()
        await repo.close()
