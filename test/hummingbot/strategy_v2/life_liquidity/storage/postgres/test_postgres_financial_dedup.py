"""Financial receipts qualify reporting IDs before actual SDK cache recovery."""
from dataclasses import asdict
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_events import event
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_financial_observer import observed_source

import pytest

from hummingbot.connector.utils import TradeFillOrderDetails
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
from hummingbot.strategy_v2.life_liquidity.storage.postgres.events import PgEventRepository
from hummingbot.strategy_v2.life_liquidity.storage.postgres.financial_dedup import PgSpotDedupRecovery
from hummingbot.strategy_v2.life_liquidity.storage.recorder_events import EventSource, json_text


async def setup(database, *, apply_finance=True, changes=None, fee_asset='USDT', signed_fee='-.02', **seed_options):
    source = EventSource('controller1', 'life.yml', 'strategy_v2', 'okx')
    t, observer, payloads, requests, values = await observed_source(database, **({'configuration_body': lambda _: {'recorder_sources': [asdict(source)]}} | seed_options))
    fee_amount = D(signed_fee)
    payloads['/api/v5/trade/fills-history'][0].update(feeCcy=fee_asset, fee=signed_fee)
    payloads['/api/v5/account/bills-archive'][0].update(
        fee=signed_fee if fee_asset == 'USDT' else '0', balChg=str(D('-1') + (fee_amount if fee_asset == 'USDT' else 0)))
    payloads['/api/v5/account/bills-archive'][1].update(
        fee=signed_fee if fee_asset == 'LIFE' else '0', balChg=str(D('1') + (fee_amount if fee_asset == 'LIFE' else 0)))
    payloads['/api/v5/account/balance'][0]['details'][0]['cashBal'] = str(D('6') + (fee_amount if fee_asset == 'LIFE' else 0))
    payloads['/api/v5/account/balance'][0]['details'][1]['cashBal'] = str(D('11') + (fee_amount if fee_asset == 'USDT' else 0))
    events = PgEventRepository(t.repo, source=source, max_rows=64, max_read_bytes=1048576)
    if apply_finance:
        await observer.observe()
    # Report time is an SDK observation time, not the native exchange fill time.
    at = int(t.journal.clock().timestamp() * 1000)
    fee = json_text({'fee_type': 'AddedToCost', 'percent': '0', 'percent_token': fee_asset,
                     'flat_fees': [{'token': fee_asset, 'amount': str(-fee_amount)}]})
    created = event(t.cfg.binding, source, at)
    body = {'fee_quote': str(-fee_amount), 'fee_json': fee}
    body.update(changes or {})
    fill = event(t.cfg.binding, source, at + 1, 'OrderFilled', **body)
    await events.project(created, owner=t.owner)
    await events.project(fill, owner=t.owner)
    return t, observer, events, fill


@pytest.mark.asyncio
async def test_actual_sdk_dedup_requires_financial_and_report_receipts_and_never_restores_tracking(database):
    t, observer, events, fill = await setup(database)
    try:
        before = await t.repo.snapshot(t.cfg.binding)
        recovery = PgSpotDedupRecovery(observer.reader, events, local_idle=lambda: True)
        assert await recovery.restore() == (1, 1)
        assert t.connector._current_trade_fills == {TradeFillOrderDetails('okx', 'trade1', 'LIFE-USDT')}
        assert t.connector._exchange_order_ids == {'123': 'wire1'}
        assert not t.connector.is_confirmed_new_order_filled_event('trade1', '123', 'LIFE-USDT')
        assert t.connector.is_confirmed_new_order_filled_event('trade2', '123', 'LIFE-USDT')
        assert await recovery.restore() == (1, 1)
        assert (await t.repo.snapshot(t.cfg.binding)).cut == before.cut
        assert (await t.repo.balance(t.cfg.binding)).reserved_quote_usdt > 0 and len(t.session.sent) == 1
        assert t.connector.in_flight_orders == {}
    finally:
        await t.close()


