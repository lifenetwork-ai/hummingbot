"""Native final market reads and original SDK handoff; fake HTTP only.

Capital/carry and final market observations use the original signed sources.
Intent prices and economic costs remain explicit, not a configured planner.
"""
import asyncio
import json
from copy import copy
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal as D
from test.hummingbot.core.web_assistant.connections.test_request_guard import Response
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_spot_market import market
from types import SimpleNamespace

import pytest

from hummingbot.core.data_type.common import OrderType, TradeType
from hummingbot.core.data_type.in_flight_order import OrderState
from hummingbot.strategy_v2.executors.order_executor.order_executor import OrderExecutor
from hummingbot.strategy_v2.life_liquidity.storage.actions import RunnerActionFacts
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
from hummingbot.strategy_v2.life_liquidity.storage.postgres.quote_account_source import PgQuoteAccountSource
from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot_application import PgSpotQuoteApplication
from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot_intent import PgSpotQuoteIntent


async def application(database, *, maker=D('.0005'), side='BUY', objective='profit_mm', price=None, subsidy=D('0'), shared_quota=False):
    t = await market(database, clean=True, objective=objective, shared_quota=shared_quota)
    price = price if price is not None else D('.97') if side == 'BUY' else D('1.03')
    # Synthetic SPOT fee schedule: both possible fee currencies fit the
    # existing .001 shared fee allocation. SWAP terms/budgets are unchanged.
    t.spot['trade-fee'][0]['feeGroup'][0]['maker'] = str(-maker)
    t.connector.enable_protected_trading_pair('LIFE-USDT')
    journal = t.reader.source.journal
    active = set()

    async def runner():
        return RunnerActionFacts(t.cfg.binding, datetime.now(timezone.utc), 'main', tuple(sorted(active)), True)
    journal.runner_facts, journal.local_ready = runner, lambda: True
    preparation = [None]

    async def prepare(action, wire):
        assert action == create
        preparation[0] = await t.market_source.prepare(app, wire, subsidy_quote_usdt=subsidy,
                                                       fee_buffer_quote_usdt=price * maker, fee_buffer_base_life=maker,
                                                       carry_cost_usdt=D('0'), inventory_risk_usdt=D('0'))
        return preparation[0]
    source = await PgQuoteAccountSource.create(journal, connector=t.connector, market_source=t.market_source,
                                               max_uid_age_ms=1000, max_auth_age_ms=1000, max_clock_skew_ms=100,
                                               max_pending=1, collection_timeout_ms=1000)
    app = PgSpotQuoteApplication(source, preparation_source=prepare, max_pending=1,
                                 operation_timeout_ms=5000, shutdown_timeout_ms=1000)
    guards, final_checks = [], []
    original_guard = source.guard

    def capture_guard(**kwargs):
        guard = original_guard(**kwargs)
        guards.append(guard)
        original_check = guard.check
        original_authorize = guard.authorize

        async def authorize(request):
            try:
                result = await original_authorize(request)
            except BaseException as error:
                error.add_note(str(final_checks))
                final_checks.append(dict(stage='authorize', error=type(error).__name__ + ':' + str(error),
                                         age_ms=(journal.clock().timestamp() - guard._action.executor_config.timestamp) * 1000))
                raise
            if result is not True:
                final_checks.append('authorize:denied')
            return result

        def check(request):
            try:
                result = original_check(request)
            except BaseException as error:
                final_checks.append(type(error).__name__ + ':' + str(error))
                raise
            final_checks.append(dict(result=result, fresh=guard._fresh(),
                                     age_ms=(journal.clock().timestamp() - guard._action.executor_config.timestamp) * 1000,
                                     epoch=t.financial._runtime._ingress_epoch, expected=guard._captured_ingress_epoch))
            return result
        guard.check = check
        guard.authorize = authorize
        return guard
    source.guard = capture_guard
    original = t.session.request

    async def http(**kwargs):
        if kwargs['method'] != 'POST':
            return await original(**kwargs)
        t.requests.append(kwargs)
        response = Response()

        async def body():
            wire = json.loads(kwargs['data'])['clOrdId']
            return dict(code='0', data=[dict(sCode='0', ordId='2468', clOrdId=wire)])
        response.json = body
        return response
    t.session.request = http
    # Expensive cold preflight precedes capture of this new intent. These
    # stamps are fixed thereafter, including failure and retry paths.
    await t.financial.snapshot(t.cfg.binding)
    intent = PgSpotQuoteIntent('quote1', 'main', t.owner, 1, TradeType[side], 0, D('1'), price)
    wire, create, created = intent.wire_id, None, []
    executors = []

    async def start():
        nonlocal create
        try:
            allocated = await t.app.allocate_spot('quote-allocation', intent)
        except BaseException as error:
            report = t.joint._report
            error.add_note(str(dict(stage='allocation', report_age_ms=None if report is None else
                                    (journal.clock() - report.observed_at).total_seconds() * 1000,
                                    healthy=t.financial._runtime._healthy, observation_inflight=t.financial._runtime._observation_inflight)))
            raise
        assert allocated.capital.decision.allowed and allocated.carry.decision.allowed
        create = await source.capture_intent_ready(intent)
        created.append(create)
        await journal.publish('quote-claim', (create,))
        active.add('quote1')
        assert await journal.async_on_runner_create_action_dispatched(create)
        config = create.executor_config
        strategy = SimpleNamespace(connectors={'okx': t.connector}, controllers={'main': journal}, current_timestamp=config.timestamp)
        executor = OrderExecutor(strategy, config)
        executors.append(executor)
        executor.register_events()
        executor.place_open_order()
        assert executor._order.order_id == wire

    async def close():
        await app.close()
        for executor in executors:
            executor.unregister_events()
        await t.financial.close()
    return SimpleNamespace(**locals())


