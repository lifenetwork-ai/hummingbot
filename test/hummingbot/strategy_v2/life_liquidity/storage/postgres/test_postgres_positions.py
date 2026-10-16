"""Original held-position snapshots, no exchange position or accounting grant."""
from dataclasses import replace
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_events import setup as event_setup

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
from hummingbot.strategy_v2.life_liquidity.storage.postgres.positions import PgPositionRepository
from hummingbot.strategy_v2.life_liquidity.storage.recorder_positions import HeldPosition, PositionReport


async def setup(database):
    t, _, _, at = await event_setup(database)
    repo = PgPositionRepository(t.repo, controller_id='controller1', max_rows=64, max_read_bytes=1048576)
    return t, repo, at


def held(**changes):
    value = HeldPosition('okx', 'LIFE-USDT', None, ('wire1',), D('1.0000001'), D('1.0000002'), D('-0.0000009'),
                         D('2.0000002'), D('-0.0000009'), D('1.0000001'), D('1.0000003'), D('0'), D('0'), D('1.2'))
    return replace(value, **changes)


def command(t, at, *, seq=0, positions=None, operation=None):
    return PositionReport(operation or 'positions' + str(seq), t.cfg.binding, 'controller1', seq, at,
                          (held(),) if positions is None else positions)


@pytest.mark.asyncio
async def test_exact_snapshot_atomic_delivery_cold_restore_and_historical_replay(database):
    t, repo, at = await setup(database)
    try:
        c = command(t, at)
        original = await t.repo.snapshot(t.cfg.binding)
        await repo.stage(c, cut=original.cut, owner=t.owner)
        assert (await repo.pending(t.cfg.binding))[0].command == c
        assert (await t.repo.snapshot(t.cfg.binding)).recovery_required
        assert await database[0].fetchval('SELECT count(*) FROM life_recorder."Position"') == 0
        await repo.apply(c.operation_id, cut=(await t.repo.snapshot(t.cfg.binding)).cut, owner=t.owner)
        cold = PgPositionRepository(t.repo, controller_id='controller1', max_rows=64, max_read_bytes=1048576)
        state = await cold.positions(t.cfg.binding)
        assert state.sequence == 1 and state.positions == c.positions
        assert state.cut == (await t.repo.snapshot(t.cfg.binding)).cut.checkpoint
        row = await database[0].fetchrow('SELECT * FROM life_recorder."Position"')
        expected = c.sql_rows()[0]
        for key in ('volume_traded_quote', 'amount', 'breakeven_price', 'unrealized_pnl_quote', 'realized_pnl_quote', 'cum_fees_quote'):
            assert row[key] == int(getattr(expected, key) * 1000000)
        assert state.positions[0].restore().cum_fees_quote == D('-0.0000009')
        newer = command(t, at + 1, seq=1, positions=(held(mark_price=D('1.3')),))
        await cold.project(newer, owner=t.owner)
        assert (await cold.stage(c, cut=original.cut, owner=t.owner)).replayed
        assert (await cold.apply(c.operation_id, cut=original.cut, owner=t.owner)).replayed
        assert (await cold.positions(t.cfg.binding)).positions == newer.positions
        assert await t.repo.balance(t.cfg.binding) == original.balance
        assert len(t.session.sent) == 1
    finally:
        await t.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['wire', 'sequence', 'time', 'regression', 'native'])
async def test_unqualified_or_regressing_position_snapshot_denied(database, fault):
    t, repo, at = await setup(database)
    try:
        await repo.project(command(t, at), owner=t.owner)
        c = command(t, at + 1, seq=1)
        expected = 'PG_POSITION_HISTORY_REGRESSION'
        if fault == 'wire':
            c = replace(c, positions=(held(order_ids=('foreign',)),))
            expected = 'PG_POSITION_WIRE_INVALID'
        elif fault == 'sequence':
            c = replace(c, operation_id='stale-sequence', expected_sequence=0)
            expected = 'PG_POSITION_SEQUENCE_INVALID'
        elif fault == 'time':
            c = replace(c, timestamp_ms=at - 1)
        elif fault == 'regression':
            c = replace(c, positions=())
        elif fault == 'native':
            await database[0].execute('UPDATE life_recorder."Position" SET amount=99')
            expected = 'PG_POSITION_CUT_OR_DELIVERY_INVALID'
        with pytest.raises(StorageError, match=expected):
            await repo.project(c, owner=t.owner)
        assert len(t.session.sent) == 1
    finally:
        await t.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('phase', ['stage', 'apply'])
