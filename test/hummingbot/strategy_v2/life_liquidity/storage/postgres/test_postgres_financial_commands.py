"""Actual PostgreSQL spot preparation contracts; no exchange transport or grant."""
import asyncio
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.helpers import backend

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import AtomicScope, StorageError
from hummingbot.strategy_v2.life_liquidity.storage.financial import AttemptState, PrepareSpot


def order(owner, **changes):
    return replace(PrepareSpot('operation1', 'intent1', 'wire1', 'LIFE-USDT', 'BUY', D('2'), D('1'),
                               D('1'), D('.1'), str(owner.session_id), owner.epoch, 1, 0, D('.2'), D('.1')), **changes)


async def spot(database, limits=(D('0'), D('100'), D('1000'), D('100')), *, session_clock_offset=None,
               session_duration=timedelta(seconds=60), on_expiry='pause_quotes', max_order_fact_age_ms=1000,
               cancel_retry_interval_ms=100, import_order_policy=True, config_hash=None, configuration_body=None,
               original_session_id=None, opening_base_life=None, safety_size=1):
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
    runtime, cfg, owner = await backend(database, session_duration=session_duration, config_hash=config_hash, configuration_body=configuration_body,
                                        original_session_id=original_session_id, opening_base_life=opening_base_life, safety_size=safety_size)
    # Approved policy import is administrator-only; explicitly synthetic test values.
    await database[0].execute('''INSERT INTO life_financial.spot_policies VALUES(
        $1,$2,$3,$4,$5,$6,'synthetic',10,5,2,10,5,2,8)''', cfg.account_id, cfg.binding.policy_hash, *limits)
    watermark = owner.valid_from if session_clock_offset is None else await database[0].fetchval('SELECT clock_timestamp()+$1::interval', session_clock_offset)
    await database[0].execute("INSERT INTO life_financial.spot_session_policies VALUES($1,$2,$3,$4,1000,$5,'ACTIVE')",
                              cfg.account_id, owner.session_id, cfg.binding.policy_hash, on_expiry, watermark)
    await database[0].execute("INSERT INTO life_financial.spot_session_progress VALUES($1,$2,'ACTIVE','SESSION_ACTIVE',$3,false,0)",
                              cfg.account_id, owner.session_id, watermark)
    if import_order_policy:
        await database[0].execute('INSERT INTO life_financial.spot_order_policies VALUES($1,$2,$3,$4)',
                                  cfg.account_id, cfg.binding.policy_hash, max_order_fact_age_ms, cancel_retry_interval_ms)
    return PgSpotRepository(runtime), cfg, owner