@pytest.mark.asyncio
@pytest.mark.parametrize('side', ['BUY', 'SELL'])
async def test_original_pending_sdk_order_can_collect_signed_final_market_then_arm_and_ack(database, side):
    x = await application(database, side=side)
    try:
        await x.start()
        try:
            await x.app._pending['quote1']
        except BaseException as error:
            error.add_note(str(x.final_checks))
            raise
        await x.app.drain()
        posts = [r for r in x.t.requests if r['method'] == 'POST']
        assert len(posts) == 1 and json.loads(posts[0]['data'])['clOrdId'] == x.wire
        attempt = await x.t.financial.attempt('quote1', x.t.cfg.binding)
        assert attempt.state.value == 'ACKED' and attempt.exchange_order_id == '2468'
        capture = x.t.market_source._capture
        assert capture.received_at > x.preparation[0].admission.observed_at
        assert capture.book.exchange_timestamp_ms == x.t.spot_at  # cached feed was not renewed
        assert not capture.trading_permission and not x.app._guards
        assert x.executors[0]._order.order.exchange_order_id == '2468'
        with pytest.raises(StorageError, match='ALREADY_STARTED'):
            cfg = x.created[0].executor_config
            x.app.submit(cfg, amount=cfg.amount, price=cfg.price, order_type=OrderType.LIMIT_MAKER)
        assert len([r for r in x.t.requests if r['method'] == 'POST']) == 1
    finally:
        await x.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('side', ['BUY', 'SELL'])
@pytest.mark.parametrize('preload', [0, 4])
async def test_pooled_spot_cycle_reaches_original_ack_without_renewing_capture_or_source_budgets(database, side, preload):
    x = await application(database, shared_quota=True, side=side)
    try:
        for _ in range(preload):
            assert await x.t.swap_connector.get_account_uid() == '12345'
        await x.start()
        try:
            await x.app._pending['quote1']
        except BaseException as error:
            error.add_note(str(x.final_checks))
            raise
        await x.app.drain()
        assert (await x.t.financial.attempt('quote1', x.t.cfg.binding)).state.value == 'ACKED'
        assert len([r for r in x.t.requests if r['method'] == 'POST']) == 1
        assert x.final_checks[-1]['result'] is True and 0 <= x.final_checks[-1]['age_ms'] <= 1000
        captured_at = x.created[0].executor_config.timestamp
        after_capture = [r for r in x.t.requests if r['url'].endswith('/account/config')
                         and datetime.fromisoformat(r['headers']['OK-ACCESS-TIMESTAMP'].replace('Z', '+00:00')).timestamp() >= captured_at]
        assert len(after_capture) == 5
        snapshot = (await x.t.source.capital.frame(x.t.cfg.binding))['snapshot']
        assert captured_at * 1000 - snapshot['observed_at_ms'] >= 2000  # Original money feed was not renewed.
        x.t.quota.assert_binding()
        with pytest.raises(StorageError, match='ALREADY_USED'):
            await x.source.capture_intent_ready(x.intent)
    finally:
        await x.close()


