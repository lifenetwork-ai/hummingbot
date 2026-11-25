import asyncio
import random
from contextlib import nullcontext
from decimal import Decimal
from test.hummingbot.client.config.test_mm_profiles import CATALOGUE
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest

from controllers.market_making.life_mm import LifeMMConfig, LifeMMController
from hummingbot.cli import profile_switch
from hummingbot.cli.service import ServiceHost
from hummingbot.client.config.mm_profiles import MMProfilesConfig
from hummingbot.client.config.runtime_config import RuntimeSettings
from hummingbot.core.data_type.common import PositionAction
from hummingbot.core.data_type.order_book import OrderBook
from hummingbot.core.data_type.order_book_row import OrderBookRow
from hummingbot.core.mm_session import MMSession, MMSessionRequest
from hummingbot.core.spot_outflow_guard import SpotOutflowGuard
from hummingbot.data_feed.market_data_provider import MarketDataProvider
from hummingbot.strategy_v2.utils.quote_guard import QuoteGuard


async def setup_host():
    catalogue = await MMProfilesConfig.load(CATALOGUE)
    session = MMSession.create(catalogue, MMSessionRequest(
        market_mode="both", profile_id="follow_market", duration_seconds=3600), rng=random.Random(1))
    host = ServiceHost(RuntimeSettings.from_environment({"DATABASE_URL": "postgresql://unused/test"}),
                       asyncio.Event(), MagicMock())
    host.mm_session = session
    host.applied_version = 1
    host.owner = MagicMock(generation=1, owner_uuid="owner")
    host.owner.observe_state = AsyncMock(side_effect=host.owner.check_owner)
    host.owner.observe_halt = AsyncMock(return_value=False)
    host.store = MagicMock()
    provider = MagicMock()
    provider.time.return_value = 1000
    provider.ready = True
    provider.get_order_book.side_effect = lambda name, pair: SimpleNamespace(
        snapshot_uid=1000000 if name == "okx_perpetual" else 1000, last_diff_uid=0)
    prices = {"LIFE-USDT": Decimal("0.1"), "BTC-USDT": Decimal("100"), "ETH-USDT": Decimal("10")}
    provider.get_price_by_type.side_effect = lambda name, pair, *args: prices[pair]
    connectors = {name: MagicMock() for name in ("okx", "okx_perpetual")}
    for connector in connectors.values():
        connector.trading_rules = dict.fromkeys(prices, object())
        connector.account_positions = {}
        connector.get_balance.return_value = Decimal("1000")
        connector.get_available_balance.return_value = Decimal("1000")
    provider.get_connector_with_fallback.side_effect = connectors.__getitem__
    configs = catalogue.controller_configs("follow_market", "both", None)
    controllers = {}
    for config in configs:
        config = LifeMMConfig.model_validate({**config.model_dump(), "mm_session_id": session.id, "mm_generation": 0})
        controller = LifeMMController(config, provider, asyncio.Queue())
        controller._account_ready = True
        controller.update_reference()
        controllers[config.id] = controller
    host.hb = SimpleNamespace(trading_core=SimpleNamespace(connector_manager=SimpleNamespace(connectors=connectors), strategy=SimpleNamespace(
        controllers=controllers, market_data_provider=provider)))
    return host, controllers, provider


@pytest.mark.asyncio
async def test_effective_service_config_reuses_runner_and_binds_session_scope():
    host, _, _ = await setup_host()
    config = host.mm_effective_config(host.mm_session)
    assert config["script_file_name"] == "v2_with_controllers.py"
    assert len(config["controller_config_overrides"]) == 2
    for controller in config["controller_config_overrides"]:
        assert controller["mm_session_id"] == host.mm_session.id
        assert controller["mm_generation"] == 0


