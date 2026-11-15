"""Cold indicator preparation must not block the trading loop or seed prices."""
import asyncio
import builtins
import threading
import time
from contextvars import ContextVar
from test.hummingbot.strategy_v2.controllers.test_life_mm import configuration, make_controller
from test.hummingbot.strategy_v2.controllers.test_life_mm_spread import dynamic, feed

import pytest

from hummingbot.connector.trading_rule import TradingRule
from hummingbot.core.engine_diagnostics import EngineDiagnostics
from hummingbot.strategy_v2.utils import mm_spread


@pytest.fixture(autouse=True)
def isolate_preparation(monkeypatch):
    monkeypatch.setattr(mm_spread, '_NATR_PREPARATION', None, raising=False)


def controller(*, managed=False, **volatility):
    config = configuration(spread_policy=dynamic(**volatility).model_dump(), runtime_config_managed=managed,
                           mm_session_id='indicator-session' if managed else None, mm_generation=1 if managed else None)
    result, _ = make_controller(config)
    provider = result.market_data_provider
    provider.time.return_value = 1200
    provider.get_order_book.side_effect = lambda *args: type('Book', (), dict(snapshot_uid=1200, last_diff_uid=1200))()
    provider._generate_candle_feed_key.return_value = 'volatility'
    provider.candles_feeds = {'volatility': feed()}
    connector = provider.get_connector_with_fallback.return_value
    connector.trading_rules = {'LIFE-USDT': TradingRule('LIFE-USDT')}
    connector.quantize_order_price.side_effect = lambda pair, price: price
    connector.quantize_order_amount.side_effect = lambda pair, amount, **kwargs: amount
    return result


@pytest.mark.asyncio
async def test_native_startup_moves_cold_natr_off_loop_and_discards_warmup_value(monkeypatch):
    actor = controller()
    loop_thread = threading.get_ident()
    original = builtins.__import__
    cold_threads, progress_during_import = [], []
    progress = asyncio.Event()
    handle = asyncio.get_running_loop().call_later(.05, progress.set)

    def cold_import(name, *args, **kwargs):
        if name == 'pandas_ta' and not cold_threads:
            cold_threads.append(threading.get_ident())
            time.sleep(.35)  # Controlled first-use dependency wait, not a Cloud timing claim.
            progress_during_import.append(progress.is_set())
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, '__import__', cold_import)
    try:
        await actor.on_start()
        # Preparation is not a candle, a cached volatility observation or permission to quote.
        assert actor._spread_policy.key is None and actor._spread_policy.raw_natr is None
        await actor.update_processed_data()
        assert actor.processed_data['ready'], actor.processed_data
        assert actor._spread_policy.raw_natr is not None
        assert cold_threads and all(identity != loop_thread for identity in cold_threads)
        assert progress_during_import == [True]
    finally:
        handle.cancel()
        await actor.on_stop()


@pytest.mark.asyncio
async def test_many_startups_share_one_pure_preparation_and_keep_separate_observations(monkeypatch):
    actors = [controller() for _ in range(8)]
    original = mm_spread.natr_value
    calls = []

    def counted(frame, length):
        calls.append(threading.get_ident())
        return original(frame, length)

    monkeypatch.setattr(mm_spread, 'natr_value', counted)
    try:
        await asyncio.gather(*(actor.on_start() for actor in actors))
        assert len(calls) == 1 and calls[0] != threading.get_ident()
        assert all(actor._spread_policy.key is None and actor._spread_policy.raw_natr is None for actor in actors)
        await actors[0].update_processed_data()
        assert actors[0]._spread_policy.raw_natr is not None
        assert all(actor._spread_policy.raw_natr is None for actor in actors[1:])
    finally:
        await asyncio.gather(*(actor.on_stop() for actor in actors))


@pytest.mark.asyncio
async def test_canceling_one_startup_cannot_cancel_shared_preparation_or_publish_for_retired_controller(monkeypatch):
    first, second = controller(), controller()
    entered, release = threading.Event(), threading.Event()
    original = mm_spread.natr_value

    def held(frame, length):
        entered.set()
        assert release.wait(2)
        return original(frame, length)

    monkeypatch.setattr(mm_spread, 'natr_value', held)
    tasks = [asyncio.create_task(first.on_start()), asyncio.create_task(second.on_start())]
    try:
        async def started():
            while not entered.is_set():
                await asyncio.sleep(.001)
        await asyncio.wait_for(started(), .5)
        tasks[0].cancel()
        with pytest.raises(asyncio.CancelledError):
            await tasks[0]
        assert not tasks[1].done()
        release.set()
        await asyncio.wait_for(tasks[1], 1)
        assert first._startup_stage != 'ready' and second._startup_stage == 'ready'
        assert first._spread_policy.key is None and first.actions_queue.empty()
        assert second._spread_policy.key is None
    finally:
        release.set()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.gather(first.on_stop(), second.on_stop())


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['wait', 'static'])
async def test_preparation_failure_uses_declared_fallback_and_retries_off_loop(monkeypatch, mode):
    values = dict(on_unavailable=mode)
    if mode == 'static':
        values.update(fallback_buy_spreads=['.01'], fallback_sell_spreads=['.01'])
    actor = controller(**values)
    original = mm_spread.natr_value
    threads = []

    def unavailable(*args):
        threads.append(threading.get_ident())
        raise ImportError('offline unavailable indicator')

    monkeypatch.setattr(mm_spread, 'natr_value', unavailable)
    try:
        await actor.on_start()
        await actor.update_processed_data()
        assert threads and all(identity != threading.get_ident() for identity in threads)
        assert actor.processed_data['ready'] is (mode == 'static')
        assert actor._spread_policy.key is None and actor._spread_policy.raw_natr is None
        monkeypatch.setattr(mm_spread, 'natr_value', original)

        async def recovered():
            while actor._spread_policy.raw_natr is None:
                await asyncio.sleep(.01)
                await actor.update_processed_data()
        await asyncio.wait_for(recovered(), 1)
        assert actor.processed_data['ready'] and actor._spread_policy.raw_natr is not None
    finally:
        await actor.on_stop()


