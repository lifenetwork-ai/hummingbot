"""Actual OKX PubSub callbacks -> PG reporting -> signed account facts -> SDK IDs."""
import asyncio
from dataclasses import asdict, replace
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_financial_observer import observed_source
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_quote_account_source import source
from types import SimpleNamespace

import pytest

from controllers.generic.life_liquidity import LifeLiquidityConfig
from hummingbot.core.data_type.common import OrderType, TradeType
from hummingbot.core.data_type.trade_fee import AddedToCostTradeFee, TokenAmount
from hummingbot.core.event.events import BuyOrderCreatedEvent, MarketEvent, OrderFilledEvent
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError, fingerprint
from hummingbot.strategy_v2.life_liquidity.storage.postgres.financial_observer import PgSpotFinancialObserver
from hummingbot.strategy_v2.life_liquidity.storage.postgres.financial_source import PgSpotAccountReader
from hummingbot.strategy_v2.life_liquidity.storage.postgres.recorder_application import PgRecorderApplication
from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
from hummingbot.strategy_v2.life_liquidity.storage.recorder_events import EventSource


async def setup(database):
    template = LifeLiquidityConfig(id='controller1', storage_backend='postgres')
    event_source = EventSource(template.id, 'life.yml', 'strategy_v2', 'okx')
    t, old_observer, payloads, requests, values = await observed_source(
        database, config_hash=fingerprint(template.strategy.model_dump(mode='json')),
        configuration_body=lambda binding: {
            'controller': template.model_copy(update={'storage_binding': binding, 'recovery_account_uid': binding.account_uid}).model_dump(mode='json'),
            'recorder_sources': [asdict(event_source)]})
    await t.repo.close()
    cfg = replace(t.cfg, safety_size=2)
    financial = PgSpotRepository(await PgRuntime.open(cfg))
    t.journal.repository = financial
    reader = PgSpotAccountReader(await source(t), max_reads=64, max_bytes=1048576, timeout_ms=1000)
    observer = PgSpotFinancialObserver(reader, independent_fill_value=old_observer.fill_value,
                                       independent_cashflow_value=old_observer.cashflow_value,
                                       max_pages=2, max_rows=64, max_bytes=1048576)
    controller = SimpleNamespace(config=template.model_copy(update={'storage_binding': cfg.binding, 'recovery_account_uid': cfg.binding.account_uid}))
    revoked = []
    reporting = await PgRecorderApplication.install(
        financial, controller=controller, owner=t.owner, sources=(event_source,), revoke=lambda: revoked.append(True),
        local_ready=lambda: True, max_pending=4, max_callbacks=16, shutdown_timeout_s=2, max_rows=64, max_read_bytes=1048576)
    return SimpleNamespace(**locals())


def created(s):
    return BuyOrderCreatedEvent(s.t.journal.clock().timestamp(), OrderType.LIMIT_MAKER, 'LIFE-USDT', D('2'), D('1'),
                                'wire1', s.t.journal.clock().timestamp(), exchange_order_id='123')


def fill(s, **changes):
    event = OrderFilledEvent(s.t.journal.clock().timestamp(), 'wire1', 'LIFE-USDT', TradeType.BUY,
                             OrderType.LIMIT_MAKER, D('1'), D('1'),
                             AddedToCostTradeFee(percent=D('0'), percent_token='USDT', flat_fees=[TokenAmount('USDT', D('.02'))]),
                             exchange_trade_id='trade1', exchange_order_id='123')
    return event._replace(**changes)


async def close(s):
    try:
        try:
            await s.reporting.close()
        except StorageError:
            assert s.revoked
    finally:
        await s.financial.close()
        await s.t.close()