@pytest.mark.asyncio
async def test_observer_wakes_when_strategy_start_finishes_without_waiting_for_the_next_poll(monkeypatch):
    host, _, _ = await setup_host()
    host.bootstrap = AsyncMock()
    startup = asyncio.get_running_loop().create_future()
    wait = asyncio.wait
    observed = asyncio.Event()

    async def capture(triggers, **kwargs):
        if not observed.is_set():
            assert startup in triggers
            observed.set()
        else:
            assert startup not in triggers
        return await wait(triggers, **kwargs)

    def observe():
        if host.start_task is None:
            host.start_task = startup
        else:
            assert startup.done()
            host.stop.set()

    host.observe_once = AsyncMock(side_effect=observe)
    monkeypatch.setattr(asyncio, 'wait', capture)
    task = asyncio.create_task(host.run())
    try:
        await asyncio.wait_for(observed.wait(), 2)
        startup.set_result(True)
        await asyncio.wait_for(task, 2)
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    assert host.observe_once.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize('stop_while_preparing', [False, True])
async def test_profile_preparation_leaves_current_controller_admitted_until_promotion(stop_while_preparing):
    host, controllers, provider = await setup_host()
    old = host.mm_session
    old.status = 'running'
    host.running = True
    host.applied_mm_identity = (old.id, old.generation)
    host.owner.allow_new_orders = True
    host.owner.order_write_scope.side_effect = nullcontext
    for controller in controllers.values():
        controller.admit_session(old.id, old.generation)
    next_session = MMSession.create(old.catalogue, MMSessionRequest(
        market_mode='both', profile_id='follow_market', duration_seconds=3600), rng=random.Random(2))
    preparing = dict(control_version=1, desired_state='running', database_now=1010,
                     mm_session=old.model_dump(mode='json'), recovery_state={'mm_profile_switch': dict(
                         version=1, session_id=old.id, generation=old.generation, queued_at=1010,
                         target=next_session.model_dump(mode='json', exclude={'catalogue'}))})
    acquired, release = asyncio.Event(), asyncio.Event()

    async def acquire(name, pairs, consumer):
        acquired.set()
        await release.wait()
        return dict.fromkeys(pairs, True)

    provider.acquire_order_books = AsyncMock(side_effect=acquire)
    provider.release_order_books = AsyncMock()
    promoted = {**preparing, 'control_version': 2, 'mm_session': next_session.model_dump(mode='json'), 'recovery_state': {}}
    host.owner.check_owner.return_value = promoted
    host.store.finish_profile_switch.return_value = True
    try:
        assert await host.observe_profile_switch(preparing) is preparing
        await asyncio.wait_for(acquired.wait(), 1)
        assert await host.observe_profile_switch(preparing) is preparing
        assert host.running and host.owner.allow_new_orders
        assert all(controller.session_admitted for controller in controllers.values())
        assert all(not controller.session_admitted for controller in host.profile_switch.controllers)
        host.store.finish_profile_switch.assert_not_called()
        if stop_while_preparing:
            stopped = {**preparing, 'desired_state': 'stopped', 'recovery_state': {}}
            assert await host.observe_profile_switch(stopped) is stopped
            assert host.profile_switch is None
            provider.release_order_books.assert_awaited_once()
            host.store.finish_profile_switch.assert_not_called()
        else:
            release.set()
            await asyncio.wait_for(host.profile_switch.task, 1)
            with patch.object(type(host.profile_switch), 'guards_ready', new_callable=PropertyMock, return_value=False, create=True):
                assert await host.observe_profile_switch(preparing) is preparing
                assert host.running and all(controller.session_admitted for controller in controllers.values())
                host.store.finish_profile_switch.assert_not_called()
            assert await host.observe_profile_switch(preparing) == promoted
            assert host.profile_switch.promoted
            provider.release_order_books.assert_not_awaited()
    finally:
        if host.profile_switch is not None:
            await host.profile_switch.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('stop_while_preparing', [False, True])
async def test_profile_preparation_overlaps_reference_books_volatility_and_guard_sampling(monkeypatch, stop_while_preparing):
    host, _, provider = await setup_host()
    configs = host.mm_effective_config(host.mm_session)['controller_config_overrides']
    for config in configs:
        config['quote_guards'] = dict(enabled=True, mode='strict', max_deviation_bps='200', resume_deviation_bps='100')
        config['spread_policy'] = {'volatility': {'enabled': True}}
    books_started, release_books, books_finished = asyncio.Event(), asyncio.Event(), asyncio.Event()
    indicator_started = asyncio.Event()
    feed = SimpleNamespace(ready=False)

    async def acquire_books(name, pairs, consumer):
        books_started.set()
        try:
            await release_books.wait()
            return dict.fromkeys(pairs, True)
        finally:
            books_finished.set()

    async def warm_indicator():
        indicator_started.set()
        return True

    provider.acquire_order_books = AsyncMock(side_effect=acquire_books)
    provider.release_order_books = AsyncMock()
    provider.acquire_candle_feed.return_value = feed
    monkeypatch.setattr(profile_switch, 'prepare_natr', warm_indicator)
    guard_sampled = asyncio.Event()

    def sample_guard(controller):
        guard_sampled.set()
        return None

    monkeypatch.setattr(LifeMMController, 'sample_profile_guard', sample_guard)
    preparation = profile_switch.ProfileSwitchPreparation(host.mm_session, provider, configs)
    try:
        await asyncio.wait_for(books_started.wait(), 2)
        await asyncio.wait_for(indicator_started.wait(), 2)
        await asyncio.wait_for(guard_sampled.wait(), 2)
        assert provider.acquire_candle_feed.call_count == len(configs)
        assert not books_finished.is_set() and not preparation.ready
        if not stop_while_preparing:
            release_books.set()
            await asyncio.wait_for(books_finished.wait(), 2)
            assert not preparation.task.done()
            feed.ready = True
            await asyncio.wait_for(preparation.task, 2)
            assert preparation.ready
    finally:
        await preparation.close()
    assert books_finished.is_set()
    assert preparation.task.cancelled() is stop_while_preparing
    provider.release_order_books.assert_awaited_once()
    assert provider.release_candle_feed.call_args_list == provider.acquire_candle_feed.call_args_list