@pytest.mark.parametrize('committed', [False, True])
@pytest.mark.parametrize('fault', ['backend', 'process'])
async def test_real_failure_boundaries_repair_original_effects_without_resend(database, monkeypatch, phase, committed, fault):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.report_process import exit_report

    import asyncpg

    from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
    t, repo, at = await setup(database)
    captured = command(t, at)
    if phase == 'apply':
        await repo.stage(captured, cut=(await t.repo.snapshot(t.cfg.binding)).cut, owner=t.owner)
    before = await t.repo.snapshot(t.cfg.binding)
    admin = None
    original = t.repo._runtime._commit
    try:
        if fault == 'process':
            await exit_report(t.cfg, captured, t.owner, before.cut, phase, committed)
        else:
            admin = await asyncpg.connect(database[1])

            async def lost(tx):
                pid = tx._connection.get_server_pid()
                if committed:
                    await original(tx)
                assert await admin.fetchval('SELECT pg_terminate_backend($1)', pid)
                raise ConnectionError('synthetic lost event COMMIT reply')
            monkeypatch.setattr(t.repo._runtime, '_commit', lost)
            with pytest.raises(CommitUnknown):
                if phase == 'stage':
                    await repo.stage(captured, cut=before.cut, owner=t.owner)
                else:
                    await repo.apply(captured.operation_id, cut=before.cut, owner=t.owner)
    finally:
        if admin is not None:
            await admin.close()
        await t.close()
    financial = PgSpotRepository(await PgRuntime.open(t.cfg))
    cold = PgPositionRepository(financial, controller_id='controller1', max_rows=64, max_read_bytes=1048576)
    try:
        assert len(await cold.pending(t.cfg.binding)) == int(committed if phase == 'stage' else not committed)
        for table in ('Position',):
            assert await database[0].fetchval(f'SELECT count(*) FROM life_recorder."{table}"') == int(phase == 'apply' and committed)
        await cold.project(captured, owner=t.owner)
        assert await cold.pending(t.cfg.binding) == ()
        for table in ('Position',):
            assert await database[0].fetchval(f'SELECT count(*) FROM life_recorder."{table}"') == 1
        assert await financial.balance(t.cfg.binding) == before.balance and len(t.session.sent) == 1
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('phase', ['stage', 'apply'])
async def test_generation_change_at_position_commit_rolls_back_native_rows(database, monkeypatch, phase):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.helpers import install_generation_fault
    t, repo, at = await setup(database)
    await install_generation_fault(database)
    try:
        captured = command(t, at)
        if phase == 'apply':
            await repo.stage(captured, cut=(await t.repo.snapshot(t.cfg.binding)).cut, owner=t.owner)
        before = await t.repo.snapshot(t.cfg.binding)
        original = t.repo._runtime._commit

        async def changed(tx):
            await tx._connection.execute('SELECT life_financial.test_change_generation($1)', t.cfg.account_id)
            await original(tx)
        monkeypatch.setattr(t.repo._runtime, '_commit', changed)
        with pytest.raises(StorageError, match='OWNER_EXPIRED_OR_CHANGED_AT_COMMIT'):
            if phase == 'stage':
                await repo.stage(captured, cut=before.cut, owner=t.owner)
            else:
                await repo.apply(captured.operation_id, cut=before.cut, owner=t.owner)
        assert (await t.repo.snapshot(t.cfg.binding)).cut == before.cut
        assert await database[0].fetchval('SELECT count(*) FROM life_recorder."Position"') == 0
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.recorder_projection_heads') == 0
    finally:
        await t.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('target', ['row', 'extra', 'effects', 'heads'])
async def test_position_corruption_denies_history_and_risk_without_repair_inference(database, target):
    t, repo, at = await setup(database)
    try:
        await repo.project(command(t, at), owner=t.owner)
        if target == 'row':
            await database[0].execute('UPDATE life_recorder."Position" SET amount=99')
        elif target == 'extra':
            await database[0].execute('''INSERT INTO life_recorder."Position"
                SELECT 'foreign',controller_id,connector_name,side,trading_pair,timestamp,volume_traded_quote,
                    amount,breakeven_price,unrealized_pnl_quote,realized_pnl_quote,cum_fees_quote FROM life_recorder."Position"''')
        else:
            table = 'recorder_projection_' + target
            await database[0].execute('ALTER TABLE life_financial.' + table + ' DISABLE TRIGGER USER')
            if target == 'effects':
                await database[0].execute("UPDATE life_financial.recorder_projection_effects SET after_row=jsonb_set(after_row,'{amount}','99')")
            else:
                await database[0].execute('UPDATE life_financial.recorder_projection_heads SET version=99')
            await database[0].execute('ALTER TABLE life_financial.' + table + ' ENABLE TRIGGER USER')
        with pytest.raises(StorageError, match='PG_RECORDER_UNVERIFIED'):
            await repo.positions(t.cfg.binding)
        assert (await t.repo.snapshot(t.cfg.binding)).recovery_required
        assert len(t.session.sent) == 1
    finally:
        await t.close()


