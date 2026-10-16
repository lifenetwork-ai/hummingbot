"""Multiple reporting effects commit together, with no financial authority."""
import json
from dataclasses import asdict, replace
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_quote_transport import transport

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
from hummingbot.strategy_v2.life_liquidity.storage.postgres.events import PgEventRepository
from hummingbot.strategy_v2.life_liquidity.storage.recorder_events import EventProjection, EventSource, json_text


async def setup(database, *, registered=True, market='okx'):
    source = EventSource('controller1', 'life.yml', 'strategy_v2', market)
    t = await transport(database, configuration_body=lambda _: {'recorder_sources': [asdict(source)]} if registered else {})
    repo = PgEventRepository(t.repo, source=source, max_rows=64, max_read_bytes=1048576)
    await t.place()  # Actual OKX/auth/REST code over the existing fake HTTP fixture.
    at = await database[0].fetchval('SELECT floor(extract(epoch FROM clock_timestamp())*1000)::bigint')
    return t, repo, source, at


def event(binding, source, at, kind='BuyOrderCreated', **changes):
    body = {'kind': kind, 'timestamp_ms': at}
    if kind in ('BuyOrderCreated', 'SellOrderCreated', 'OrderFilled'):
        body.update(wire='wire1', pair='LIFE-USDT', exchange_id='123', order_type='LIMIT_MAKER', side='BUY',
                    amount='2', price='1', leverage=1, position='OPEN')
    if kind == 'OrderFilled':
        body.update(amount='1', trade_id='trade1', fee_json=json_text({'percent': '0', 'percent_token': None, 'flat_fees': []}), fee_quote='0')
    elif kind == 'FundingPaymentCompleted':
        body.update(pair='LIFE-USDT', rate='0.001', amount='-0.2')
    elif kind not in ('BuyOrderCreated', 'SellOrderCreated'):
        body.update(wire='wire1', exchange_id='123')
    body.update(changes)
    return EventProjection(binding, source, json_text(body), '{}')


@pytest.mark.asyncio
async def test_order_status_tracking_and_receipt_are_atomic_and_idempotent(database):
    t, repo, source, at = await setup(database)
    try:
        command = event(t.cfg.binding, source, at)
        before = await t.repo.snapshot(t.cfg.binding)
        await repo.stage(command, cut=before.cut, owner=t.owner)
        assert len(await repo.pending(t.cfg.binding)) == 1
        assert (await t.repo.snapshot(t.cfg.binding)).recovery_required
        assert await database[0].fetchval('SELECT count(*) FROM life_recorder."Order"') == 0
        assert await database[0].fetchval('SELECT count(*) FROM life_recorder."OrderStatus"') == 0
        result = await repo.apply(command.operation_id, cut=(await t.repo.snapshot(t.cfg.binding)).cut, owner=t.owner)
        assert not result.trading_permission
        assert await database[0].fetchval('SELECT amount FROM life_recorder."Order"') == 2000000
        assert await database[0].fetchval('SELECT count(*) FROM life_recorder."OrderStatus"') == 1
        assert await database[0].fetchval('SELECT count(*) FROM life_recorder."MarketState"') == 1
        assert (await repo.stage(command, cut=before.cut, owner=t.owner)).replayed
        assert (await repo.apply(command.operation_id, cut=before.cut, owner=t.owner)).replayed
        assert await repo.pending(t.cfg.binding) == ()
        assert await t.repo.balance(t.cfg.binding) == before.balance
        assert len(t.session.sent) == 1
    finally:
        await t.close()


@pytest.mark.asyncio
async def test_fill_before_create_and_late_status_preserve_latest_order_state(database):
    t, repo, source, at = await setup(database)
    try:
        before = await t.repo.balance(t.cfg.binding)
        fill = event(t.cfg.binding, source, at, 'OrderFilled')
        await repo.project(fill, owner=t.owner)
        assert await database[0].fetchval('SELECT count(*) FROM life_recorder."Order"') == 0
        assert (await repo.connector_history(source, t.cfg.binding)).exchange_ids == ()
        create = event(t.cfg.binding, source, at - 1)
        await repo.project(create, owner=t.owner)
        assert await database[0].fetchval('SELECT last_status FROM life_recorder."Order"') == 'OrderFilled'
        await repo.project(event(t.cfg.binding, source, at + 1, 'OrderCancelled'), owner=t.owner)
        await repo.project(create, owner=t.owner)
        assert await database[0].fetchval('SELECT last_status FROM life_recorder."Order"') == 'OrderCancelled'
        assert await database[0].fetchval('SELECT count(*) FROM life_recorder."TradeFill"') == 1
        history = await repo.connector_history(source, t.cfg.binding)
        assert history.trade_ids == (('okx', 'trade1', 'LIFE-USDT'),)
        assert history.exchange_ids == (('123', 'wire1'),)
        assert await t.repo.balance(t.cfg.binding) == before
        assert len(t.session.sent) == 1
    finally:
        await t.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['source', 'wire', 'amount', 'tracking'])