@pytest.mark.asyncio
async def test_random_preparation_is_local_until_the_db_step_deadline():
    host, _, _ = await setup_host()
    session = MMSession.create(host.mm_session.catalogue, MMSessionRequest(
        market_mode='both', selection='random', profile_ids=['follow_market', 'sideways'],
        step_duration_seconds=15, duration_seconds=600), rng=SimpleNamespace(choice=lambda pool: 'follow_market'))
    session.ready(now=1000, anchors={name: {'life': '.1', 'sources': {'BTC-USDT': '100', 'ETH-USDT': '10'}}
                                     for name in ('life_spot', 'life_perpetual')})
    host.running = True
    host.applied_mm_identity = (session.id, 0)
    host.prepare_profile_switch = MagicMock()
    chooser = SimpleNamespace(choice=lambda pool: 'sideways')
    row = dict(desired_state='running', control_version=1, recovery_state={}, database_now=1001)
    host.prepare_next_random_profile(row, session, chooser)
    host.prepare_profile_switch.assert_not_called()
    host.prepare_next_random_profile({**row, 'database_now': 1010}, session, chooser)
    target = host.prepare_profile_switch.call_args.args[0]
    assert host.prepare_profile_switch.call_args.kwargs == {'automatic': True}
    assert session.profile_id == 'follow_market' and session.generation == 0 and session.step_expires_at == 1015
    assert target.profile_id == 'sideways' and target.generation == 1 and target.step_expires_at == 1030
    assert not target.permits(session_id=target.id, generation=target.generation, now=1015)
    prepared = SimpleNamespace(automatic=True, ready=True, identity=(target.id, target.generation), session=target)
    reroll = SimpleNamespace(choice=lambda pool: 'follow_market')
    at_cutover = session.model_copy(deep=True)
    host.advance_profile(at_cutover, 1015, reroll, prepared)
    assert at_cutover.profile_id == 'sideways' and at_cutover.generation == 1
    missed = session.model_copy(deep=True)
    host.advance_profile(missed, 1031, reroll, prepared)
    assert missed.profile_id == 'follow_market' and missed.step_index == 2