@pytest.mark.asyncio
async def test_actual_connector_callbacks_account_only_from_signed_reads_and_qualify_sdk_dedup(database):
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.event_application import PgSpotEventApplication
    s = await setup(database)
    try:
        app = await PgSpotEventApplication.install(s.reporting, s.observer)
        baseline = await s.financial.snapshot(s.cfg.binding)
        assert baseline.balance.total_quote_usdt == D('10.98')
        assert not s.t.connector._current_trade_fills and not s.t.connector._exchange_order_ids
        epoch = s.financial._runtime._ingress_epoch
        s.t.connector.trigger_event(MarketEvent.BuyOrderCreated, created(s))
        s.t.connector.trigger_event(MarketEvent.OrderFilled, fill(s))
        assert s.financial._runtime._ingress_epoch > epoch
        assert s.financial._runtime._observation_inflight > 0
        assert not s.financial._runtime.healthy and not s.t.connector._current_trade_fills
        await app.drain()
        after = await s.financial.snapshot(s.cfg.binding)
        assert after.balance == baseline.balance and after.budget == baseline.budget
        assert s.t.connector._exchange_order_ids == {'123': 'wire1'}
        assert not s.t.connector.is_confirmed_new_order_filled_event('trade1', '123', 'LIFE-USDT')
        assert s.t.connector.in_flight_orders == {}
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.fills') == 1
        assert await database[0].fetchval('SELECT count(*) FROM life_recorder."TradeFill"') == 1
        assert len(s.t.session.sent) == 1 and not s.revoked
    finally:
        await close(s)


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['amount', 'trade', 'uid', 'wallet', 'wire', 'credentials'])
async def test_untrusted_callback_or_failed_collection_cannot_credit_money_or_poison_sdk_cache(database, fault):
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.event_application import PgSpotEventApplication
    s = await setup(database)
    try:
        app = await PgSpotEventApplication.install(s.reporting, s.observer)
        before = await s.financial.snapshot(s.cfg.binding)
        values = {'amount': D('2')} if fault == 'amount' else {'exchange_trade_id': 'foreign'} if fault == 'trade' else {'order_id': 'foreign'} if fault == 'wire' else {}
        if fault == 'uid':
            s.payloads['/api/v5/account/config'][0]['uid'] = '999'
        elif fault == 'wallet':
            s.payloads['/api/v5/account/balance'][0]['details'][0]['cashBal'] = '1000'
        elif fault == 'credentials':
            s.t.connector.okx_api_key = 'changed-synthetic-key'
        # Native PubSub contains callback exceptions; latches must still revoke.
        s.t.connector.trigger_event(MarketEvent.OrderFilled, fill(s, **values))
        with pytest.raises(StorageError):
            await app.drain()
        assert s.revoked and not s.financial._runtime.healthy
        assert not s.t.connector._current_trade_fills and not s.t.connector._exchange_order_ids
        assert (await s.financial.snapshot(s.cfg.binding)).balance == before.balance
        assert len(s.t.session.sent) == 1
    finally:
        await close(s)


@pytest.mark.asyncio
async def test_startup_uid_failure_removes_listeners_and_grants_no_caches_or_permission(database):
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.event_application import PgSpotEventApplication
    s = await setup(database)
    try:
        s.payloads['/api/v5/account/config'][0]['uid'] = '999'
        with pytest.raises(StorageError, match='PG_ACCOUNT_UID_MISMATCH'):
            await PgSpotEventApplication.install(s.reporting, s.observer)
        app = s.reporting._event_application
        assert app._closed and app._registered == [] and s.revoked
        s.t.connector.trigger_event(MarketEvent.OrderFilled, fill(s))
        assert await database[0].fetchval('SELECT count(*) FROM life_recorder."TradeFill"') == 0
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.fills') == 0
        assert not s.t.connector._current_trade_fills
    finally:
        await close(s)


@pytest.mark.asyncio
async def test_close_removes_actual_listeners_without_restoring_tracking_or_releasing_holds(database):
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.event_application import PgSpotEventApplication
    s = await setup(database)
    try:
        app = await PgSpotEventApplication.install(s.reporting, s.observer)
        before = await s.financial.snapshot(s.cfg.binding)
        await app.close()
        await app.close()
        s.t.connector.trigger_event(MarketEvent.OrderFilled, fill(s))
        await asyncio.sleep(0)
        assert app._registered == [] and not s.financial._runtime.healthy
        assert await database[0].fetchval('SELECT count(*) FROM life_recorder."TradeFill"') == 0
        assert (await s.financial.snapshot(s.cfg.binding)).balance == before.balance
        assert s.t.connector.in_flight_orders == {}
    finally:
        await close(s)


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', (
    'owner', 'controller', 'repository', 'foreign_connector', 'backpressure',
    'source_controller', 'event_repository', 'runtime'))