@pytest.mark.asyncio
async def test_spot_preparation_atomic_holds_fees_budget_and_identity(database):
    repo, cfg, owner = await spot(database)
    try:
        before = await repo.snapshot(cfg.binding)
        result = await repo.prepare(order(owner), cut=before.cut, owner=owner)
        assert result.atomic_scope is AtomicScope.ACCOUNT and result.state == 'PREPARED' and not result.replayed
        view = await repo.snapshot(cfg.binding)
        assert view.balance.reserved_quote_usdt == D('2.2') and view.balance.reserved_base_life == D('.1')
        assert view.balance.fee_reserved_quote_usdt == D('.2') and view.balance.fee_reserved_base_life == D('.1')
        assert view.budget.session_subsidy_committed_usdt == D('.1')
        assert view.budget.campaign_subsidy_committed_usdt == D('.1')
        assert (await repo.attempt('intent1', cfg.binding)).state is AttemptState.PREPARED
        assert (await repo.reservation('intent1', cfg.binding)).remaining_base_life == 2
        assert (await repo.prepare(order(owner), cut=before.cut, owner=owner)).replayed
        assert (await repo.balance(cfg.binding)).reserved_quote_usdt == D('2.2')
        with pytest.raises(StorageError):
            await repo.prepare(order(owner, quantity_base=D('3')), cut=before.cut, owner=owner)
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('changes', [
    dict(quantity_base=D('11')), dict(subsidy_quote_usdt=D('3')), dict(fee_buffer_quote_usdt=D('9')),
    dict(budget_scope='EXIT', side='SELL'), dict(risk_epoch=1), dict(quote_level=8),
])
async def test_denied_preparation_rolls_back_every_table(database, changes):
    repo, cfg, owner = await spot(database)
    try:
        view = await repo.snapshot(cfg.binding)
        with pytest.raises(StorageError):
            await repo.prepare(order(owner, **changes), cut=view.cut, owner=owner)
        c = database[0]
        for table in ('operations', 'send_attempts', 'reservations', 'spot_preparations', 'budget_events', 'budget_buckets'):
            assert await c.fetchval('SELECT count(*) FROM life_financial.' + table) == 0
        assert (await repo.balance(cfg.binding)).reserved_quote_usdt == 0
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_concurrent_preparations_conflict_without_overallocation(database):
    repo, cfg, owner = await spot(database)
    try:
        cut = (await repo.snapshot(cfg.binding)).cut
        results = await asyncio.gather(repo.prepare(order(owner), cut=cut, owner=owner),
                                       repo.prepare(order(owner, operation_id='other', intent_id='other', client_order_id='wire2', quote_level=1),
                                                    cut=cut, owner=owner), return_exceptions=True)
        assert sum(isinstance(row, StorageError) for row in results) == 1
        assert (await repo.balance(cfg.binding)).reserved_quote_usdt == D('2.2')
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_arm_durably_unknown_never_replays_send_or_releases_holds(database):
    repo, cfg, owner = await spot(database)
    try:
        await repo.prepare(order(owner), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        receipt = await repo.arm('operation1', cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        assert receipt.state == 'SEND_UNKNOWN' and receipt.atomic_scope is AtomicScope.ACCOUNT
        assert (await repo.lookup_prepare('operation1', cfg.binding)).state == 'SEND_UNKNOWN'
        assert (await repo.attempt('intent1', cfg.binding)).state is AttemptState.SEND_UNKNOWN
        assert (await repo.snapshot(cfg.binding)).recovery_required
        with pytest.raises(StorageError):
            await repo.arm('operation1', cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        with pytest.raises(StorageError):
            await repo.prepare(order(owner, operation_id='other', intent_id='other', client_order_id='wire2', quote_level=1),
                               cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        assert (await repo.balance(cfg.binding)).reserved_quote_usdt == D('2.2')
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_prepared_replay_after_arm_is_not_a_fresh_preparation(database):
    repo, cfg, owner = await spot(database)
    try:
        cut = (await repo.snapshot(cfg.binding)).cut
        await repo.prepare(order(owner), cut=cut, owner=owner)
        await repo.arm('operation1', cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        with pytest.raises(StorageError):
            await repo.prepare(order(owner), cut=cut, owner=owner)
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_understated_subsidy_hold_requires_recovery_and_cannot_arm(database):
    repo, cfg, owner = await spot(database)
    try:
        await repo.prepare(order(owner), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        await database[0].execute("UPDATE life_financial.budget_buckets SET committed=0,version=version+1 WHERE scope='DAY'")
        view = await repo.snapshot(cfg.binding)
        assert view.recovery_required
        with pytest.raises(StorageError):
            await repo.arm('operation1', cut=view.cut, owner=owner)
        assert (await repo.balance(cfg.binding)).reserved_quote_usdt == D('2.2')
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_foreign_owner_forged_cut_and_occupied_slot_deny_without_changes(database):
    from uuid import uuid4
    repo, cfg, owner = await spot(database)
    try:
        view = await repo.snapshot(cfg.binding)
        forged = replace(view.cut, checkpoint=replace(view.cut.checkpoint, state_digest='f' * 64), components=(('postgres_account', 'f' * 64),))
        for cut, token in ((forged, owner), (view.cut, replace(owner, generation=uuid4()))):
            with pytest.raises(StorageError):
                await repo.prepare(order(owner), cut=cut, owner=token)
        await repo.prepare(order(owner), cut=view.cut, owner=owner)
        with pytest.raises(StorageError):
            await repo.prepare(order(owner, operation_id='other', intent_id='other', client_order_id='wire2'),
                               cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        assert (await repo.balance(cfg.binding)).reserved_quote_usdt == D('2.2')
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_expiry_at_spot_commit_rolls_back_funds_budgets_intent_and_receipt(database, monkeypatch):
    repo, cfg, owner = await spot(database)
    expires = await database[0].fetchval("SELECT clock_timestamp()+interval '1 second'")
    owner = replace(owner, expires_at=expires)
    await database[0].execute('UPDATE life_financial.accounts SET owner_until=$2 WHERE account_id=$1', cfg.account_id, expires)
    original = repo._runtime._commit

    async def expire(tx):
        await asyncio.sleep(1.1)
        await original(tx)
    monkeypatch.setattr(repo._runtime, '_commit', expire)
    try:
        with pytest.raises(StorageError, match='EXPIRED_OR_CHANGED_AT_COMMIT'):
            await repo.prepare(order(owner), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        assert await repo.lookup_prepare('operation1', cfg.binding) is None
        assert (await repo.balance(cfg.binding)).reserved_quote_usdt == 0
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.budget_events') == 0
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['prepare', 'arm'])
@pytest.mark.parametrize('committed', [False, True])
async def test_spot_unknown_commit_primary_readback_retains_original_identity(database, monkeypatch, stage, committed):
    import asyncpg

    from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
    repo, cfg, owner = await spot(database)
    admin = await asyncpg.connect(database[1])
    original = repo._runtime._commit
    if stage == 'arm':
        await repo.prepare(order(owner), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)

    async def lost(tx):
        pid = tx._connection.get_server_pid()
        if committed:
            await original(tx)
        await admin.fetchval('SELECT pg_terminate_backend($1)', pid)
        raise ConnectionError('synthetic lost SPOT commit reply')
    monkeypatch.setattr(repo._runtime, '_commit', lost)
    try:
        with pytest.raises(CommitUnknown):
            cut = (await repo.snapshot(cfg.binding)).cut
            if stage == 'prepare':
                await repo.prepare(order(owner), cut=cut, owner=owner)
            else:
                await repo.arm('operation1', cut=cut, owner=owner)
        assert not repo._runtime.healthy
        assert ((await repo.lookup_prepare('operation1', cfg.binding)) is not None) is (committed or stage == 'arm')
    finally:
        await admin.close()
        await repo.close()
    cold = PgSpotRepository(await PgRuntime.open(cfg))
    try:
        result = await cold.lookup_prepare('operation1', cfg.binding)
        assert (await cold.balance(cfg.binding)).reserved_quote_usdt == (D('2.2') if committed or stage == 'arm' else 0)
        if result is not None:
            assert result.state == ('SEND_UNKNOWN' if stage == 'arm' and committed else 'PREPARED')
        if stage == 'arm' and committed:
            with pytest.raises(StorageError):
                await cold.arm('operation1', cut=(await cold.snapshot(cfg.binding)).cut, owner=owner)
    finally:
        await cold.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('side', ['BUY', 'SELL'])
async def test_file_and_postgres_preparation_have_equal_units_fees_budgets_and_states(database, tmp_path, side):
    from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_financial_contracts import (
        BUDGET,
        CAPACITY,
        NOW,
        RISK,
        create_fixture,
        open_legacy_spot,
    )
    repo, cfg, owner = await spot(database)
    files, file_binding, file_owner = create_fixture(tmp_path)
    native = await open_legacy_spot(files, binding=file_binding, limits=CAPACITY, risk_limits=RISK,
                                    subsidy_policy=BUDGET, loss_policy=BUDGET, config_version=1,
                                    account_lock_root=tmp_path / 'locks', owner=file_owner, create_binding=True, clock=lambda: NOW)
    try:
        for store, binding, token in ((repo, cfg.binding, owner), (native, file_binding, file_owner)):
            await store.prepare(order(token, side=side), cut=(await store.snapshot(binding)).cut, owner=token)
        pg, legacy = await repo.snapshot(cfg.binding), await native.snapshot(file_binding)
        assert pg.balance == legacy.balance and pg.budget == legacy.budget
        assert pg.session.epoch == legacy.session.epoch and pg.session.config_version == legacy.session.config_version
        for store, binding, token in ((repo, cfg.binding, owner), (native, file_binding, file_owner)):
            await store.arm('operation1', cut=(await store.snapshot(binding)).cut, owner=token)
        assert (await repo.attempt('intent1', cfg.binding)).state == (await native.attempt('intent1', file_binding)).state
        assert (await repo.snapshot(cfg.binding)).recovery_required and (await native.snapshot(file_binding)).recovery_required
    finally:
        await repo.close()
        await native.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['prepare', 'arm'])
@pytest.mark.parametrize('committed', [False, True])
async def test_actual_process_exit_at_spot_commit_retains_atomic_state(database, stage, committed):
    import json
    import sys
    from dataclasses import fields

    from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
    repo, cfg, owner = await spot(database)
    try:
        if stage == 'arm':
            await repo.prepare(order(owner), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        cut = (await repo.snapshot(cfg.binding)).cut
        config = {f.name: getattr(cfg, f.name) for f in fields(cfg)}
        config.update(account_id=str(cfg.account_id), database_lineage=str(cfg.database_lineage), binding=cfg.binding.json())
        payload = json.dumps(dict(config=config, owner=owner.json(), cut=cut.json(), command=order(owner).json(), stage=stage, committed=committed))
        child = r'''
import asyncio, json, os, sys
from uuid import UUID
from hummingbot.strategy_v2.life_liquidity.storage.contracts import OwnerToken, StateBinding
from hummingbot.strategy_v2.life_liquidity.storage.financial import FinancialCut, PrepareSpot
from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgConfig, PgRuntime
from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
async def run():
    data=json.load(sys.stdin)
    config=data['config']
    config['binding']=StateBinding.from_json(config['binding'])
    for key in ('account_id','database_lineage'): config[key]=UUID(config[key])
    runtime=await PgRuntime.open(PgConfig(**config))
    repo=PgSpotRepository(runtime)
    original=runtime._commit
    async def die(tx):
        if data['committed']: await original(tx)
        os._exit(73)
    runtime._commit=die
    owner=OwnerToken.from_json(data['owner'])
    cut=FinancialCut.from_json(data['cut'])
    if data['stage']=='prepare': await repo.prepare(PrepareSpot.from_json(data['command']),cut=cut,owner=owner)
    else: await repo.arm('operation1',cut=cut,owner=owner)
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
        result = await cold.lookup_prepare('operation1', cfg.binding)
        assert (result is not None) is (committed or stage == 'arm')
        assert (await cold.balance(cfg.binding)).reserved_quote_usdt == (D('2.2') if committed or stage == 'arm' else 0)
        if result is not None:
            assert result.state == ('SEND_UNKNOWN' if committed and stage == 'arm' else 'PREPARED')
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.send_attempts') == (1 if result else 0)
    finally:
        await cold.close()


@pytest.mark.asyncio
async def test_snapshot_arithmetic_preserves_all_qualified_numeric_digits(database):
    repo, cfg, owner = await spot(database)
    try:
        total = D('12345678901234567890.123456789012345678')
        await database[0].execute("UPDATE life_financial.balances SET total=$1,version=version+1 WHERE asset='USDT'", total)
        await repo.prepare(order(owner), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        view = await repo.snapshot(cfg.binding)
        assert view.balance.available_quote_usdt == D('12345678901234567887.923456789012345678')
        assert view.balance.total_quote_usdt == total
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('limits,side', [
    ((D('0'), D('6'), D('1000'), D('100')), 'BUY'),
    ((D('0'), D('100'), D('6'), D('100')), 'BUY'),
    ((D('0'), D('100'), D('1000'), D('6')), 'BUY'),
    ((D('4'), D('100'), D('1000'), D('100')), 'SELL'),
])
async def test_persisted_risk_limits_prevent_inventory_gross_and_net_breaches(database, limits, side):
    repo, cfg, owner = await spot(database, limits=limits)
    try:
        with pytest.raises(StorageError):
            await repo.prepare(order(owner, side=side), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        assert await repo.lookup_prepare('operation1', cfg.binding) is None
        assert (await repo.balance(cfg.binding)).reserved_quote_usdt == 0
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_unported_allocation_is_retained_and_prevents_spot_admission(database):
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import AllocateBalance
    repo, cfg, owner = await spot(database)
    try:
        await repo._runtime.allocate(AllocateBalance('unported', 0, 'USDT', D('1')), owner)
        view = await repo.snapshot(cfg.binding)
        assert view.recovery_required
        with pytest.raises(StorageError):
            await repo.prepare(order(owner), cut=view.cut, owner=owner)
        assert (await repo.balance(cfg.binding)).reserved_quote_usdt == 1
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_reservation_reader_never_reports_original_quantity_when_principal_is_missing(database):
    repo, cfg, owner = await spot(database)
    try:
        await repo.prepare(order(owner), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        await database[0].execute("UPDATE life_financial.reservations SET remaining=1,version=version+1 WHERE reservation_id='operation1:principal'")
        assert (await repo.snapshot(cfg.binding)).recovery_required
        with pytest.raises(StorageError):
            await repo.reservation('intent1', cfg.binding)
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_expired_prepare_replay_requires_primary_lookup_instead_of_admission(database):
    repo, cfg, owner = await spot(database)
    try:
        expires = await database[0].fetchval("SELECT clock_timestamp()+interval '1 second'")
        owner = replace(owner, expires_at=expires)
        await database[0].execute('UPDATE life_financial.accounts SET owner_until=$2 WHERE account_id=$1', cfg.account_id, expires)
        cut = (await repo.snapshot(cfg.binding)).cut
        await repo.prepare(order(owner), cut=cut, owner=owner)
        await asyncio.sleep(1.1)
        with pytest.raises(StorageError):
            await repo.prepare(order(owner), cut=cut, owner=owner)
        assert (await repo.lookup_prepare('operation1', cfg.binding)).state == 'PREPARED'
        assert (await repo.balance(cfg.binding)).reserved_quote_usdt == D('2.2')
    finally:
        await repo.close()
