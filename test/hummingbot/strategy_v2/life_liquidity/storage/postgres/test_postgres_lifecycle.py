"""Real disposable PostgreSQL order lifecycle; synthetic qualified observations."""
import asyncio
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_financial_commands import order, spot
from uuid import uuid4

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import AtomicScope, StorageError
from hummingbot.strategy_v2.life_liquidity.storage.spot_lifecycle import SpotLifecycleCommand
from hummingbot.strategy_v2.life_liquidity.storage.terminal import SpotTerminalFacts, TerminalSpot


def lifecycle(action='ABORT_PREPARED', **changes):
    fields = dict(operation_id='lifecycle1', prepare_operation_id='operation1', intent_id='intent1', client_order_id='wire1', action=action)
    fields.update(changes)
    return SpotLifecycleCommand(**fields)


async def prepared(database, *, side='BUY', arm=False, **settings):
    repo, cfg, owner = await spot(database, **settings)
    # Explicit synthetic opening anchor; terminal facts must reconcile booked money.
    await database[0].execute("INSERT INTO life_financial.spot_capital_policies VALUES($1,$2,5,10,1,$3,'100',4096)",
                              cfg.account_id, cfg.binding.policy_hash, owner.valid_from)
    await database[0].execute('INSERT INTO life_financial.spot_fill_policies VALUES($1,$2,1000,4096)', cfg.account_id, cfg.binding.policy_hash)
    await repo.prepare(order(owner, side=side), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
    if arm:
        await repo.arm('operation1', cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
    return repo, cfg, owner


async def terminal(repo, cfg, owner, *, side='BUY', fills=(), **changes):
    view = await repo.snapshot(cfg.binding)
    at = await repo._runtime._admit(lambda c: c.fetchval('SELECT clock_timestamp()'), safety=True)
    facts = SpotTerminalFacts(cfg.binding, at, 'CANCELED', sum((row.quantity_base_life for row in fills), D('0')),
                              D('2'), D('1'), fills, view.balance.total_base_life, view.balance.total_quote_usdt,
                              D('0'), D('0'), tuple(row.trade_id for row in fills), (), (), (), ('wire1',), True, True, True)
    return TerminalSpot('terminal1', 'operation1', 'intent1', 'wire1', 'LIFE-USDT', side,
                        str(owner.session_id), owner.epoch, '123456789', replace(facts, **changes))


@pytest.mark.asyncio
@pytest.mark.parametrize('side', ['BUY', 'SELL'])
async def test_abort_proven_unsent_releases_holds_once_and_preserves_identity(database, side):
    repo, cfg, owner = await prepared(database, side=side)
    try:
        before = await repo.snapshot(cfg.binding)
        command = lifecycle()
        result = await repo.apply_lifecycle(command, cut=before.cut, owner=owner)
        assert result.state == 'ABORTED_BEFORE_SEND' and result.atomic_scope is AtomicScope.ACCOUNT
        assert (await repo.apply_lifecycle(command, cut=before.cut, owner=owner)).replayed
        view = await repo.snapshot(cfg.binding)
        assert view.balance.reserved_quote_usdt == view.balance.reserved_base_life == 0
        assert (view.balance.total_base_life, view.balance.total_quote_usdt) == (D('5'), D('10'))
        assert view.budget.campaign_subsidy_committed_usdt == 0 and not view.recovery_required
        assert (await repo.reservation('intent1', cfg.binding)).state == 'TERMINAL'
        with pytest.raises(StorageError):
            await repo.arm('operation1', cut=view.cut, owner=owner)
        with pytest.raises(StorageError):
            await repo.prepare(order(owner, operation_id='new', intent_id='new'), cut=view.cut, owner=owner)
        await repo.prepare(order(owner, operation_id='new', intent_id='new', client_order_id='wire2'), cut=view.cut, owner=owner)
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_unknown_send_cannot_abort_and_cancel_retains_money_and_budget(database):
    repo, cfg, owner = await prepared(database, arm=True)
    try:
        before = await repo.snapshot(cfg.binding)
        with pytest.raises(StorageError, match='NOT_PROVEN_UNSENT'):
            await repo.apply_lifecycle(lifecycle(), cut=before.cut, owner=owner)
        command = lifecycle('REQUEST_CANCEL')
        result = await repo.apply_lifecycle(command, cut=before.cut, owner=owner)
        assert result.state == 'SEND_UNKNOWN'
        assert (await repo.attempt('intent1', cfg.binding)).cancel_requested
        assert (await repo.reservation('intent1', cfg.binding)).state == 'CANCEL_PENDING'
        assert (await repo.apply_lifecycle(command, cut=before.cut, owner=owner)).replayed
        after = await repo.snapshot(cfg.binding)
        assert (after.balance, after.budget) == (before.balance, before.budget)
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('side', ['BUY', 'SELL'])
async def test_zero_fill_terminal_releases_only_reconciled_holds(database, side):
    repo, cfg, owner = await prepared(database, side=side, arm=True)
    try:
        command = await terminal(repo, cfg, owner, side=side)
        cut = (await repo.snapshot(cfg.binding)).cut
        result = await repo.confirm_terminal(command, cut=cut, owner=owner)
        assert result.state == 'TERMINAL' and result.subsidy_settled and result.atomic_scope is AtomicScope.ACCOUNT
        assert (await repo.confirm_terminal(command, cut=cut, owner=owner)).replayed
        assert (await repo.resume_terminal(command, cut=cut, owner=owner)).replayed
        assert (await repo.lookup_terminal('terminal1', cfg.binding)).state == 'TERMINAL'
        view = await repo.snapshot(cfg.binding)
        assert view.balance.reserved_base_life == view.balance.reserved_quote_usdt == 0
        assert view.budget.session_subsidy_committed_usdt == 0 and not view.recovery_required
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('bad', ['scope', 'wallet', 'trade', 'wire', 'liability', 'stale', 'owner', 'payload'])
async def test_unqualified_terminal_facts_cannot_release_anything(database, bad):
    repo, cfg, owner = await prepared(database, arm=True)
    try:
        command = await terminal(repo, cfg, owner)
        token = owner
        changes = {
            'scope': dict(bill_scope_complete=False), 'wallet': dict(account_quote_usdt=D('11')),
            'trade': dict(account_trade_ids=('unknown',)), 'wire': dict(pending_wire_ids=('wire1',)),
            'liability': dict(liability_quote_usdt=D('1')), 'stale': dict(observed_at=owner.valid_from),
            'payload': dict(limit_price_usdt=D('2')),
        }
        if bad == 'owner':
            token = replace(owner, generation=uuid4())
        else:
            command = replace(command, facts=replace(command.facts, **changes[bad]))
        before = await repo.snapshot(cfg.binding)
        with pytest.raises(StorageError):
            await repo.confirm_terminal(command, cut=before.cut, owner=token)
        assert (await repo.snapshot(cfg.binding)).cut == before.cut
        assert await repo.lookup_terminal('terminal1', cfg.binding) is None
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('side', ['BUY', 'SELL'])
@pytest.mark.parametrize('quantity', [D('1'), D('2')])
async def test_filled_terminal_releases_order_holds_without_refunding_loss_or_subsidy(database, side, quantity):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_spot_observations import filled
    repo, cfg, owner = await prepared(database, side=side, arm=True)
    try:
        at = await database[0].fetchval('SELECT clock_timestamp()')
        original = filled(owner, at, side=side)
        row = replace(original.fills[0], quantity_base_life=quantity)
        original = replace(original, cumulative_base_life=quantity, fills=(row,))
        await repo.apply_lifecycle(lifecycle('REQUEST_CANCEL'), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        await repo.apply_fills(original, cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        before = await repo.snapshot(cfg.binding)
        command = await terminal(repo, cfg, owner, side=side, fills=(row,), exchange_state='FILLED' if quantity == 2 else 'CANCELED')
        result = await repo.confirm_terminal(command, cut=before.cut, owner=owner)
        after = await repo.snapshot(cfg.binding)
        assert not result.subsidy_settled and after.budget == before.budget
        assert after.balance.reserved_base_life == after.balance.reserved_quote_usdt == 0
        assert (after.balance.total_base_life, after.balance.total_quote_usdt) == (before.balance.total_base_life, before.balance.total_quote_usdt)
        assert not after.recovery_required
        assert (await repo.reservation('intent1', cfg.binding)).filled_base_life == quantity
        assert (await repo.attempt('intent1', cfg.binding)).exchange_order_id == command.exchange_order_id
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_cancel_retry_binds_policy_counter_and_server_elapsed_time(database):
    repo, cfg, owner = await prepared(database, arm=True, max_order_fact_age_ms=2000, cancel_retry_interval_ms=1000)
    try:
        await asyncio.sleep(1.2)
        at = await database[0].fetchval('SELECT clock_timestamp()')
        # A qualified but backdated observation cannot accelerate actual retry.
        first = lifecycle('RECORD_CANCEL_ATTEMPT', attempted_at=at - timedelta(seconds=1.1), expected_cancel_attempts=0, retry_interval_ms=1000)
        cut = (await repo.snapshot(cfg.binding)).cut
        await repo.apply_lifecycle(first, cut=cut, owner=owner)
        assert (await repo.apply_lifecycle(first, cut=cut, owner=owner)).replayed
        assert (await repo.attempt('intent1', cfg.binding)).cancel_attempts == 1
        for changes in (dict(expected_cancel_attempts=0), dict(retry_interval_ms=1), dict(attempted_at=at + timedelta(seconds=20))):
            second = replace(first, operation_id='invalid', expected_cancel_attempts=1, attempted_at=at)
            with pytest.raises(StorageError):
                await repo.apply_lifecycle(replace(second, **changes), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        second = replace(first, operation_id='retry', expected_cancel_attempts=1, attempted_at=await database[0].fetchval('SELECT clock_timestamp()'))
        with pytest.raises(StorageError, match='RETRY_NOT_DUE'):
            await repo.apply_lifecycle(second, cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        await asyncio.sleep(1.01)
        second = replace(second, attempted_at=await database[0].fetchval('SELECT clock_timestamp()'))
        await repo.apply_lifecycle(second, cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        row = await repo.attempt('intent1', cfg.binding)
        assert row.cancel_attempts == 2 and row.last_cancel_attempt_at == second.attempted_at
        assert (await repo.balance(cfg.binding)).reserved_quote_usdt == D('2.2')
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['abort', 'cancel', 'terminal'])
async def test_current_writer_can_close_after_halt_and_expiry_without_rearming(database, stage):
    repo, cfg, owner = await prepared(database, arm=stage != 'abort', session_duration=timedelta(seconds=1))
    try:
        await database[0].execute("UPDATE life_financial.accounts SET halted=true,mode='DRAINING'")
        await asyncio.sleep(max(0, (owner.expires_at - await database[0].fetchval('SELECT clock_timestamp()')).total_seconds()) + .01)
        if stage == 'terminal':
            await repo.confirm_terminal(await terminal(repo, cfg, owner), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        else:
            await repo.apply_lifecycle(lifecycle('ABORT_PREPARED' if stage == 'abort' else 'REQUEST_CANCEL'), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        view = await repo.snapshot(cfg.binding)
        assert view.session.state != 'ACTIVE'
        assert view.balance.reserved_quote_usdt == (D('2.2') if stage == 'cancel' else 0)
        assert await database[0].fetchval('SELECT halted FROM life_financial.accounts')
        with pytest.raises(StorageError):
            await repo.prepare(order(owner, operation_id='new', intent_id='new', client_order_id='wire2'), cut=view.cut, owner=owner)
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_unknown_sibling_blocks_terminal_but_verified_abort_does_not(database):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_spot_observations import ack
    repo, cfg, owner = await prepared(database)
    try:
        other = order(owner, operation_id='sibling', intent_id='sibling', client_order_id='wire2', quote_level=1)
        await repo.prepare(other, cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        await repo.arm('operation1', cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        # Abort remains conservatively blocked by another SEND_UNKNOWN.
        with pytest.raises(StorageError):
            await repo.apply_lifecycle(lifecycle(prepare_operation_id='sibling', intent_id='sibling', client_order_id='wire2'),
                                       cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        await repo.acknowledge(ack(owner, await database[0].fetchval('SELECT clock_timestamp()')), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        await repo.apply_lifecycle(lifecycle(prepare_operation_id='sibling', intent_id='sibling', client_order_id='wire2'),
                                   cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        await repo.confirm_terminal(await terminal(repo, cfg, owner), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        assert not (await repo.snapshot(cfg.binding)).recovery_required
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_filled_sibling_and_missing_or_changed_terminal_fills_are_not_adopted(database):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_spot_observations import filled
    repo, cfg, owner = await prepared(database, arm=True)
    try:
        source = filled(owner, await database[0].fetchval('SELECT clock_timestamp()'))
        await repo.apply_fills(source, cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        for row in (replace(source.fills[0], signed_fee=D('-.01')), replace(source.fills[0], trade_id='foreign')):
            command = await terminal(repo, cfg, owner, fills=(row,))
            with pytest.raises(StorageError):
                await repo.confirm_terminal(command, cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        with pytest.raises(StorageError):
            await repo.confirm_terminal(await terminal(repo, cfg, owner), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        assert (await repo.balance(cfg.binding)).reserved_quote_usdt == D('1.18')
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['abort', 'cancel', 'terminal'])
@pytest.mark.parametrize('committed', [False, True])
async def test_actual_backend_termination_keeps_release_and_receipt_atomic(database, monkeypatch, stage, committed):
    import asyncpg

    from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
    repo, cfg, owner = await prepared(database, arm=stage != 'abort')
    command = await terminal(repo, cfg, owner) if stage == 'terminal' else lifecycle('ABORT_PREPARED' if stage == 'abort' else 'REQUEST_CANCEL')
    original = repo._runtime._commit
    admin = await asyncpg.connect(database[1])

    async def lost(tx):
        pid = tx._connection.get_server_pid()
        if committed:
            await original(tx)
        assert await admin.fetchval('SELECT pg_terminate_backend($1)', pid)
        raise ConnectionError('synthetic lost order COMMIT reply')
    monkeypatch.setattr(repo._runtime, '_commit', lost)
    try:
        before = await repo.snapshot(cfg.binding)
        with pytest.raises(CommitUnknown):
            await getattr(repo, 'confirm_terminal' if stage == 'terminal' else 'apply_lifecycle')(command, cut=before.cut, owner=owner)
        lookup = repo.lookup_terminal if stage == 'terminal' else repo.lookup_lifecycle
        assert ((await lookup(command.operation_id, cfg.binding)) is not None) is committed
        assert (await repo.balance(cfg.binding)).reserved_quote_usdt == (0 if committed and stage != 'cancel' else D('2.2'))
    finally:
        await admin.close()
        await repo.close()
    cold = PgSpotRepository(await PgRuntime.open(cfg))
    try:
        receipt = await getattr(cold, 'confirm_terminal' if stage == 'terminal' else 'apply_lifecycle')(command,
                                                                                                        cut=(await cold.snapshot(cfg.binding)).cut, owner=owner)
        assert receipt.replayed is committed
        assert (await cold.balance(cfg.binding)).reserved_quote_usdt == (D('2.2') if stage == 'cancel' else 0)
    finally:
        await cold.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['abort', 'cancel', 'terminal'])
async def test_generation_change_at_commit_cannot_publish_release_or_cancel(database, monkeypatch, stage):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.helpers import install_generation_fault
    repo, cfg, owner = await prepared(database, arm=stage != 'abort')
    await install_generation_fault(database)
    original = repo._runtime._commit

    async def changed(tx):
        await tx._connection.execute('SELECT life_financial.test_change_generation($1)', cfg.account_id)
        await original(tx)
    monkeypatch.setattr(repo._runtime, '_commit', changed)
    try:
        before = await repo.snapshot(cfg.binding)
        command = await terminal(repo, cfg, owner) if stage == 'terminal' else lifecycle('ABORT_PREPARED' if stage == 'abort' else 'REQUEST_CANCEL')
        with pytest.raises(StorageError, match='EXPIRED_OR_CHANGED_AT_COMMIT'):
            await getattr(repo, 'confirm_terminal' if stage == 'terminal' else 'apply_lifecycle')(command, cut=before.cut, owner=owner)
        assert (await repo.snapshot(cfg.binding)).cut == before.cut
        assert not (await repo.attempt('intent1', cfg.binding)).cancel_requested
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_terminal_facts_stale_at_physical_commit_do_not_release(database, monkeypatch):
    repo, cfg, owner = await prepared(database, arm=True)
    original = repo._runtime._commit

    async def stale(tx):
        await asyncio.sleep(1.1)
        await original(tx)
    monkeypatch.setattr(repo._runtime, '_commit', stale)
    try:
        before = await repo.snapshot(cfg.binding)
        with pytest.raises(StorageError, match='FACTS_STALE_AT_COMMIT'):
            await repo.confirm_terminal(await terminal(repo, cfg, owner), cut=before.cut, owner=owner)
        assert (await repo.snapshot(cfg.binding)).cut == before.cut
        assert await repo.lookup_terminal('terminal1', cfg.binding) is None
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_filled_terminal_subsidy_cannot_be_refunded_by_an_unproven_release_event(database):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_spot_observations import filled
    repo, cfg, owner = await prepared(database, arm=True)
    try:
        source = filled(owner, await database[0].fetchval('SELECT clock_timestamp()'))
        await repo.apply_fills(source, cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        await repo.confirm_terminal(await terminal(repo, cfg, owner, fills=source.fills), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        await database[0].execute("UPDATE life_financial.budget_buckets SET committed=0,version=version+1 WHERE family='SUBSIDY'")
        await database[0].execute("INSERT INTO life_financial.budget_events SELECT account_id,'fake:'||scope,family,scope,window_key,'RELEASE',.12 FROM life_financial.budget_buckets WHERE family='SUBSIDY'")
        assert (await repo.snapshot(cfg.binding)).recovery_required
        with pytest.raises(StorageError):
            await repo.prepare(order(owner, operation_id='new', intent_id='new', client_order_id='wire2'), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['abort', 'cancel', 'terminal'])
@pytest.mark.parametrize('committed', [False, True])
async def test_actual_process_exit_cold_retry_never_double_releases(database, stage, committed):
    import json
    import sys
    from dataclasses import fields
    from datetime import datetime

    from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
    repo, cfg, owner = await prepared(database, arm=stage != 'abort')
    config = {field.name: getattr(cfg, field.name) for field in fields(cfg)}
    config.update(account_id=str(cfg.account_id), database_lineage=str(cfg.database_lineage), binding=cfg.binding.json())
    payload = json.dumps(dict(config=config, owner=owner.json(), stage=stage, committed=committed))
    child = r'''
import asyncio,json,os,sys
from uuid import UUID
from hummingbot.strategy_v2.life_liquidity.storage.contracts import OwnerToken,StateBinding
from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgConfig,PgRuntime
from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_lifecycle import lifecycle,terminal
async def run():
    data=json.load(sys.stdin); config=data['config']; config['binding']=StateBinding.from_json(config['binding'])
    for key in ('account_id','database_lineage'): config[key]=UUID(config[key])
    cfg=PgConfig(**config); runtime=await PgRuntime.open(cfg); repo=PgSpotRepository(runtime)
    owner=OwnerToken.from_json(data['owner']); original=runtime._commit
    command=await terminal(repo,cfg,owner) if data['stage']=='terminal' else lifecycle('ABORT_PREPARED' if data['stage']=='abort' else 'REQUEST_CANCEL')
    print(command.facts.observed_at.isoformat() if data['stage']=='terminal' else 'none',flush=True)
    async def die(tx):
        if data['committed']: await original(tx)
        os._exit(73)
    runtime._commit=die
    await getattr(repo,'confirm_terminal' if data['stage']=='terminal' else 'apply_lifecycle')(command,cut=(await repo.snapshot(cfg.binding)).cut,owner=owner)
asyncio.run(run())
'''
    try:
        process = await asyncio.create_subprocess_exec(sys.executable, '-c', child, stdin=asyncio.subprocess.PIPE,
                                                       stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            output, error = await asyncio.wait_for(process.communicate(payload.encode()), 15)
        except BaseException:
            process.kill()
            await process.wait()
            raise
        assert process.returncode == 73, error.decode()
    finally:
        await repo.close()
    cold = PgSpotRepository(await PgRuntime.open(cfg))
    try:
        command = await terminal(cold, cfg, owner, observed_at=datetime.fromisoformat(output.decode().strip())) if stage == 'terminal' else lifecycle('ABORT_PREPARED' if stage == 'abort' else 'REQUEST_CANCEL')
        lookup = cold.lookup_terminal if stage == 'terminal' else cold.lookup_lifecycle
        assert ((await lookup(command.operation_id, cfg.binding)) is not None) is committed
        if stage == 'terminal' and not committed:
            with pytest.raises(StorageError, match='RECOVERY_NOT_STARTED'):
                await cold.resume_terminal(command, cut=(await cold.snapshot(cfg.binding)).cut, owner=owner)
            # Definite original absence is not terminal permission. Collect a
            # fresh complete observation after cold lookup, retaining the same
            # operation/order/economic identity. Committed replay below keeps
            # its historical original facts; the 1 s freshness gate is intact.
            fresh = await terminal(cold, cfg, owner)
            assert replace(fresh.facts, observed_at=command.facts.observed_at) == command.facts
            command = fresh
        result = await getattr(cold, 'confirm_terminal' if stage == 'terminal' else 'apply_lifecycle')(command,
                                                                                                       cut=(await cold.snapshot(cfg.binding)).cut, owner=owner)
        assert result.replayed is committed
        view = await cold.snapshot(cfg.binding)
        assert view.balance.reserved_quote_usdt == (D('2.2') if stage == 'cancel' else 0)
        assert view.budget.session_subsidy_committed_usdt == (D('.1') if stage == 'cancel' else 0)
        assert await database[0].fetchval("SELECT count(*) FROM life_financial.operations WHERE operation_id=$1", command.operation_id) == 1
    finally:
        await cold.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('filled_order', [False, True])
@pytest.mark.parametrize('side', ['BUY', 'SELL'])
async def test_actual_file_and_postgres_terminal_money_budget_parity(database, tmp_path, filled_order, side):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_spot_observations import filled
    from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_terminal_contracts import (
        ACCOUNTING,
        BUDGET,
        CAPACITY,
        NOW,
        POLICY,
        RISK,
        TERMINAL_POLICY,
        create_fixture,
    )

    from hummingbot.strategy_v2.life_liquidity.storage.legacy_spot import open_legacy_spot
    repo, cfg, owner = await prepared(database, side=side, arm=True)
    files, binding, file_owner = create_fixture(tmp_path)
    native = await open_legacy_spot(
        files, binding=binding, limits=CAPACITY, risk_limits=RISK, subsidy_policy=BUDGET,
        loss_policy=BUDGET, fill_policy=POLICY, accounting_policy=ACCOUNTING, terminal_policy=TERMINAL_POLICY,
        config_version=1, account_lock_root=tmp_path / 'locks', owner=file_owner, create_binding=True, clock=lambda: NOW)
    try:
        await native.prepare(order(file_owner, side=side), cut=(await native.snapshot(binding)).cut, owner=file_owner)
        await native.arm('operation1', cut=(await native.snapshot(binding)).cut, owner=file_owner)
        for store, scope, token in ((repo, cfg.binding, owner), (native, binding, file_owner)):
            at = await database[0].fetchval('SELECT clock_timestamp()') if store is repo else NOW
            rows = ()
            if filled_order:
                command = filled(token, at, side=side)
                rows = command.fills
                await store.apply_fills(command, cut=(await store.snapshot(scope)).cut, owner=token)
            view = await store.snapshot(scope)
            facts = SpotTerminalFacts(scope, at, 'CANCELED', D('1') if filled_order else D('0'), D('2'), D('1'), rows,
                                      view.balance.total_base_life, view.balance.total_quote_usdt, D('0'), D('0'),
                                      ('trade1',) if filled_order else (), (), (), (), ('wire1',), True, True, True)
            command = TerminalSpot(
                'terminal1', 'operation1', 'intent1', 'wire1', 'LIFE-USDT', side,
                str(token.session_id), token.epoch, '123456789', facts)
            await store.confirm_terminal(command, cut=view.cut, owner=token)
        pg, file = await repo.snapshot(cfg.binding), await native.snapshot(binding)
        assert pg.balance == file.balance and pg.budget == file.budget
        assert (await repo.attempt('intent1', cfg.binding)).state == (await native.attempt('intent1', binding)).state
    finally:
        await native.close()
        await repo.close()


@pytest.mark.asyncio
async def test_terminal_replay_conflicts_and_resume_only_reads_existing_economic_core(database):
    repo, cfg, owner = await prepared(database, arm=True)
    try:
        command = await terminal(repo, cfg, owner)
        cut = (await repo.snapshot(cfg.binding)).cut
        with pytest.raises(StorageError, match='RECOVERY_NOT_STARTED'):
            await repo.resume_terminal(command, cut=cut, owner=owner)
        await repo.confirm_terminal(command, cut=cut, owner=owner)
        after = await repo.snapshot(cfg.binding)
        refreshed = replace(command, facts=replace(command.facts, observed_at=await database[0].fetchval('SELECT clock_timestamp()')))
        assert (await repo.resume_terminal(refreshed, cut=after.cut, owner=owner)).replayed
        with pytest.raises(StorageError, match='OPERATION_CONFLICT'):
            await repo.confirm_terminal(refreshed, cut=after.cut, owner=owner)
        with pytest.raises(StorageError):
            await repo.resume_terminal(replace(command, exchange_order_id='999'), cut=after.cut, owner=owner)
        with pytest.raises(StorageError):
            await repo.confirm_terminal(replace(command, operation_id='other'), cut=after.cut, owner=owner)
        assert (await repo.snapshot(cfg.binding)).cut == after.cut
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_concurrent_arm_and_abort_have_one_winner_and_no_partial_refund(database):
    repo, cfg, owner = await prepared(database)
    try:
        cut = (await repo.snapshot(cfg.binding)).cut
        results = await asyncio.gather(repo.arm('operation1', cut=cut, owner=owner),
                                       repo.apply_lifecycle(lifecycle(), cut=cut, owner=owner), return_exceptions=True)
        assert sum(isinstance(value, StorageError) for value in results) == 1
        state = (await repo.attempt('intent1', cfg.binding)).state
        view = await repo.snapshot(cfg.binding)
        assert view.balance.reserved_quote_usdt == (0 if state == 'ABORTED_BEFORE_SEND' else D('2.2'))
        assert view.budget.session_subsidy_committed_usdt == (0 if state == 'ABORTED_BEFORE_SEND' else D('.1'))
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('bad', ['kind', 'action', 'intent_id', 'resume'])
async def test_raw_sql_nulls_cannot_select_a_release_branch(database, bad):
    import json

    import asyncpg
    repo, cfg, owner = await prepared(database)
    connection = await asyncpg.connect(cfg.dsn)
    try:
        before = await repo.snapshot(cfg.binding)
        command, kind, resume = lifecycle().json(), 'SPOT_LIFECYCLE', False
        if bad == 'kind':
            kind = None
        elif bad == 'resume':
            resume = None
        else:
            command[bad] = None
        with pytest.raises(asyncpg.RaiseError, match='ORDER_COMMAND_INVALID'):
            await connection.fetchval('SELECT life_financial.spot_order_command($1,$2::jsonb,$3::jsonb,$4,$5,$6,$7,$8::jsonb,$9)',
                                      cfg.account_id, json.dumps(cfg.binding.json()), json.dumps(owner.json()), before.cut.checkpoint.revision,
                                      before.cut.checkpoint.state_digest, 'f' * 64, kind, json.dumps(command), resume)
        assert (await repo.snapshot(cfg.binding)).cut == before.cut
    finally:
        await connection.close()
        await repo.close()


@pytest.mark.asyncio
async def test_cancellation_progress_corruption_blocks_financial_mutation(database):
    repo, cfg, owner = await prepared(database, arm=True)
    try:
        await repo.apply_lifecycle(lifecycle('REQUEST_CANCEL'), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        at = await database[0].fetchval('SELECT clock_timestamp()')
        await database[0].execute('UPDATE life_financial.spot_cancel_progress SET cancel_attempts=1,last_cancel_attempt_at=$1,last_recorded_at=$1,version=version+1', at)
        view = await repo.snapshot(cfg.binding)
        assert view.recovery_required
        with pytest.raises(StorageError, match='PROJECTION_UNVERIFIED'):
            await repo.confirm_terminal(await terminal(repo, cfg, owner), cut=view.cut, owner=owner)
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['abort', 'terminal'])
async def test_missing_order_policy_does_not_create_implicit_live_limits(database, stage):
    repo, cfg, owner = await prepared(database, arm=stage == 'terminal', import_order_policy=False)
    try:
        before = await repo.snapshot(cfg.binding)
        command = await terminal(repo, cfg, owner) if stage == 'terminal' else lifecycle()
        with pytest.raises(StorageError, match='ORDER_POLICY_REQUIRED'):
            await getattr(repo, 'confirm_terminal' if stage == 'terminal' else 'apply_lifecycle')(command, cut=before.cut, owner=owner)
        assert (await repo.snapshot(cfg.binding)).cut == before.cut
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.spot_order_policies') == 0
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_terminal_requires_imported_opening_capital_anchor(database):
    repo, cfg, owner = await spot(database)
    try:
        await repo.prepare(order(owner), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        await repo.arm('operation1', cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        before = await repo.snapshot(cfg.binding)
        with pytest.raises(StorageError, match='CAPITAL_POLICY_REQUIRED'):
            await repo.confirm_terminal(await terminal(repo, cfg, owner), cut=before.cut, owner=owner)
        assert (await repo.snapshot(cfg.binding)).cut == before.cut
    finally:
        await repo.close()