@pytest.mark.asyncio
async def test_reporting_fill_without_financial_application_cannot_suppress_sdk_fill(database):
    t, observer, events, fill = await setup(database, apply_finance=False)
    try:
        assert (await events.read(t.cfg.binding)).trade_ids == (('okx', 'trade1', 'LIFE-USDT'),)
        with pytest.raises(StorageError, match='PG_SPOT_DEDUP_FILL_UNQUALIFIED'):
            await PgSpotDedupRecovery(observer.reader, events, local_idle=lambda: True).restore()
        assert not t.connector._current_trade_fills and not t.connector._exchange_order_ids
        assert (await t.repo.balance(t.cfg.binding)).total_quote_usdt == 10
    finally:
        await t.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('changes,reason', [
    ({'amount': '1.1'}, 'PG_SPOT_DEDUP_FILL_UNQUALIFIED'),
    ({'price': '1.01'}, 'PG_SPOT_DEDUP_FILL_UNQUALIFIED'),
    ({'fee_quote': '.03'}, 'PG_SPOT_DEDUP_FEE_UNQUALIFIED'),
    ({'fee_json': json_text({'percent': '.01', 'percent_token': 'USDT', 'flat_fees': []})}, 'PG_SPOT_DEDUP_FEE_UNQUALIFIED'),
    ({'fee_json': json_text({'percent': '0', 'percent_token': 'LIFE', 'flat_fees': [{'token': 'LIFE', 'amount': '.02'}]})}, 'PG_SPOT_DEDUP_FEE_UNQUALIFIED'),
    ({'trade_id': 'foreign'}, 'PG_SPOT_DEDUP_FILL_UNQUALIFIED'),
])
async def test_diagnostic_trade_must_match_original_financial_values(database, changes, reason):
    t, observer, events, fill = await setup(database, changes=changes)
    try:
        with pytest.raises(StorageError, match=reason):
            await PgSpotDedupRecovery(observer.reader, events, local_idle=lambda: True).restore()
        assert not t.connector._current_trade_fills and not t.connector._exchange_order_ids
        assert not t.repo._runtime.healthy and len(t.session.sent) == 1
    finally:
        await t.close()


@pytest.mark.asyncio
async def test_failure_between_actual_sdk_cache_publications_restores_original_empty_cache(database, monkeypatch):
    t, observer, events, fill = await setup(database)
    original = t.connector.add_exchange_order_ids_from_market_recorder

    def failed(value):
        original(value)
        raise StorageError('PG_SYNTHETIC_CACHE_PUBLICATION_FAILURE')
    monkeypatch.setattr(t.connector, 'add_exchange_order_ids_from_market_recorder', failed)
    try:
        with pytest.raises(StorageError, match='PG_SYNTHETIC_CACHE_PUBLICATION_FAILURE'):
            await PgSpotDedupRecovery(observer.reader, events, local_idle=lambda: True).restore()
        assert not t.connector._current_trade_fills and not t.connector._exchange_order_ids
        assert not t.repo._runtime.healthy
    finally:
        await t.close()


@pytest.mark.asyncio
async def test_unknown_existing_sdk_cache_is_not_blessed_or_cleared(database):
    t, observer, events, fill = await setup(database)
    existing = TradeFillOrderDetails('okx', 'foreign', 'LIFE-USDT')
    t.connector._current_trade_fills.add(existing)
    try:
        with pytest.raises(StorageError, match='PG_SPOT_DEDUP_EXISTING_CACHE_UNQUALIFIED'):
            await PgSpotDedupRecovery(observer.reader, events, local_idle=lambda: True).restore()
        assert t.connector._current_trade_fills == {existing} and not t.connector._exchange_order_ids
    finally:
        await t.close()