@pytest.mark.asyncio
async def test_competing_uid_capacity_after_capture_refuses_http_and_retains_original_prepared_holds(database):
    x = await application(database, shared_quota=True)
    original, injected = x.source.guard, []

    def guard(**kwargs):
        value = original(**kwargs)
        authorize = value.authorize

        async def squeezed(request):
            assert await x.t.swap_connector.get_account_uid() == '12345'
            injected.append(True)
            return await authorize(request)
        value.authorize = squeezed
        return value
    x.source.guard = guard
    try:
        await x.start()
        captured = x.created[0].executor_config.timestamp
        with pytest.raises(StorageError):
            await x.app._pending['quote1']
        assert injected == [True]
        assert not [r for r in x.t.requests if r['method'] == 'POST']
        assert (await x.t.financial.attempt('quote1', x.t.cfg.binding)).state.value == 'PREPARED'
        assert len(await x.t.source.capital.claims(x.t.cfg.binding)) == 1
        assert x.created[0].executor_config.timestamp == captured
        assert not x.t.financial._runtime.healthy
    finally:
        await x.close()


@pytest.mark.asyncio
async def test_pooled_pending_observation_exception_never_admits_a_financial_send_during_collection(database, monkeypatch):
    x = await application(database, shared_quota=True)
    reader = x.t.reader
    original, reached = reader._capture_configuration, []

    async def capture(proof):
        guard = x.t.market_source._pending_quote
        if guard is not None:
            action = x.created[0]
            with pytest.raises(StorageError, match='APPLICATION_STOPPED'):
                x.app._sdk_check(action, x.wire, {'clOrdId': x.wire})
            with pytest.raises(StorageError, match='PENDING_ORIGIN_REQUIRED'):
                guard.pending_order(armed=True, observation=True)
            assert guard.pending_order(observation=True).client_order_id == x.wire

            async def foreign():
                with pytest.raises(StorageError, match='QUOTA_PHASE_INVALID'):
                    async with x.t.quota.collection(continuation=x.t.market_source):
                        pytest.fail('A foreign task borrowed the original quote cycle')
            await asyncio.create_task(foreign())
            reached.append(True)
        return await original(proof)
    monkeypatch.setattr(reader, '_capture_configuration', capture)
    try:
        await x.start()
        try:
            await x.app._pending['quote1']
        except BaseException as error:
            error.add_note(str(x.final_checks))
            error.add_note('observation injections: ' + str(reached))
            raise
        assert reached == [True, True]
        assert len([r for r in x.t.requests if r['method'] == 'POST']) == 1
    finally:
        await x.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['stop', 'cancel'])
async def test_waiting_for_quota_never_creates_an_action_after_stop_or_cancellation(database, fault):
    x = await application(database, shared_quota=True)
    task = None
    try:
        with pytest.raises(StorageError, match='QUOTA_CAPTURE_REQUIRED'):
            x.source.capture_intent(x.intent)
        assert await x.t.swap_connector.get_account_uid() == '12345'
        task = asyncio.create_task(x.source.capture_intent_ready(x.intent))
        await asyncio.sleep(.03)
        assert not task.done() and not x.source._intents
        if fault == 'stop':
            x.source.revoke()
        else:
            task.cancel()
        with pytest.raises(asyncio.CancelledError if fault == 'cancel' else StorageError):
            await asyncio.wait_for(task, 3)
        assert not x.source._intents and not x.source._scheduled and x.source._capture_ready_task is None
        assert not x.t.quota._collection_lock.locked()
        assert not [r for r in x.t.requests if r['method'] == 'POST']
    finally:
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await x.close()