@pytest.mark.asyncio
@pytest.mark.parametrize('concurrent_control', [None, 'stop', 'new-version', 'pending-mutation'])
async def test_random_cutover_keeps_the_feed_when_next_profile_preparation_is_still_running(monkeypatch, concurrent_control):
    host, _, provider = await setup_host()
    session = MMSession.create(host.mm_session.catalogue, MMSessionRequest(
        market_mode='both', selection='random', profile_ids=['follow_market', 'sideways'],
        step_duration_seconds=15, duration_seconds=600), rng=SimpleNamespace(choice=lambda pool: 'follow_market'))
    session.ready(now=1000, anchors={name: {'life': '.1', 'sources': {'BTC-USDT': '100', 'ETH-USDT': '10'}}
                                     for name in ('life_spot', 'life_perpetual')})
    host.mm_session = session
    host.applied_mm_identity = session.id, session.generation
    host.running = host.owner.allow_new_orders = True
    host.phase = 'running'
    host.mm_startup_pending = MagicMock(return_value=False)
    monkeypatch.setattr('hummingbot.core.order_submission_guard.install_guard', MagicMock())

    async def acquire(*args):
        await asyncio.Event().wait()

    provider.acquire_order_books = AsyncMock(side_effect=acquire)
    provider.release_order_books = AsyncMock()
    row = dict(config_type='mm-session', desired_state='running', control_version=1, recovery_state={},
               database_now=1010, mm_session=session.model_dump(mode='json'))
    host.prepare_next_random_profile(row, session, SimpleNamespace(choice=lambda pool: 'sideways'))
    preparation = host.profile_switch
    assert not preparation.ready
    next_session = session.model_copy(deep=True)
    expired = {**row, 'database_now': 1015}

    async def advance(**kwargs):
        kwargs['change'](next_session, 1015)
        return next_session

    async def observe():
        result = {**expired, 'mm_session': next_session.model_dump(mode='json')}
        if next_session.generation != session.generation:
            if concurrent_control == 'stop':
                result['desired_state'] = 'stopped'
            elif concurrent_control == 'new-version':
                result['control_version'] += 1
            elif concurrent_control == 'pending-mutation':
                result['recovery_state'] = {'pending_mutations': {'operation': {}}}
        return result

    host.owner.observe_state = AsyncMock(side_effect=observe)
    host._update_mm_session = AsyncMock(side_effect=advance)
    host.stop_core = AsyncMock(return_value=False)
    try:
        with patch('random.SystemRandom', return_value=SimpleNamespace(choice=lambda pool: 'follow_market')):
            with pytest.raises(RuntimeError, match='Service cleanup did not complete'):
                await host.observe_once()
        assert next_session.profile_id == 'sideways'
        assert not session.permits(session_id=session.id, generation=session.generation, now=1015)
        assert not next_session.permits(session_id=next_session.id, generation=next_session.generation, now=1015)
        if concurrent_control is None:
            host.stop_core.assert_awaited_once_with(keep_market_data=True)
        else:
            host.stop_core.assert_awaited_once_with()
    finally:
        await preparation.close()


@pytest.mark.asyncio
async def test_hot_random_repeat_extends_the_window_without_replacing_quote_authority():
    host, _, _ = await setup_host()
    draw = MagicMock(return_value='follow_market')
    chooser = SimpleNamespace(choice=draw)
    session = MMSession.create(host.mm_session.catalogue, MMSessionRequest(
        market_mode='both', selection='random', profile_ids=['follow_market', 'sideways'],
        step_duration_seconds=15, duration_seconds=600), rng=chooser)
    anchors = {key: {'life': '.1', 'sources': {'BTC-USDT': '100', 'ETH-USDT': '10'}}
               for key in ('life_spot', 'life_perpetual')}
    session.ready(now=1000, anchors=anchors)
    host.running = host.owner.allow_new_orders = True
    host.phase = 'running'
    host.applied_mm_identity = (session.id, 0)
    host.prepare_profile_switch = MagicMock()
    row = dict(desired_state='running', control_version=1, recovery_state={}, database_now=1010)
    draw.reset_mock()
    host.prepare_next_random_profile(row, session, chooser)
    host.prepare_next_random_profile({**row, 'database_now': 1011}, session, chooser)
    host.prepare_profile_switch.assert_not_called()
    draw.assert_called_once()

    # The selected repeat survives until cutover without another draw.
    at_cutover = session.model_copy(deep=True)
    host.advance_profile(at_cutover, 1015, None, None, repeat=host.random_repeat)
    assert (at_cutover.step_index, at_cutover.generation, at_cutover.status) == (1, 0, 'running')
    assert at_cutover.anchors == anchors
    assert at_cutover.step_expires_at == 1030
    assert at_cutover.permits(session_id=session.id, generation=0, now=1015)
    assert not at_cutover.permits(session_id=session.id, generation=0, now=1030)

    # A missed window still requires a fresh generation even if the draw repeats.
    missed = session.model_copy(deep=True)
    host.advance_profile(missed, 1031, chooser, None, repeat=host.random_repeat)
    assert (missed.step_index, missed.generation, missed.status) == (2, 1, 'preparing')
    assert missed.anchors == {}


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', [None, 'stale-reference', 'missing-reference', 'stale-sample', 'replaced-book'])
async def test_hot_switch_can_reuse_strict_guard_samples_from_the_target_profile(monkeypatch, failure):
    _, controllers, provider = await setup_host()
    old = controllers["life_spot"]
    cfg = LifeMMConfig.model_validate({**old.config.model_dump(), 'mm_generation': 1,
                                       'quote_guards': dict(enabled=True, max_deviation_bps="200",
                                                            resume_deviation_bps="100", recovery_delay_seconds="2")})
    preview, target = (LifeMMController(cfg, provider, asyncio.Queue()) for _ in range(2))
    connector = provider.get_connector_with_fallback('okx')
    connector.name = 'okx'
    connector.in_flight_orders = {}
    connector._order_tracker = SimpleNamespace(cached_orders={}, lost_orders={})
    connector.trading_rules['LIFE-USDT'] = SimpleNamespace(min_order_size=Decimal('.01'), min_base_amount_increment=Decimal('.01'))
    now = [1000, 50]
    monkeypatch.setattr('controllers.market_making.life_mm.time', SimpleNamespace(monotonic=lambda: now[1]))
    provider.time.side_effect = lambda: now[0]
    book = OrderBook()
    reference_age = [0]
    reference_available = [True]

    def get_book(name, pair):
        if pair == 'LIFE-USDT':
            return book
        if not reference_available[0]:
            raise KeyError(pair)
        return SimpleNamespace(snapshot_uid=now[0] - reference_age[0], last_diff_uid=0)

    provider.get_order_book.side_effect = get_book

    def publish(book):
        book.apply_snapshot([OrderBookRow(.099, 1000, now[0])], [OrderBookRow(.101, 1000, now[0])], now[0])

    publish(book)
    preview.sample_profile_guard()
    assert preview._quote_guard.blocked
    now[:] = [1002, 52]
    preview.sample_profile_guard()  # Re-reading the same book does not count.
    assert preview._quote_guard.blocked
    publish(book)
    witness = preview.sample_profile_guard()
    assert not preview._quote_guard.blocked and not preview.session_admitted
    if failure == 'stale-reference':
        reference_age[0] = 60
    elif failure == 'missing-reference':
        reference_available[0] = False
    elif failure == 'stale-sample':
        now[1] += 2
    elif failure == 'replaced-book':
        book = OrderBook()
        publish(book)
    target.use_profile_guard(preview, witness)
    target.update_reference()
    assert target.processed_data['ready'] is (failure is None)
    assert not target.session_admitted