async def test_unregistered_or_conflicting_event_is_not_adopted(database, fault):
    t, repo, source, at = await setup(database, registered=fault != 'source')
    try:
        command = event(t.cfg.binding, source, at)
        if fault == 'wire':
            command = event(t.cfg.binding, source, at, wire='foreign')
        elif fault == 'amount':
            command = event(t.cfg.binding, source, at, amount='9')
        elif fault == 'tracking':
            command = replace(command, tracking_json=json_text({'foreign': {}}))
        before = await t.repo.snapshot(t.cfg.binding)
        expected = {'source': 'PG_EVENT_SOURCE_INVALID', 'wire': 'PG_EVENT_WIRE_INVALID',
                    'amount': 'PG_EVENT_ORDER_INVALID', 'tracking': 'PG_EVENT_TRACKING_INVALID'}[fault]
        with pytest.raises(StorageError, match=expected):
            await repo.stage(command, cut=before.cut, owner=t.owner)
        assert (await t.repo.snapshot(t.cfg.binding)).cut == before.cut
        assert await database[0].fetchval('SELECT count(*) FROM life_recorder."Order"') == 0
        assert len(t.session.sent) == 1
    finally:
        await t.close()


@pytest.mark.asyncio
async def test_changed_trade_terms_under_economic_identity_conflict(database):
    t, repo, source, at = await setup(database)
    try:
        command = event(t.cfg.binding, source, at, 'OrderFilled')
        await repo.project(command, owner=t.owner)
        changed = event(t.cfg.binding, source, at + 1, 'OrderFilled', fee_quote='0.1')
        assert changed.operation_id == command.operation_id
        before = await t.repo.snapshot(t.cfg.binding)
        with pytest.raises(StorageError):
            await repo.stage(changed, cut=before.cut, owner=t.owner)
        assert (await t.repo.snapshot(t.cfg.binding)).cut == before.cut
        assert await database[0].fetchval('SELECT count(*) FROM life_recorder."TradeFill"') == 1
    finally:
        await t.close()


@pytest.mark.asyncio
async def test_funding_is_a_report_and_never_a_financial_credit(database):
    t, repo, source, at = await setup(database, market='okx_perpetual')
    try:
        before = await t.repo.snapshot(t.cfg.binding)
        await repo.project(event(t.cfg.binding, source, at, 'FundingPaymentCompleted'), owner=t.owner)
        assert await database[0].fetchval('SELECT count(*) FROM life_recorder."FundingPayment"') == 1
        assert await t.repo.balance(t.cfg.binding) == before.balance
        assert await database[0].fetchval("SELECT count(*) FROM life_financial.financial_events WHERE kind='FUNDING'") == 0
        assert len(t.session.sent) == 1
    finally:
        await t.close()