@pytest.mark.asyncio
async def test_scheduled_capture_expiry_cannot_be_renewed_during_original_preparation(database, monkeypatch):
    x = await application(database, shared_quota=True)
    original = x.t.market_source.collect

    async def delayed(**kwargs):
        if kwargs.get('preparing_quote') is not None:
            await asyncio.sleep(1.01)
        return await original(**kwargs)
    monkeypatch.setattr(x.t.market_source, 'collect', delayed)
    try:
        await x.start()
        captured = x.created[0].executor_config.timestamp
        with pytest.raises(StorageError, match='QUOTA_CAPTURE_EXPIRED'):
            await x.app._pending['quote1']
        assert x.created[0].executor_config.timestamp == captured
        assert await x.t.financial.attempt('quote1', x.t.cfg.binding) is None
        assert len(await x.t.source.capital.claims(x.t.cfg.binding)) == 1
        assert not [r for r in x.t.requests if r['method'] == 'POST']
    finally:
        await x.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('shared_quota', [False, True])
async def test_signed_liquidity_service_quote_keeps_exact_original_subsidy_at_prepare_and_arm(database, shared_quota):
    x = await application(database, objective='liquidity_service', price=D('1'), subsidy=D('.01248'), shared_quota=shared_quota)
    try:
        await x.start()
        await x.app._pending['quote1']
        assert (await x.t.financial.attempt('quote1', x.t.cfg.binding)).state.value == 'ACKED'
        view = await x.t.financial.snapshot(x.t.cfg.binding)
        assert view.budget.session_subsidy_committed_usdt == D('.01248')
        assert len([r for r in x.t.requests if r['method'] == 'POST']) == 1
    finally:
        await x.close()


@pytest.mark.asyncio
async def test_same_fee_schedule_cannot_overdraw_shared_allocation_for_two_possible_fee_currencies(database):
    x = await application(database, maker=D('.001'))
    try:
        await x.start()
        with pytest.raises(StorageError, match='CAPITAL_FEE_BUFFER_UNCOVERED'):
            await x.app._pending['quote1']
        assert await x.t.financial.attempt('quote1', x.t.cfg.binding) is None
        assert not [r for r in x.t.requests if r['method'] == 'POST']
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.shared_capital_claims') == 1
    finally:
        await x.close()


@pytest.mark.asyncio
async def test_preparation_cut_is_original_one_use_and_never_recreated_from_equal_dto(database, monkeypatch):
    x = await application(database)
    original, taken = x.t.market_source.take_preparation_cut, []

    def take(app, command):
        with pytest.raises(StorageError, match='PREPARATION_ORIGIN_REQUIRED'):
            original(app, replace(command))
        cut = original(app, command)
        with pytest.raises(StorageError, match='PREPARATION_ORIGIN_REQUIRED'):
            original(app, command)
        taken.append(cut)
        return cut
    monkeypatch.setattr(x.t.market_source, 'take_preparation_cut', take)
    try:
        await x.start()
        await x.app._pending['quote1']
        assert len(taken) == 1
        assert len([r for r in x.t.requests if r['method'] == 'POST']) == 1
    finally:
        await x.close()


@pytest.mark.asyncio
async def test_only_original_pending_guard_task_can_use_its_unsent_sdk_order(database, monkeypatch):
    x = await application(database)
    original, seen = x.t.market_source.collect, []

    async def collect(**kwargs):
        guard = kwargs.get('pending_quote')
        if guard is not None:
            seen.append(guard)
            cloned = copy(guard)
            with pytest.raises(StorageError, match='PENDING_ORIGIN_REQUIRED'):
                cloned.pending_order()

            async def foreign_task():
                with pytest.raises(StorageError, match='PENDING_ORIGIN_REQUIRED'):
                    guard.pending_order()
            await asyncio.create_task(foreign_task())
            assert guard.pending_order().client_order_id == x.wire
        return await original(**kwargs)
    monkeypatch.setattr(x.t.market_source, 'collect', collect)
    try:
        await x.start()
        try:
            await x.app._pending['quote1']
        except BaseException as error:
            error.add_note(str(x.final_checks))
            raise
        assert len(seen) == 1
        with pytest.raises(StorageError, match='PENDING_ORIGIN_REQUIRED'):
            seen[0].pending_order()
        assert len([r for r in x.t.requests if r['method'] == 'POST']) == 1
    finally:
        await x.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['ingress_before', 'ingress_after', 'ack', 'fill', 'state', 'clone', 'foreign_sdk', 'runner_missing', 'stop'])