@pytest.mark.asyncio
async def test_intermediate_product_retains_micro_unit_truncation_and_exact_state(database):
    t, repo, at = await setup(database)
    try:
        pos = held(avg_entry_price=D('0.999999000000000002'), mark_price=D('1.000000000000000001'), net_amount_base=D('1'))
        captured = command(t, at, positions=(pos,))
        await repo.project(captured, owner=t.owner)
        assert await database[0].fetchval('SELECT unrealized_pnl_quote FROM life_recorder."Position"') == 0
        assert captured.sql_rows()[0].unrealized_pnl_quote == 0
        assert (await repo.positions(t.cfg.binding)).positions == captured.positions
    finally:
        await t.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['missing', 'null', 'side', 'nan', 'negative', 'duplicate_wire', 'controller', 'future'])
async def test_raw_sql_position_shape_source_and_time_cannot_bypass_dto(database, fault):
    import json

    import asyncpg
    t, repo, at = await setup(database)
    c = await asyncpg.connect(t.cfg.dsn)
    try:
        value = command(t, at).json()
        pos = value['positions'][0]
        if fault == 'missing':
            pos.pop('mark_price')
        elif fault == 'null':
            pos['connector_name'] = None
        elif fault == 'side':
            pos['side'] = 'BOTH'
        elif fault == 'nan':
            pos['net_amount_base'] = 'NaN'
        elif fault == 'negative':
            pos['avg_entry_price'] = '-1'
        elif fault == 'duplicate_wire':
            pos['order_ids'] = ['wire1', 'wire1']
        elif fault == 'controller':
            value['controller_id'] = 'foreign'
        else:
            value['timestamp_ms'] += 600000
        before = await t.repo.snapshot(t.cfg.binding)
        with pytest.raises(asyncpg.RaiseError, match='POSITION_'):
            await c.fetchval('SELECT life_financial.recorder_position_stage($1,$2::jsonb,$3::jsonb,$4,$5,$6,$7::jsonb)',
                             t.cfg.account_id, json.dumps(t.cfg.binding.json()), json.dumps(t.owner.json()), before.cut.checkpoint.revision,
                             before.cut.checkpoint.state_digest, 'f' * 64, json.dumps(value))
        assert (await t.repo.snapshot(t.cfg.binding)).cut == before.cut
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.recorder_projection_effects') == 0
    finally:
        await c.close()
        await t.close()


@pytest.mark.asyncio
async def test_empty_initial_snapshot_then_owned_position_keeps_sequenced_history(database):
    t, repo, at = await setup(database)
    try:
        await repo.project(command(t, at, positions=()), owner=t.owner)
        assert (await repo.positions(t.cfg.binding)).sequence == 1
        assert await database[0].fetchval('SELECT count(*) FROM life_recorder."Position"') == 0
        await repo.project(command(t, at + 1, seq=1), owner=t.owner)
        assert (await repo.positions(t.cfg.binding)).sequence == 2
        assert await database[0].fetchval('SELECT count(*) FROM life_recorder."Position"') == 1
    finally:
        await t.close()


@pytest.mark.asyncio
async def test_multiple_native_position_writes_and_heads_roll_back_together(database):
    t, repo, at = await setup(database)
    try:
        zero = held(side='BUY', order_ids=(), net_amount_base=D('0'), volume_traded_quote=D('0'), buy_amount_base=D('0'),
                    buy_amount_quote=D('0'), realized_pnl_quote=D('0'), cum_fees_quote=D('0'))
        captured = command(t, at, positions=(held(), zero))
        await repo.stage(captured, cut=(await t.repo.snapshot(t.cfg.binding)).cut, owner=t.owner)
        before = await t.repo.snapshot(t.cfg.binding)
        await database[0].execute('''CREATE FUNCTION life_recorder.test_failure() RETURNS trigger
            LANGUAGE plpgsql AS $$ BEGIN
                IF (SELECT count(*) FROM life_recorder."Position")>0 THEN
                    RAISE EXCEPTION 'LIFE_POSITION_SYNTHETIC_FAILURE' USING ERRCODE='P0001'; END IF;
                RETURN NEW; END; $$;
            CREATE TRIGGER synthetic_failure BEFORE INSERT ON life_recorder."Position"
                FOR EACH ROW EXECUTE FUNCTION life_recorder.test_failure()''')
        with pytest.raises(StorageError, match='PG_POSITION_SYNTHETIC_FAILURE'):
            await repo.apply(captured.operation_id, cut=before.cut, owner=t.owner)
        assert (await t.repo.snapshot(t.cfg.binding)).cut == before.cut
        assert await database[0].fetchval('SELECT count(*) FROM life_recorder."Position"') == 0
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.recorder_projection_heads') == 0
        await database[0].execute('DROP TRIGGER synthetic_failure ON life_recorder."Position"')
        await repo.apply(captured.operation_id, cut=before.cut, owner=t.owner)
        assert await database[0].fetchval('SELECT count(*) FROM life_recorder."Position"') == 2
    finally:
        await t.close()