@pytest.mark.asyncio
async def test_mid_application_failure_rolls_back_every_effect_and_head(database):
    t, repo, source, at = await setup(database)
    try:
        command = event(t.cfg.binding, source, at)
        await repo.stage(command, cut=(await t.repo.snapshot(t.cfg.binding)).cut, owner=t.owner)
        before = await t.repo.snapshot(t.cfg.binding)
        await database[0].execute('''CREATE FUNCTION life_recorder.test_failure() RETURNS trigger
            LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'LIFE_EVENT_SYNTHETIC_FAILURE' USING ERRCODE='P0001'; END; $$;
            CREATE TRIGGER synthetic_failure BEFORE INSERT ON life_recorder."OrderStatus"
                FOR EACH ROW EXECUTE FUNCTION life_recorder.test_failure()''')
        with pytest.raises(StorageError, match='PG_EVENT_SYNTHETIC_FAILURE'):
            await repo.apply(command.operation_id, cut=before.cut, owner=t.owner)
        assert (await t.repo.snapshot(t.cfg.binding)).cut == before.cut
        for table in ('Order', 'OrderStatus', 'MarketState'):
            assert await database[0].fetchval(f'SELECT count(*) FROM life_recorder."{table}"') == 0
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.recorder_projection_heads') == 0
        assert len(await repo.pending(t.cfg.binding)) == 1
        await database[0].execute('DROP TRIGGER synthetic_failure ON life_recorder."OrderStatus"')
        await repo.apply(command.operation_id, cut=before.cut, owner=t.owner)
        for table in ('Order', 'OrderStatus', 'MarketState'):
            assert await database[0].fetchval(f'SELECT count(*) FROM life_recorder."{table}"') == 1
        assert await t.repo.balance(t.cfg.binding) == before.balance and len(t.session.sent) == 1
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
    t, repo, source, at = await setup(database)
    command = event(t.cfg.binding, source, at)
    if phase == 'apply':
        await repo.stage(command, cut=(await t.repo.snapshot(t.cfg.binding)).cut, owner=t.owner)
    before = await t.repo.snapshot(t.cfg.binding)
    admin = None
    original = t.repo._runtime._commit
    try:
        if fault == 'process':
            await exit_report(t.cfg, command, t.owner, before.cut, phase, committed)
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
                    await repo.stage(command, cut=before.cut, owner=t.owner)
                else:
                    await repo.apply(command.operation_id, cut=before.cut, owner=t.owner)
    finally:
        if admin is not None:
            await admin.close()
        await t.close()
    financial = PgSpotRepository(await PgRuntime.open(t.cfg))
    cold = PgEventRepository(financial, source=source, max_rows=64, max_read_bytes=1048576)
    try:
        assert len(await cold.pending(t.cfg.binding)) == int(committed if phase == 'stage' else not committed)
        for table in ('Order', 'OrderStatus', 'MarketState'):
            assert await database[0].fetchval(f'SELECT count(*) FROM life_recorder."{table}"') == int(phase == 'apply' and committed)
        await cold.project(command, owner=t.owner)
        assert await cold.pending(t.cfg.binding) == ()
        for table in ('Order', 'OrderStatus', 'MarketState'):
            assert await database[0].fetchval(f'SELECT count(*) FROM life_recorder."{table}"') == 1
        assert await financial.balance(t.cfg.binding) == before.balance and len(t.session.sent) == 1
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('phase', ['stage', 'apply'])
async def test_generation_change_at_event_commit_rolls_back_native_rows(database, monkeypatch, phase):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.helpers import install_generation_fault
    t, repo, source, at = await setup(database)
    await install_generation_fault(database)
    try:
        command = event(t.cfg.binding, source, at)
        if phase == 'apply':
            await repo.stage(command, cut=(await t.repo.snapshot(t.cfg.binding)).cut, owner=t.owner)
        before = await t.repo.snapshot(t.cfg.binding)
        original = t.repo._runtime._commit

        async def changed(tx):
            await tx._connection.execute('SELECT life_financial.test_change_generation($1)', t.cfg.account_id)
            await original(tx)
        monkeypatch.setattr(t.repo._runtime, '_commit', changed)
        with pytest.raises(StorageError, match='OWNER_EXPIRED_OR_CHANGED_AT_COMMIT'):
            if phase == 'stage':
                await repo.stage(command, cut=before.cut, owner=t.owner)
            else:
                await repo.apply(command.operation_id, cut=before.cut, owner=t.owner)
        assert (await t.repo.snapshot(t.cfg.binding)).cut == before.cut
        assert await database[0].fetchval('SELECT count(*) FROM life_recorder."Order"') == 0
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.recorder_projection_heads') == 0
    finally:
        await t.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('target', ['Order', 'OrderStatus', 'TradeFill', 'MarketState', 'effects', 'heads'])
async def test_corruption_of_any_reporting_effect_blocks_history_and_new_risk(database, target):
    t, repo, source, at = await setup(database)
    try:
        await repo.project(event(t.cfg.binding, source, at), owner=t.owner)
        await repo.project(event(t.cfg.binding, source, at + 1, 'OrderFilled'), owner=t.owner)
        if target == 'effects':
            await database[0].execute('ALTER TABLE life_financial.recorder_projection_effects DISABLE TRIGGER USER')
            await database[0].execute("UPDATE life_financial.recorder_projection_effects SET after_row=jsonb_set(after_row,'{last_status}','\"foreign\"') WHERE table_kind='ORDER'")
            await database[0].execute('ALTER TABLE life_financial.recorder_projection_effects ENABLE TRIGGER USER')
        elif target == 'heads':
            await database[0].execute('ALTER TABLE life_financial.recorder_projection_heads DISABLE TRIGGER USER')
            await database[0].execute('UPDATE life_financial.recorder_projection_heads SET version=99')
            await database[0].execute('ALTER TABLE life_financial.recorder_projection_heads ENABLE TRIGGER USER')
        else:
            field = {'Order': 'last_status=\'foreign\'', 'OrderStatus': 'status=\'foreign\'', 'TradeFill': 'amount=99', 'MarketState': 'timestamp=1'}[target]
            await database[0].execute(f'UPDATE life_recorder."{target}" SET {field}')
        with pytest.raises(StorageError, match='PG_RECORDER_UNVERIFIED'):
            await repo.connector_history(source, t.cfg.binding)
        assert (await t.repo.snapshot(t.cfg.binding)).recovery_required
        assert len(t.session.sent) == 1
    finally:
        await t.close()