async def test_final_capture_rejects_unowned_epoch_changes_and_changed_pending_sdk_scope(database, monkeypatch, fault):
    x = await application(database)
    original, injected = x.t.market_source.collect, []

    async def collect(**kwargs):
        if kwargs.get('pending_quote') is not None:
            injected.append(True)
            order = x.t.connector._order_tracker.active_orders[x.wire]
            if fault == 'ingress_before':
                x.t.financial._runtime._ingress_epoch += 1
            elif fault == 'ack':
                order.exchange_order_id = '999'
            elif fault == 'fill':
                order.executed_amount_base = D('.1')
            elif fault == 'state':
                order.current_state = OrderState.OPEN
            elif fault == 'clone':
                x.t.connector._order_tracker._in_flight_orders[x.wire] = copy(order)
            elif fault == 'foreign_sdk':
                x.t.connector.start_tracking_order(order_id='foreign', exchange_order_id=None, trading_pair='LIFE-USDT',
                                                   trade_type=TradeType.BUY, price=D('.9'), amount=D('1'), order_type=OrderType.LIMIT_MAKER)
            elif fault == 'runner_missing':
                x.active.clear()
            elif fault == 'stop':
                x.app.stop()
            result = await original(**kwargs)
            if fault == 'ingress_after':
                x.t.financial._runtime._ingress_epoch += 1
            return result
        return await original(**kwargs)
    monkeypatch.setattr(x.t.market_source, 'collect', collect)
    try:
        await x.start()
        with pytest.raises(StorageError):
            await x.app._pending['quote1']
        assert injected == [True]  # the fault ran; no earlier failure can pass
        assert not [r for r in x.t.requests if r['method'] == 'POST']
        attempt = await x.t.financial.attempt('quote1', x.t.cfg.binding)
        assert attempt.state.value == 'PREPARED'
        assert await database[0].fetchval("SELECT reserved FROM life_financial.balances WHERE asset='USDT'") > 0
        assert not x.source.pending_count
    finally:
        await x.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['ingress', 'sdk_clone', 'stop'])
async def test_after_physical_arm_changes_prevent_http_without_releasing_held_money(database, monkeypatch, fault):
    x = await application(database)
    original, injected = x.t.financial.arm_quote_action, []

    async def arm(*args, **kwargs):
        result = await original(*args, **kwargs)
        injected.append(True)
        if fault == 'ingress':
            x.t.financial._runtime._ingress_epoch += 1
        elif fault == 'sdk_clone':
            order = x.t.connector._order_tracker.active_orders[x.wire]
            x.t.connector._order_tracker._in_flight_orders[x.wire] = copy(order)
        else:
            x.app.stop()
        return result
    monkeypatch.setattr(x.t.financial, 'arm_quote_action', arm)
    try:
        await x.start()
        with pytest.raises(StorageError):
            await x.app._pending['quote1']
        assert injected == [True]
        assert not [r for r in x.t.requests if r['method'] == 'POST']
        assert (await x.t.financial.attempt('quote1', x.t.cfg.binding)).state.value == 'SEND_UNKNOWN'
        assert await database[0].fetchval("SELECT reserved FROM life_financial.balances WHERE asset='USDT'") > 0
    finally:
        await x.close()


@pytest.mark.asyncio
async def test_business_intent_is_captured_once_and_never_renews_mutated_or_retried_action(database):
    x = await application(database)
    try:
        action = x.source.capture_intent(x.intent)
        original_stamp = action.executor_config.timestamp
        assert x.source.wire_for_action(action) == x.wire
        with pytest.raises(StorageError, match='ALREADY_USED_OR_INVALID'):
            x.source.capture_intent(x.intent)
        action.executor_config.timestamp += 1
        with pytest.raises(StorageError, match='INTENT_MISMATCH'):
            x.source.wire_for_action(action)
        assert x.source._intents['quote1'][1].payload_json != action.executor_config.model_dump_json()
        assert original_stamp < action.executor_config.timestamp
        # Step checks must not inherit the application's smaller Decimal
        # precision. This DTO grants no capital or oversized order admission.
        large = replace(x.intent, intent_id='large-unfunded', quantity_base=D('1000000000000000000000000000001'))
        captured = x.source.capture_intent(large)
        assert captured.executor_config.amount == large.quantity_base
        assert not [r for r in x.t.requests if r['method'] == 'POST']
    finally:
        await x.close()