async def test_callback_context_and_capacity_rejections_revoke_before_native_work(database, fault):
    from uuid import uuid4

    from hummingbot.strategy_v2.life_liquidity.storage.postgres.event_application import PgSpotEventApplication
    s = await setup(database)
    original_runtime = s.financial._runtime
    try:
        app = await PgSpotEventApplication.install(s.reporting, s.observer)
        before = await s.financial.snapshot(s.cfg.binding)
        connector = s.t.connector
        if fault == 'owner':
            s.reporting.owner = replace(s.t.owner, generation=uuid4())
        elif fault == 'controller':
            s.controller.config = s.controller.config.model_copy(update={'manual_kill_switch': True})
        elif fault == 'repository':
            s.reader.repository = object()
        elif fault == 'foreign_connector':
            connector = object()
        elif fault == 'source_controller':
            s.t.journal.controller_id = 'foreign'
        elif fault == 'event_repository':
            s.reporting.events = (s.reporting.events[0], s.reporting.events[0])
        elif fault == 'runtime':
            s.financial._runtime = object()
        else:
            app._callbacks = s.reporting.max_callbacks
        with pytest.raises(StorageError):
            app.capture(MarketEvent.OrderFilled, connector, fill(s))
        s.financial._runtime = original_runtime
        assert s.revoked and not s.financial._runtime.healthy
        assert (await s.financial.snapshot(s.cfg.binding)).balance == before.balance
        assert await database[0].fetchval('SELECT count(*) FROM life_recorder."TradeFill"') == 0
    finally:
        s.financial._runtime = original_runtime
        await close(s)


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['timeout', 'cancel'])
async def test_close_cannot_cancel_owned_observation_or_release_capacity_before_it_finishes(database, monkeypatch, fault):
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.event_application import PgSpotEventApplication
    s = await setup(database)
    entered, release = asyncio.Event(), asyncio.Event()
    waiter = None
    try:
        app = await PgSpotEventApplication.install(s.reporting, s.observer)
        before = await s.financial.snapshot(s.cfg.binding)
        original = s.observer.observe

        async def blocked():
            entered.set()
            await release.wait()
            return await original()
        monkeypatch.setattr(s.observer, 'observe', blocked)
        s.t.connector.trigger_event(MarketEvent.OrderFilled, fill(s))
        await asyncio.wait_for(entered.wait(), 3)
        if fault == 'timeout':
            s.reporting.shutdown_timeout_s = .01  # Explicit shutdown-only fault; no source/financial age changes.
            with pytest.raises(StorageError, match='PG_EVENT_SHUTDOWN_READBACK_REQUIRED'):
                await app.close()
        else:
            waiter = asyncio.create_task(app.close())
            await asyncio.sleep(0)
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter
        assert app._closed and app._registered == []
        assert not app._task.done() and s.financial._runtime._observation_inflight > 0
        assert not s.financial._runtime.healthy and not s.t.connector._current_trade_fills
        assert (await s.financial.snapshot(s.cfg.binding)).balance == before.balance
        release.set()
        await app.drain()
        assert s.financial._runtime._observation_inflight == 0
        assert not s.t.connector._current_trade_fills  # Stop cannot publish SDK IDs late.
        await app.close()
    finally:
        release.set()
        if waiter is not None:
            await asyncio.gather(waiter, return_exceptions=True)
        await close(s)


async def second_fill(s, database):
    ms = int((await database[0].fetchval('SELECT clock_timestamp()')).timestamp() * 1000)
    p = s.payloads
    p['/api/v5/trade/fills-history'].insert(0, dict(p['/api/v5/trade/fills-history'][0], billId='107', tradeId='trade2', ts=str(ms)))
    p['/api/v5/account/bills-archive'][:0] = [
        dict(p['/api/v5/account/bills-archive'][0], billId='107', tradeId='trade2', ts=str(ms)),
        dict(p['/api/v5/account/bills-archive'][1], billId='106', tradeId='trade2', ts=str(ms))]
    p['/api/v5/trade/order'][0].update(accFillSz='2', state='filled')
    p['/api/v5/trade/orders-pending'] = []
    p['/api/v5/account/balance'][0]['details'][0]['cashBal'] = '7'
    p['/api/v5/account/balance'][0]['details'][1]['cashBal'] = '9.96'