@pytest.mark.asyncio
async def test_foreign_legacy_order_is_not_adopted_even_by_a_fill_only_event(database):
    t, repo, source, at = await setup(database)
    try:
        await database[0].execute('''INSERT INTO life_recorder."Order" VALUES(
            'wire1','foreign.yml','foreign','okx','LIFE-USDT','LIFE','USDT',$1,'LIMIT_MAKER',2000000,1,1000000,'foreign',$1,'123','OPEN')''', at)
        before = await t.repo.snapshot(t.cfg.binding)
        with pytest.raises(StorageError, match='PG_EVENT_NATIVE_CONFLICT'):
            await repo.stage(event(t.cfg.binding, source, at, 'OrderFilled'), cut=before.cut, owner=t.owner)
        assert (await t.repo.snapshot(t.cfg.binding)).cut == before.cut
        assert await database[0].fetchval('SELECT count(*) FROM life_recorder."TradeFill"') == 0
    finally:
        await t.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['missing', 'null', 'identity', 'leverage', 'side', 'pair', 'fee', 'tracking'])
async def test_raw_sql_event_commands_cannot_bypass_native_shape_or_identity(database, fault):
    import asyncpg
    t, repo, source, at = await setup(database)
    c = await asyncpg.connect(t.cfg.dsn)
    try:
        command = event(t.cfg.binding, source, at, 'OrderFilled')
        value, operation = command.json(), command.operation_id
        body = json.loads(value['payload_json'])
        if fault == 'missing':
            body.pop('kind')
        elif fault == 'null':
            body['position'] = None
        elif fault == 'identity':
            operation = 'event:' + 'f' * 64
        elif fault == 'leverage':
            body['leverage'] = 2
        elif fault == 'side':
            body['side'] = 'SELL'
        elif fault == 'pair':
            body['pair'] = None
        elif fault == 'fee':
            body['fee_quote'] = None
        else:
            value['tracking_json'] = '[]'
        value['payload_json'] = json_text(body)
        before = await t.repo.snapshot(t.cfg.binding)
        with pytest.raises(asyncpg.RaiseError, match='EVENT_'):
            await c.fetchval('SELECT life_financial.recorder_event_stage($1,$2::jsonb,$3::jsonb,$4,$5,$6,$7,$8::jsonb)',
                             t.cfg.account_id, json.dumps(t.cfg.binding.json()), json.dumps(t.owner.json()), before.cut.checkpoint.revision,
                             before.cut.checkpoint.state_digest, 'f' * 64, operation, json.dumps(value))
        assert (await t.repo.snapshot(t.cfg.binding)).cut == before.cut
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.recorder_projection_effects') == 0
    finally:
        await c.close()
        await t.close()


@pytest.mark.asyncio
async def test_actual_sdk_event_matches_file_effect_units_and_preserves_precise_signed_fee(database):
    from decimal import Decimal as D
    from types import SimpleNamespace

    from hummingbot.core.data_type.common import OrderType, TradeType
    from hummingbot.core.data_type.trade_fee import AddedToCostTradeFee, TokenAmount
    from hummingbot.core.event.events import MarketEvent, OrderFilledEvent
    from hummingbot.strategy_v2.life_liquidity.storage.recorder_events import FileEventAdapter
    t, repo, source, at = await setup(database)
    try:
        sdk = OrderFilledEvent(at / 1000, 'wire1', 'LIFE-USDT', TradeType.BUY, OrderType.LIMIT_MAKER, D('1'), D('1'),
                               AddedToCostTradeFee(flat_fees=[TokenAmount('USDT', D('-.0000009'))]), 'precise', '123')
        command = EventProjection.capture(t.cfg.binding, source, MarketEvent.OrderFilled.value, sdk, {})
        native_effects, _ = FileEventAdapter._effects(None, command, SimpleNamespace(resources={}))
        expected = next(json.loads(row['after']) for row in json.loads(native_effects) if row['table'] == 'trade')
        before = await t.repo.balance(t.cfg.binding)
        await repo.project(command, owner=t.owner)
        row = await database[0].fetchrow('SELECT * FROM life_recorder."TradeFill"')
        assert D(row['price']) / 10**6 == D(expected['price'])
        assert D(row['amount']) / 10**6 == D(expected['amount'])
        assert D(row['trade_fee_in_quote']) / 10**6 == D(expected['trade_fee_in_quote']) == 0
        assert json.loads(row['trade_fee']) == expected['trade_fee']
        assert json.loads(command.payload_json)['fee_quote'] == '-9E-7'
        assert await t.repo.balance(t.cfg.binding) == before
    finally:
        await t.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('limit', ['rows', 'bytes'])