@pytest.mark.asyncio
async def test_native_market_binding_cannot_be_removed_to_fall_back_to_a_callback(database):
    t = await market(database)
    try:
        source = await PgQuoteAccountSource.create(t.reader.source.journal, connector=t.connector, market_source=t.market_source,
                                                   max_uid_age_ms=1000, max_auth_age_ms=1000, max_clock_skew_ms=100,
                                                   max_pending=1, collection_timeout_ms=1000)
        source._market_source = None
        with pytest.raises(StorageError, match='MARKET_SOURCE_CHANGED'):
            source._credentials()
        assert not [r for r in t.requests if r['method'] == 'POST']
    finally:
        await t.financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('malformed', [False, True])
async def test_fixed_account_first_pages_share_one_diagnostic_commit_before_any_parsing(database, monkeypatch, malformed):
    t = await market(database)
    batches, original = [], t.reader._journal.capture_many

    async def capture(pages):
        batches.append(tuple(p['path'] for p in pages))
        return await original(pages)
    monkeypatch.setattr(t.reader._journal, 'capture_many', capture)
    if malformed:
        t.bills = [dict(billId='99')]  # below anchor, never interpreted as empty history
    try:
        if malformed:
            with pytest.raises(StorageError):
                await t.market_source.collect()
        else:
            await t.market_source.collect()
        expected = ('/api/v5/account/bills-archive', '/api/v5/trade/fills-history',
                    '/api/v5/trade/fills-history', '/api/v5/trade/orders-pending')
        assert batches.count(expected) == (1 if malformed else 2)
        pages = await t.reader._journal.read_collection(t.reader.collection_id, max_rows=128, max_bytes=4194304)
        assert pages[0].body['path'] == '/api/v5/account/config'
        assert tuple(p.body['path'] for p in pages[1:5]) == expected
        assert not [r for r in t.requests if r['method'] != 'GET']
    finally:
        await t.financial.close()


@pytest.mark.asyncio
async def test_original_preparation_commit_followed_by_ingress_never_starts_sdk_submission(database, monkeypatch):
    x = await application(database)
    original, injected = x.t.financial.prepare_quote_action, []

    async def prepare(*args, **kwargs):
        receipt = await original(*args, **kwargs)
        injected.append(True)
        x.t.financial._runtime._ingress_epoch += 1
        return receipt
    monkeypatch.setattr(x.t.financial, 'prepare_quote_action', prepare)
    try:
        await x.start()
        with pytest.raises(StorageError, match='PREPARATION_SOURCE_CHANGED'):
            await x.app._pending['quote1']
        assert injected == [True]
        assert (await x.t.financial.attempt('quote1', x.t.cfg.binding)).state.value == 'PREPARED'
        assert not x.t.connector._order_tracker.active_orders
        assert not [r for r in x.t.requests if r['method'] == 'POST']
    finally:
        await x.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['commit_reply', 'cancel'])
async def test_initial_account_batch_cannot_publish_after_ambiguous_commit_or_partial_transport(database, monkeypatch, fault):
    from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown
    t = await market(database)
    original, original_http = t.reader._journal.capture_many, t.session.request

    async def capture(pages):
        receipt = await original(pages)
        if pages[0]['path'] == '/api/v5/account/bills-archive':
            raise CommitUnknown('PG_COMMIT_UNKNOWN')
        return receipt

    async def http(**kwargs):
        if kwargs['url'].endswith('/trade/fills-history'):
            raise asyncio.CancelledError()
        return await original_http(**kwargs)
    if fault == 'commit_reply':
        monkeypatch.setattr(t.reader._journal, 'capture_many', capture)
    else:
        t.session.request = http
    try:
        with pytest.raises((StorageError, asyncio.CancelledError)):
            await t.market_source.collect()
        assert t.market_source._capture is None and t.joint._report is None
        assert not t.reader._active and not t.market_source._busy and not t.financial._runtime.healthy
        pages = await t.reader._journal.read_collection(t.reader.collection_id, max_rows=128, max_bytes=4194304)
        assert len(pages) == (5 if fault == 'commit_reply' else 2)  # Also retain the original opening config.
        assert not [r for r in t.requests if r['method'] != 'GET']
    finally:
        await t.financial.close()