@pytest.mark.asyncio
async def test_mm_startup_prefetches_public_spot_references_during_reconciliation():
    host, controllers, _ = await setup_host()
    core = host.hb.trading_core
    controller = controllers['life_spot']
    assert controller.config.reference_connector == controller.config.connector_name == 'okx'
    provider = MarketDataProvider(core.connector_manager.connectors)
    controller.market_data_provider = provider
    reference_started = asyncio.Event()
    financial_proof_finished = asyncio.Event()

    async def initialize(name, pair):
        assert name == 'okx'
        reference_started.set()
        await financial_proof_finished.wait()
        return True

    async def reconcile(engine):
        assert engine is core
        assert not controller.session_admitted
        await asyncio.wait_for(reference_started.wait(), 1)
        financial_proof_finished.set()

    provider.initialize_order_book = AsyncMock(side_effect=initialize)
    provider.remove_order_book = AsyncMock(return_value=True)
    provider.acquire_book_recovery = MagicMock()
    provider.release_book_recovery = MagicMock()
    host.owner.reconcile = AsyncMock(side_effect=reconcile)

    await host._reconcile_mm_startup(core)
    try:
        assert financial_proof_finished.is_set()
        assert not controller.session_admitted
        expected = [(controller.config.reference_connector, ref.pair) for ref in controller.config.references]
        assert [call.args for call in provider.initialize_order_book.await_args_list] == expected
        await controller.on_start()
        assert [call.args for call in provider.initialize_order_book.await_args_list] == expected
        assert controller._quote_guard.blocked and not controller.session_admitted
    finally:
        await controller.on_stop()