@pytest.mark.asyncio
@pytest.mark.parametrize('committed', [False, True])
async def test_actual_backend_loss_in_callback_collection_recovers_second_fill_once_without_sdk_poison_or_resend(database, monkeypatch, committed):
    import asyncpg

    from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.event_application import PgSpotEventApplication
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.observation_account_source import (
        PgObservationAccountSource,
    )
    s = await setup(database)
    admin = await asyncpg.connect(database[1])
    cold = None
    try:
        app = await PgSpotEventApplication.install(s.reporting, s.observer)
        await second_fill(s, database)
        original = s.financial._runtime._commit

        async def lost(tx):
            applied = await tx._connection.fetchval("""SELECT EXISTS(SELECT 1 FROM life_financial.operations WHERE account_id=$1
                AND command->>'kind'='SPOT_FILL' AND (command#>'{command,fills}') @> '[{"trade_id":"trade2"}]'::jsonb)""", s.cfg.account_id)
            if not applied:
                return await original(tx)
            pid = tx._connection.get_server_pid()
            if committed:
                await original(tx)
            assert await admin.fetchval('SELECT pg_terminate_backend($1)', pid) is True
            raise ConnectionError('synthetic callback financial COMMIT reply lost')
        monkeypatch.setattr(s.financial._runtime, '_commit', lost)
        s.t.connector.trigger_event(MarketEvent.OrderFilled, fill(s, exchange_trade_id='trade2'))
        with pytest.raises(CommitUnknown):
            await app.drain()
        assert s.revoked and not s.t.connector._current_trade_fills and not s.t.connector._exchange_order_ids
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.fills') == (2 if committed else 1)
        cold = PgSpotRepository(await PgRuntime.open(s.cfg))
        s.t.journal.repository = cold
        account = await PgObservationAccountSource.create(s.t.journal, connector=s.t.connector,
                                                          max_uid_age_ms=1000, max_auth_age_ms=1000, max_clock_skew_ms=100,
                                                          max_pending=2, collection_timeout_ms=1000)
        reader = PgSpotAccountReader(account, max_reads=64, max_bytes=1048576, timeout_ms=1000)
        observer = PgSpotFinancialObserver(reader, independent_fill_value=s.observer.fill_value,
                                           independent_cashflow_value=s.observer.cashflow_value,
                                           max_pages=2, max_rows=64, max_bytes=1048576)
        await observer.observe()
        repaired = await cold.snapshot(s.cfg.binding)
        assert repaired.balance.total_quote_usdt == D('9.96') and repaired.balance.total_base_life == 7
        assert await observer.observe() == () and (await cold.snapshot(s.cfg.binding)).cut == repaired.cut
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.fills') == 2
        assert repaired.balance.reserved_base_life > 0 and repaired.balance.reserved_quote_usdt > 0
        assert s.t.connector.in_flight_orders == {} and len(s.t.session.sent) == 1
    finally:
        await admin.close()
        if cold is not None:
            await cold.close()
        await close(s)


@pytest.mark.asyncio
async def test_existing_legacy_recorder_listener_cannot_run_alongside_native_pg_authority(database):
    from hummingbot.connector.markets_recorder import MarketsRecorder
    from hummingbot.core.event.event_forwarder import SourceInfoEventForwarder
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.event_application import PgSpotEventApplication
    s = await setup(database)
    # A genuine bound legacy callback on the actual connector. Its SQL manager
    # is deliberately never initialized/invoked; installation must deny first.
    legacy = object.__new__(MarketsRecorder)
    listener = SourceInfoEventForwarder(legacy._did_fill_order)
    s.t.connector.add_listener(MarketEvent.OrderFilled, listener)
    try:
        with pytest.raises(StorageError, match='PG_EVENT_APPLICATION_EXCLUSIVE_LISTENERS_REQUIRED'):
            await PgSpotEventApplication.install(s.reporting, s.observer)
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.fills') == 0
        assert not s.t.connector._current_trade_fills
    finally:
        s.t.connector.remove_listener(MarketEvent.OrderFilled, listener)
        await close(s)