@pytest.mark.asyncio
async def test_cut_change_after_financial_read_denies_all_sdk_cache_publication(database, monkeypatch):
    t, observer, events, fill = await setup(database)
    original = events._read

    async def changed(*args, **kwargs):
        await database[0].execute('UPDATE life_financial.accounts SET halted=true WHERE account_id=$1', t.cfg.account_id)
        return await original(*args, **kwargs)
    monkeypatch.setattr(events, '_read', changed)
    try:
        with pytest.raises(StorageError, match='PG_SPOT_DEDUP_CUT_CHANGED'):
            await PgSpotDedupRecovery(observer.reader, events, local_idle=lambda: True).restore()
        assert not t.connector._current_trade_fills and not t.connector._exchange_order_ids
    finally:
        await t.close()


@pytest.mark.asyncio
async def test_unapplied_event_outbox_denies_cache_recovery_without_financial_mutation(database):
    t, observer, events, fill = await setup(database)
    try:
        at = int(t.journal.clock().timestamp() * 1000)
        pending = event(t.cfg.binding, events.source, at, 'OrderCancelled')
        await events.stage(pending, cut=(await t.repo.snapshot(t.cfg.binding)).cut, owner=t.owner)
        before = await t.repo.balance(t.cfg.binding)
        with pytest.raises(StorageError, match='PG_RECORDER_DELIVERY_PENDING'):
            await PgSpotDedupRecovery(observer.reader, events, local_idle=lambda: True).restore()
        assert not t.connector._current_trade_fills and not t.connector._exchange_order_ids
        assert await t.repo.balance(t.cfg.binding) == before
    finally:
        await t.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('race', [False, True])
async def test_actual_recorder_application_restores_only_idle_financially_qualified_spot_ids(database, race):
    from dataclasses import replace
    from types import SimpleNamespace

    from controllers.generic.life_liquidity import LifeLiquidityConfig
    from hummingbot.strategy_v2.life_liquidity.storage.contracts import fingerprint
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.financial_source import PgSpotAccountReader
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.quote_account_source import PgQuoteAccountSource
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.recorder_application import PgRecorderApplication
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.runner_journal import PgQuoteRunnerJournal
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
    template = LifeLiquidityConfig(id='controller1', storage_backend='postgres')
    source = EventSource(template.id, 'life.yml', 'strategy_v2', 'okx')

    def configuration(binding):
        registered = template.model_copy(update={'storage_binding': binding, 'recovery_account_uid': binding.account_uid})
        return {'controller': registered.model_dump(mode='json'), 'recorder_sources': [asdict(source)]}

    t, observer, events, fill = await setup(
        database, config_hash=fingerprint(template.strategy.model_dump(mode='json')), configuration_body=configuration)
    financial = PgSpotRepository(await PgRuntime.open(replace(t.cfg, safety_size=2)))
    controller = SimpleNamespace(config=template.model_copy(update={'storage_binding': t.cfg.binding, 'recovery_account_uid': t.cfg.binding.account_uid}))
    app = None
    try:
        app = await PgRecorderApplication.install(financial, controller=controller, owner=t.owner, sources=(source,),
                                                  revoke=lambda: None, local_ready=lambda: False, max_pending=2,
                                                  max_callbacks=2, shutdown_timeout_s=2, max_rows=64, max_read_bytes=1048576)
        journal = PgQuoteRunnerJournal(financial, owner=t.owner, controller_id=source.controller_id,
                                       config_version=1, runner_facts=t.journal.runner_facts, local_ready=lambda: False,
                                       clock=t.journal.clock, max_actions=64)
        account = await PgQuoteAccountSource.create(journal, connector=t.connector, admission_source=t.facts,
                                                    max_uid_age_ms=1000, max_auth_age_ms=1000, max_clock_skew_ms=100,
                                                    max_pending=2, collection_timeout_ms=1000)
        reader = PgSpotAccountReader(account, max_reads=64, max_bytes=1048576, timeout_ms=1000)
        if race:
            original_http = t.session.request
            uid_count = [0]

            async def http(**kwargs):
                response = await original_http(**kwargs)
                if kwargs['url'].endswith('/account/config'):
                    uid_count[0] += 1
                    if uid_count[0] == 2:
                        at = int(t.journal.clock().timestamp() * 1000)
                        app.enqueue_event(event(t.cfg.binding, source, at, 'OrderCancelled'))
                return response
            t.session.request = http
            with pytest.raises(StorageError, match='PG_SPOT_DEDUP_CONTEXT_CHANGED'):
                await app.restore_spot_dedup(reader)
            await app.drain()
            assert not t.connector._current_trade_fills and not t.connector._exchange_order_ids
        else:
            assert await app.restore_spot_dedup(reader) == (1, 1)
            assert len(t.connector._current_trade_fills) == 1
        assert journal._ready() is False and not await app.before_dispatch(t.create)
        assert len(t.session.sent) == 1
        assert (await financial.balance(t.cfg.binding)).reserved_quote_usdt > 0
        assert t.connector.in_flight_orders == {}
    finally:
        if app is not None:
            await app.close()
        await financial.close()
        await t.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('asset,fee', [('USDT', '-.02'), ('USDT', '.02'), ('LIFE', '-.02'), ('LIFE', '.02')])