@pytest.mark.asyncio
async def test_cancelled_mm_reconciliation_drains_public_reference_prefetch():
    host, controllers, provider = await setup_host()
    provider.connectors = host.hb.trading_core.connector_manager.connectors
    entered, drained = asyncio.Event(), asyncio.Event()

    async def acquire(*args):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            drained.set()

    async def reconcile(core):
        await entered.wait()
        await asyncio.Event().wait()

    provider.acquire_order_books = AsyncMock(side_effect=acquire)
    provider.release_order_books = AsyncMock()
    host.owner.reconcile = AsyncMock(side_effect=reconcile)
    task = asyncio.create_task(host._reconcile_mm_startup(host.hb.trading_core))
    await asyncio.wait_for(entered.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert drained.is_set()
    controller = controllers['life_spot']
    provider.release_order_books.assert_awaited_once_with(
        'okx', [ref.pair for ref in controller.config.references], controller)


@pytest.mark.asyncio
async def test_failed_mm_reconciliation_releases_completed_public_reference_prefetch(monkeypatch):
    host, controllers, provider = await setup_host()
    core = host.hb.trading_core
    provider.connectors = core.connector_manager.connectors
    controller = controllers['life_spot']
    references = [ref.pair for ref in controller.config.references]
    provider.acquire_order_books = AsyncMock(return_value=dict.fromkeys(references, True))
    provider.release_order_books = AsyncMock()
    acquired = asyncio.Event()
    prefetch = controller.prefetch_reference_books

    async def warm():
        result = await prefetch()
        acquired.set()
        return result

    async def reject(core):
        await asyncio.wait_for(acquired.wait(), 1)
        raise OSError('test reconciliation failed')

    monkeypatch.setattr(controller, 'prefetch_reference_books', warm)
    host.owner.reconcile = AsyncMock(side_effect=reject)
    with pytest.raises(OSError, match='test reconciliation failed'):
        await host._reconcile_mm_startup(core)
    assert not controller.session_admitted
    provider.release_order_books.assert_awaited_once_with('okx', references, controller)


@pytest.mark.asyncio
async def test_drawdown_thresholds_are_wired_to_existing_runner_without_an_inactive_controller():
    host, _, _ = await setup_host()
    data = host.mm_session.catalogue.model_dump(mode="json")
    data.update(max_global_drawdown_quote="10", max_controller_drawdown_quote="5")
    host.mm_session.catalogue = MMProfilesConfig.model_validate(data)
    host.mm_session.request.market_mode = "spot"
    effective = host.mm_effective_config(host.mm_session)
    assert effective["max_global_drawdown_quote"] == "10"
    assert effective["max_controller_drawdown_quote"] == "5"
    assert len(effective["controller_config_overrides"]) == 1
    assert set(effective["mm_drawdown_scope"]) == {"life_spot", "life_perpetual"}


@pytest.mark.asyncio
async def test_outflow_policy_survives_perpetual_selection_without_a_global_buy_cap_gate():
    host, _, _ = await setup_host()
    data = host.mm_session.catalogue.model_dump(mode="json")
    data["spot_outflow"] = dict(window_seconds=60, max_net_outflow_quote="20", fee_allowance_rate=".001")
    host.mm_session.catalogue = MMProfilesConfig.model_validate(data)
    host.mm_session.request.market_mode = "perpetual"
    effective = host.mm_effective_config(host.mm_session)
    assert effective["mm_spot_outflow_policy"]["trading_pair"] == "LIFE-USDT"
    assert len(effective["controller_config_overrides"]) == 1
    # Scope of this limit is BUY authorization, not shared MM readiness.
    host.mm_session.request.market_mode = "both"
    host.owner.spot_outflow = SimpleNamespace(status=lambda: dict(ready=False, remaining_quote=0))
    assert host.mm_runtime_ready()


@pytest.mark.asyncio
async def test_spot_session_waits_for_verified_outflow_binding_before_creating_quotes():
    host, controllers, _ = await setup_host()
    owner = host.owner
    owner.connectors = host.hb.trading_core.connector_manager.connectors
    for connector in owner.connectors.values():
        connector.account_uid = "fixture"
        connector.trading_environment = "demo"
    spot = owner.connectors["okx"]
    spot.account_uid = None
    guard = owner.spot_outflow = SpotOutflowGuard(owner, dict(
        trading_pair="LIFE-USDT", window_seconds=60, max_net_outflow_quote="20", fee_allowance_rate=".001"))

    def persist(**kwargs):
        kwargs["change"](host.mm_session, 1000)
        return host.mm_session

    host.store.update_mm_session.side_effect = persist
    assert not await host.admit_mm_session()
    assert host.mm_readiness_reason == "spot_outflow_identity_unavailable"
    spot.account_uid = "fixture"
    assert not await host.admit_mm_session()
    assert host.mm_readiness_reason == "spot_outflow_initializing"
    host.store.update_mm_session.assert_not_called()
    assert all(not controller.session_admitted for controller in controllers.values())

    # Once bound, a missing BUY projection still permits admission for SELLs.
    guard.binding = guard.identity()
    assert not guard.status()["buy_allowed"]
    assert await host.admit_mm_session()
    assert all(controller.session_admitted for controller in controllers.values())


@pytest.mark.asyncio
async def test_both_runtime_gate_rejects_one_stale_market():
    host, controllers, provider = await setup_host()
    assert host.mm_runtime_ready()
    provider.get_order_book.side_effect = lambda name, pair: SimpleNamespace(
        snapshot_uid=980000 if name == "okx_perpetual" else 1000, last_diff_uid=0)
    assert not host.mm_runtime_ready()
    assert not controllers["life_perpetual"].processed_data["ready"]


@pytest.mark.asyncio
async def test_one_market_guard_failure_blocks_both_and_readiness_does_not_advance_recovery(monkeypatch):
    host, controllers, provider = await setup_host()
    controller = controllers["life_perpetual"]
    config = controller.config.model_dump()
    config["quote_guards"] = dict(enabled=True, max_deviation_bps="200", resume_deviation_bps="100", recovery_delay_seconds="2")
    controller.config = LifeMMConfig.model_validate(config)
    controller._quote_guard = QuoteGuard(controller.config.quote_guards)
    connector = provider.get_connector_with_fallback("okx_perpetual")
    connector.name = "okx_perpetual"
    connector.in_flight_orders = {}
    connector._order_tracker = SimpleNamespace(cached_orders={}, lost_orders={})
    connector.trading_rules["LIFE-USDT"] = SimpleNamespace(min_order_size=Decimal(".01"), min_base_amount_increment=Decimal(".01"))
    guarded_book = OrderBook()
    original = provider.get_order_book.side_effect
    provider.get_order_book.side_effect = lambda name, pair: guarded_book if name == "okx_perpetual" else original(name, pair)
    tick = [0]
    monkeypatch.setattr("controllers.market_making.life_mm.time.monotonic", lambda: tick[0])

    def update(price, timestamp):
        provider.time.return_value = timestamp
        guarded_book.apply_snapshot([OrderBookRow(float(price - Decimal(".001")), 1000, timestamp * 1000)],
                                    [OrderBookRow(float(price + Decimal(".001")), 1000, timestamp * 1000)], timestamp * 1000)
    update(Decimal(".1"), 1000)
    await controller.update_processed_data()
    tick[0] = 2
    update(Decimal(".1"), 1002)
    await controller.update_processed_data()
    assert controller.processed_data["ready"]
    # Restore fresh spot/reference fixture timestamps while testing the guarded market.
    provider.get_order_book.side_effect = lambda name, pair: guarded_book if name == "okx_perpetual" else SimpleNamespace(snapshot_uid=provider.time(), last_diff_uid=0)
    assert host.mm_runtime_ready()
    anchors = controller.reference_state()
    update(Decimal(".12"), 1003)
    assert not host.mm_runtime_ready()
    assert host.mm_readiness_reason == "reference_deviation"
    assert controllers["life_spot"].processed_data["ready"]
    update(Decimal(".1"), 1004)
    for _ in range(50):
        tick[0] = 100
        assert not host.mm_runtime_ready()
    assert controller.reference_state() == anchors


@pytest.mark.asyncio
async def test_collateral_readiness_wait_reports_its_actual_reason():
    host, controllers, _ = await setup_host()
    host.phase = "waiting_for_mm_readiness"
    config = next(iter(controllers.values())).config
    controllers[config.id].executors_info = [SimpleNamespace(is_active=True, is_trading=False,
                                                             config=SimpleNamespace(amount=Decimal("1"), entry_price=Decimal("1"),
                                                                                    leverage=1, connector_name="okx", side=SimpleNamespace(name="BUY")))]
    host._mm_available_quote = lambda items: Decimal("0")
    assert not host.mm_runtime_ready()
    assert host.service_status()["reason_code"] == "insufficient_collateral"
    host.owner.require_recovery.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["position_checkpoint_pending", "position_checkpoint_waiting"])