@pytest.mark.asyncio
@pytest.mark.parametrize('fee_floor', [False, True])
async def test_disabled_volatility_never_prepares_an_indicator(monkeypatch, fee_floor):
    actor, _ = make_controller(configuration(spread_policy=dict(fee_floor=fee_floor)))

    def forbidden(*args):
        pytest.fail('Disabled volatility must not prepare NATR')

    monkeypatch.setattr(mm_spread, 'natr_value', forbidden)
    try:
        await actor.on_start()
        assert mm_spread._NATR_PREPARATION is None
    finally:
        await actor.on_stop()


@pytest.mark.asyncio
async def test_pending_preparation_after_startup_deadline_cannot_postpone_freshness_checks(monkeypatch):
    actor = controller(on_unavailable='static', fallback_buy_spreads=['.01'], fallback_sell_spreads=['.01'])
    actor.STARTUP_OPERATION_TIMEOUT = .15  # Test-only startup deadline; no production budget changes.
    release = threading.Event()
    original = mm_spread.natr_value
    calls = []

    def held(frame, length):
        calls.append(threading.get_ident())
        assert release.wait(2)
        return original(frame, length)

    monkeypatch.setattr(mm_spread, 'natr_value', held)
    try:
        await actor.on_start()
        assert mm_spread._NATR_PREPARATION is not None and not mm_spread._NATR_PREPARATION.done()
        await asyncio.wait_for(actor.update_processed_data(), .05)
        assert actor.processed_data['ready']  # Only the configured static fallback supplies spreads.
        actor.market_data_provider.time.return_value = 1300
        await asyncio.wait_for(actor.update_processed_data(), .05)
        assert not actor.processed_data['ready']
        assert actor._spread_policy.key is None and actor.actions_queue.empty()
        assert len(calls) == 1  # A timed-out waiter must not create another worker.
    finally:
        release.set()
        if mm_spread._NATR_PREPARATION is not None:
            await asyncio.wait_for(asyncio.shield(mm_spread._NATR_PREPARATION), 1)
        await actor.on_stop()


@pytest.mark.asyncio
async def test_native_diagnostics_separate_indicator_data_proposals_and_queue_publication():
    actor = controller()
    tracer = EngineDiagnostics(emit=lambda record: None)
    await tracer.start()
    try:
        await actor.on_start()
        actor.executors_update_event.set()
        await actor.control_task()
        assert actor.processed_data['ready'], actor.processed_data
        actions = actor.actions_queue.get_nowait()
        assert len(actions) == 2  # Native proposals only; no fake venue acknowledgement.
        operations = tracer.snapshot()['operations']
        for operation in ('controller.indicator', 'controller.reference', 'controller.proposals', 'controller.actions'):
            assert operations[operation]['completed'] >= 1
            assert operations[operation]['errors'] == 0
    finally:
        await tracer.close()
        await actor.on_stop()


@pytest.mark.asyncio
async def test_pure_indicator_worker_does_not_inherit_callers_execution_context(monkeypatch):
    actor = controller()
    authority = ContextVar('offline_execution_scope', default=None)
    token = authority.set('previous-executor-scope')
    original = mm_spread.natr_value
    observed = []

    def inspected(frame, length):
        observed.append(authority.get())
        return original(frame, length)

    monkeypatch.setattr(mm_spread, 'natr_value', inspected)
    try:
        await actor.on_start()
        assert observed == [None]
        assert authority.get() == 'previous-executor-scope'
        assert actor._spread_policy.raw_natr is None
    finally:
        authority.reset(token)
        await actor.on_stop()


@pytest.mark.asyncio
async def test_session_revoked_during_preparation_cannot_publish_quotes_after_worker_finishes(monkeypatch):
    actor = controller(managed=True)
    actor.admit_session('indicator-session', 1)
    entered, release = threading.Event(), threading.Event()
    original = mm_spread.natr_value

    def held(frame, length):
        entered.set()
        assert release.wait(2)
        return original(frame, length)

    monkeypatch.setattr(mm_spread, 'natr_value', held)
    task = asyncio.create_task(actor.on_start())
    try:
        async def started():
            while not entered.is_set():
                await asyncio.sleep(.001)
        await asyncio.wait_for(started(), .5)
        actor.revoke_session()
        release.set()
        await asyncio.wait_for(task, 1)
        actor.executors_update_event.set()
        await actor.control_task()
        assert actor._spread_policy.raw_natr is not None  # Real candles are available after preparation.
        assert not actor.session_admitted and actor.actions_queue.empty()
    finally:
        release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await actor.on_stop()