async def test_event_history_capacities_deny_partial_scope(database, limit):
    t, repo, source, at = await setup(database)
    try:
        await repo.project(event(t.cfg.binding, source, at), owner=t.owner)
        await repo.project(event(t.cfg.binding, source, at + 1, 'OrderFilled'), owner=t.owner)
        bounded = PgEventRepository(
            t.repo, source=source, max_rows=1 if limit == 'rows' else 64,
            max_read_bytes=8 if limit == 'bytes' else 1048576)
        with pytest.raises(StorageError, match='PG_RECORDER_(BYTE_)?CAPACITY'):
            await bounded.connector_history(source, t.cfg.binding)
    finally:
        await t.close()


@pytest.mark.asyncio
async def test_unicode_source_identity_matches_the_existing_native_fingerprint(database):
    source = EventSource('controller1', 'thanh khoản-😀-\u007f.yml', 'strategy_v2', 'okx')
    t = await transport(database, configuration_body=lambda _: {'recorder_sources': [asdict(source)]})
    repo = PgEventRepository(t.repo, source=source, max_rows=64, max_read_bytes=1048576)
    try:
        await t.place()
        at = await database[0].fetchval('SELECT floor(extract(epoch FROM clock_timestamp())*1000)::bigint')
        command = event(t.cfg.binding, source, at)
        await repo.project(command, owner=t.owner)
        assert await repo.pending(t.cfg.binding) == ()
        assert (await repo.connector_history(source, t.cfg.binding)).exchange_ids == (('123', 'wire1'),)
    finally:
        await t.close()


@pytest.mark.asyncio
async def test_read_only_role_can_inspect_events_but_not_write_or_invoke_private_effects(database):
    from uuid import uuid4

    import asyncpg

    from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
    t, repo, source, at = await setup(database)
    financial = None
    try:
        await repo.project(event(t.cfg.binding, source, at), owner=t.owner)
        cfg = replace(t.cfg, dsn=t.cfg.dsn.replace('life_pg2_runtime@', 'life_pg2_reporter@'), principal='life_pg2_reporter', write=False)
        financial = PgSpotRepository(await PgRuntime.open(cfg))
        reader = PgEventRepository(financial, source=source, max_rows=64, max_read_bytes=1048576)
        assert (await reader.connector_history(source, t.cfg.binding)).exchange_ids == (('123', 'wire1'),)
        with pytest.raises(StorageError, match='PG_SPOT_COMMAND_INVALID'):
            await reader.stage(event(t.cfg.binding, source, at), cut=(await financial.snapshot(t.cfg.binding)).cut, owner=t.owner)
        c = await asyncpg.connect(cfg.dsn)
        try:
            with pytest.raises(asyncpg.RaiseError, match='ACCOUNT_SCOPE_INVALID'):
                await c.fetchval('SELECT life_financial.recorder_executor_verified($1)', uuid4())
            for signature in ('recorder_event_stage(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,life_financial.identity,jsonb)',
                              'recorder_event_apply(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.identity)',
                              'recorder_native_put(text,jsonb,jsonb,jsonb)', 'recorder_event_integrity(uuid)'):
                assert not await c.fetchval('SELECT has_function_privilege(current_user,$1,\'EXECUTE\')', 'life_financial.' + signature)
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await c.execute('DELETE FROM life_financial.recorder_projection_heads')
        finally:
            await c.close()
    finally:
        if financial is not None:
            await financial.close()
        await t.close()


@pytest.mark.asyncio
async def test_connector_history_matches_sdk_trade_and_exchange_cache_contract(database):
    from hummingbot.connector.utils import TradeFillOrderDetails
    t, repo, source, at = await setup(database)
    try:
        await repo.project(event(t.cfg.binding, source, at), owner=t.owner)
        await repo.project(event(t.cfg.binding, source, at + 1, 'OrderFilled'), owner=t.owner)
        history = await repo.connector_history(source, t.cfg.binding)
        assert {TradeFillOrderDetails(*row) for row in history.trade_ids} == {TradeFillOrderDetails('okx', 'trade1', 'LIFE-USDT')}
        assert dict(history.exchange_ids) == {'123': 'wire1'}
    finally:
        await t.close()