async def test_position_checkpoint_pauses_order_admission_without_drawdown_policy(reason):
    host, controllers, _ = await setup_host()
    assert host.mm_session.catalogue.drawdown_policy() is None
    config = next(iter(controllers.values())).config
    connector = SimpleNamespace(name=config.connector_name)
    order = SimpleNamespace(trading_pair=config.trading_pair, position=PositionAction.OPEN)
    strategy = host.hb.trading_core.strategy

    assert host.mm_runtime_order_ready(connector, order)
    strategy.controller_reports_error = reason
    assert not host.mm_runtime_order_ready(connector, order)
    assert host.owner.mm_order_readiness_reason == reason
    host.owner.mm_order_readiness_reason = None
    assert not host.mm_runtime_ready()
    assert host.owner.mm_order_readiness_reason == reason
    host.owner.require_recovery.assert_not_called()

    strategy.controller_reports_error = None
    assert host.mm_runtime_order_ready(connector, order)
    assert host.owner.mm_order_readiness_reason is None


@pytest.mark.asyncio
async def test_admission_persists_joint_anchors_before_allowing_quotes():
    host, controllers, _ = await setup_host()

    def persist(**kwargs):
        assert all(not controller._session_admitted for controller in controllers.values())
        kwargs["change"](host.mm_session, 1000)
        return host.mm_session

    host.store.update_mm_session.side_effect = persist
    assert await host.admit_mm_session()
    assert host.mm_session.started_at == 1000
    assert host.mm_session.expires_at == 4600
    assert host.mm_session.anchors["life_spot"]["sources"] == host.mm_session.anchors["life_perpetual"]["sources"]
    assert all(controller._session_admitted for controller in controllers.values())