@pytest.mark.asyncio
async def test_event_and_position_streams_keep_independent_heads_and_current_cut(database):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_events import event

    from hummingbot.strategy_v2.life_liquidity.storage.postgres.events import PgEventRepository
    from hummingbot.strategy_v2.life_liquidity.storage.recorder_events import EventSource
    t, repo, at = await setup(database)
    source = EventSource('controller1', 'life.yml', 'strategy_v2', 'okx')
    events = PgEventRepository(t.repo, source=source, max_rows=64, max_read_bytes=1048576)
    try:
        before = await t.repo.balance(t.cfg.binding)
        await repo.project(command(t, at), owner=t.owner)
        await events.project(event(t.cfg.binding, source, at), owner=t.owner)
        newer = command(t, at + 1, seq=1, positions=(held(mark_price=D('1.4')),))
        await repo.project(newer, owner=t.owner)
        await events.project(event(t.cfg.binding, source, at + 2, 'OrderCancelled'), owner=t.owner)
        state = await repo.positions(t.cfg.binding)
        assert state.positions == newer.positions and state.sequence == 2
        assert state.cut == (await t.repo.snapshot(t.cfg.binding)).cut.checkpoint
        assert (await events.connector_history(source, t.cfg.binding)).exchange_ids == (('123', 'wire1'),)
        assert await t.repo.balance(t.cfg.binding) == before and len(t.session.sent) == 1
    finally:
        await t.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('limit', ['rows', 'bytes'])
async def test_position_audit_does_not_return_a_truncated_snapshot_history(database, limit):
    t, repo, at = await setup(database)
    try:
        await repo.project(command(t, at), owner=t.owner)
        await repo.project(command(t, at + 1, seq=1), owner=t.owner)
        bounded = PgPositionRepository(
            t.repo, controller_id='controller1', max_rows=1 if limit == 'rows' else 64,
            max_read_bytes=8 if limit == 'bytes' else 1048576)
        with pytest.raises(StorageError, match='PG_RECORDER_(BYTE_)?CAPACITY'):
            await bounded.positions(t.cfg.binding)
    finally:
        await t.close()


@pytest.mark.asyncio
async def test_position_reporting_role_reads_scoped_history_but_cannot_publish_or_write(database):
    import asyncpg

    from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
    t, repo, at = await setup(database)
    reader_financial = None
    try:
        captured = command(t, at)
        await repo.project(captured, owner=t.owner)
        cfg = replace(t.cfg, dsn=t.cfg.dsn.replace('life_pg2_runtime@', 'life_pg2_reporter@'), principal='life_pg2_reporter', write=False)
        reader_financial = PgSpotRepository(await PgRuntime.open(cfg))
        reader = PgPositionRepository(reader_financial, controller_id='controller1', max_rows=64, max_read_bytes=1048576)
        assert (await reader.positions(t.cfg.binding)).positions == captured.positions
        with pytest.raises(StorageError, match='PG_SPOT_COMMAND_INVALID'):
            await reader.stage(captured, cut=(await reader_financial.snapshot(t.cfg.binding)).cut, owner=t.owner)
        c = await asyncpg.connect(cfg.dsn)
        try:
            for signature in ('recorder_position_stage(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb)',
                              'recorder_position_apply(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.identity)',
                              'recorder_native_put(text,jsonb,jsonb,jsonb)', 'recorder_native_get_v18(text,jsonb)'):
                assert not await c.fetchval('SELECT has_function_privilege(current_user,$1,\'EXECUTE\')', 'life_financial.' + signature)
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await c.execute('DELETE FROM life_recorder."Position"')
        finally:
            await c.close()
    finally:
        if reader_financial is not None:
            await reader_financial.close()
        await t.close()