async def test_signed_native_fee_debits_and_rebates_qualify_without_new_wallet_delta(database, asset, fee):
    t, observer, events, fill = await setup(database, fee_asset=asset, signed_fee=fee)
    try:
        before = await t.repo.snapshot(t.cfg.binding)
        assert await PgSpotDedupRecovery(observer.reader, events, local_idle=lambda: True).restore() == (1, 1)
        assert (await t.repo.snapshot(t.cfg.binding)).cut == before.cut
        assert len(t.connector._current_trade_fills) == 1 and len(t.session.sent) == 1
    finally:
        await t.close()


@pytest.mark.asyncio
async def test_received_but_undelivered_financial_intake_blocks_sdk_suppression(database):
    from hummingbot.strategy_v2.life_liquidity.storage.financial import FillSpot
    t, observer, events, fill = await setup(database)
    try:
        context = await observer.context.observation_context(t.cfg.binding)
        prepared, recorded = context.commands[0], context.fills[0]
        command = FillSpot('pending-sdk', prepared.operation_id, prepared.intent_id, prepared.client_order_id,
                           prepared.market, prepared.side, prepared.session_id, prepared.epoch, recorded.exchange_order_id,
                           recorded.fill.quantity_base_life, (recorded.fill,), t.journal.clock())
        await observer.intake.capture(command, owner=t.owner)
        with pytest.raises(StorageError, match='PG_SPOT_DEDUP_FINANCIAL_PENDING'):
            await PgSpotDedupRecovery(observer.reader, events, local_idle=lambda: True).restore()
        assert not t.connector._current_trade_fills and not t.connector._exchange_order_ids
    finally:
        await t.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('field', ['runtime', 'owner', 'local_idle'])
async def test_original_context_and_local_idle_are_rechecked_after_final_primary_await(database, monkeypatch, field):
    from dataclasses import replace
    from types import SimpleNamespace
    t, observer, events, fill = await setup(database)
    runtime, owner, idle = t.repo._runtime, t.journal.owner, [True]
    original = t.repo.snapshot
    # Context SQL reads directly, so this is specifically the last primary await.

    async def changed(*args, **kwargs):
        result = await original(*args, **kwargs)
        if field == 'runtime':
            t.repo._runtime = SimpleNamespace(healthy=True, _closing=False)
        elif field == 'owner':
            t.journal.owner = replace(owner, epoch=owner.epoch + 1)
        else:
            idle[0] = False
        return result
    monkeypatch.setattr(t.repo, 'snapshot', changed)
    try:
        with pytest.raises(StorageError, match='PG_SPOT_DEDUP_CONTEXT_CHANGED'):
            await PgSpotDedupRecovery(observer.reader, events, local_idle=lambda: idle[0]).restore()
        assert not t.connector._current_trade_fills and not t.connector._exchange_order_ids
        assert not runtime.healthy
    finally:
        t.repo._runtime, t.journal.owner = runtime, owner
        await t.close()