@pytest.mark.asyncio
async def test_failed_db_admission_keeps_all_quotes_blocked():
    host, controllers, _ = await setup_host()
    host.store.update_mm_session.side_effect = RuntimeError("database unavailable")
    with pytest.raises(RuntimeError):
        await host.admit_mm_session()
    assert all(not controller._session_admitted for controller in controllers.values())


def running_row(host, controllers, now):
    host.mm_session.ready(now=1000, anchors={key: controller.reference_state() for key, controller in controllers.items()})
    host.applied_mm_identity = (host.mm_session.id, host.mm_session.generation)
    host.owner.try_acquire.return_value = True
    host.owner.allow_new_orders = True
    host.snapshot = lambda: {}
    host.validate_selection = AsyncMock()
    host.poll_reconciliation = AsyncMock()
    host.poll_funding = MagicMock()
    row = dict(config_type="mm-session", config_file_path="life_mm_profiles.yaml", control_version=1,
               desired_state="running", recovery_state={}, database_now=now,
               mm_session=host.mm_session.model_dump(mode="json"))
    host.owner.check_owner.side_effect = lambda: row
    host.hb.trading_core.trade_fill_db = SimpleNamespace(persistence_healthy=True)
    host.hb.trading_core._strategy_running = True
    return row


@pytest.mark.asyncio
async def test_observer_expires_session_even_when_feed_unready_without_restarting_core():
    host, controllers, provider = await setup_host()
    row = running_row(host, controllers, now=4600)
    host.running = True
    provider.ready = False

    def persist(**kwargs):
        session = MMSession.model_validate(row["mm_session"])
        kwargs["change"](session, row["database_now"])
        row["mm_session"] = session.model_dump(mode="json")
        if session.status == "expired":
            row["desired_state"] = "stopped"
        return session

    async def stop():
        host.running = False
        return True

    host.store.update_mm_session.side_effect = persist
    host.stop_core = AsyncMock(side_effect=stop)
    host.start_strategy = AsyncMock()
    await host.observe_once()
    assert row["mm_session"]["status"] == "expired"
    assert row["control_version"] == 1
    host.stop_core.assert_awaited_once()
    host.start_strategy.assert_not_awaited()


@pytest.mark.asyncio
async def test_observer_cancels_both_on_one_stale_feed_and_preserves_session_deadline():
    host, controllers, provider = await setup_host()
    row = running_row(host, controllers, now=1001)
    host.running = True
    for key, controller in controllers.items():
        controller._session_admitted = True
        controller.executors_info = [SimpleNamespace(id=key + "-open", is_active=True, is_trading=True)]
    provider.get_order_book.side_effect = lambda name, pair: SimpleNamespace(
        snapshot_uid=980000 if name == "okx_perpetual" else 1000, last_diff_uid=0)
    await host.observe_once()
    assert host.phase == "waiting_for_mm_readiness"
    for controller in controllers.values():
        assert not controller._session_admitted
        assert controller.actions_queue.get_nowait()[0].keep_position
    assert row["mm_session"]["expires_at"] == 4600
    host.store.update_mm_session.assert_not_called()


@pytest.mark.asyncio
async def test_mm_start_uses_the_existing_trading_core_and_runner():
    host, _, _ = await setup_host()
    host.hb.trading_core.start_strategy = AsyncMock(return_value=True)
    row = {"config_type": "mm-session", "config_file_path": "life_mm_profiles.yaml",
           "mm_session": host.mm_session.model_dump(mode="json")}
    assert await host.start_strategy(row)
    args = host.hb.trading_core.start_strategy.call_args.args
    assert args[0] == "v2_with_controllers"
    assert args[2] == "life_mm_profiles.yaml"
    assert len(args[1]["controller_config_overrides"]) == 2
