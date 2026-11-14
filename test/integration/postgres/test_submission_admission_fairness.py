"""A native quote burst must leave loop turns for health and revocation checks."""
import asyncio
import json
import time
from decimal import Decimal
from test.integration.postgres.test_ownership import assistant, owned as owned_fixture
from test.integration.postgres.test_spot_outflow import setup
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from bidict import bidict
from sqlalchemy import event

from hummingbot.connector.exchange.okx.okx_exchange import OkxExchange
from hummingbot.connector.markets_recorder import MarketsRecorder
from hummingbot.connector.trading_rule import TradingRule
from hummingbot.core.data_type.common import OrderType, TradeType
from hummingbot.core.data_type.in_flight_order import OrderState, OrderUpdate
from hummingbot.core.data_type.trade_fee import AddedToCostTradeFee
from hummingbot.core.order_submission_guard import session_order_scope
from hummingbot.model.order import Order
from hummingbot.model.runtime_journal import SubmissionAttempt

owned = owned_fixture


@pytest.fixture
def native(owned):
    exchange = OkxExchange(okx_api_key='offline', okx_secret_key='offline', okx_passphrase='offline',
                           trading_pairs=['LIFE-USDT'])
    exchange._set_current_timestamp(time.time())
    exchange._set_trading_pair_symbol_map(bidict({'LIFE-USDT': 'LIFE-USDT'}))
    exchange._trading_rules['LIFE-USDT'] = TradingRule('LIFE-USDT', min_order_size=Decimal('.01'),
                                                       min_price_increment=Decimal('.01'),
                                                       min_base_amount_increment=Decimal('.01'))
    exchange.get_fee = MagicMock(return_value=AddedToCostTradeFee(percent=Decimal('.001')))
    recorder = MarketsRecorder(owned.manager, [exchange], 'test.yml', 'pure_market_making',
                               owned.recorder._market_data_collection_config)
    recorder.start()
    context = SimpleNamespace(**{**vars(owned), 'market': exchange, 'recorder': recorder})
    owned.owner.connectors = {'okx': exchange}
    session, _ = setup(context, max_net_outflow_quote='1000')
    rest, transport = assistant()
    rest._auth = exchange.authenticator

    async def reply(request):
        data = json.loads(request.data)
        response = MagicMock(status=200)
        response.json = AsyncMock(return_value={'code': '0', 'data': [dict(ordId='venue-' + data['clOrdId'], sCode='0')]})
        return response

    transport.call.side_effect = reply
    try:
        with patch.object(exchange._web_assistants_factory, 'get_rest_assistant', AsyncMock(return_value=rest)):
            yield SimpleNamespace(**vars(context), session=session, transport=transport)
    finally:
        recorder.stop()
        owned.loop.run_until_complete(exchange._web_assistants_factory.close())


def test_native_eight_quote_burst_allows_health_callback_between_admissions(native):
    admitted, probe_counts = [], []
    original = native.recorder.record_order_intent

    def record(*args):
        original(*args)
        admitted.append(args[1].client_order_id)
        if len(admitted) == 1:
            asyncio.get_running_loop().call_soon(lambda: probe_counts.append(len(admitted)))

    async def burst():
        with session_order_scope(native.session.id, native.session.generation):
            tasks = [
                asyncio.create_task(native.market._create_order(
                    TradeType.BUY, 'burst-' + str(i), 'LIFE-USDT', Decimal('5'), OrderType.LIMIT_MAKER, Decimal('10')))
                for i in range(8)]
        await asyncio.gather(*tasks)
        await asyncio.sleep(0)
        assert not native.owner.submission_tasks

    with patch.object(native.recorder, 'record_order_intent', side_effect=record):
        native.loop.run_until_complete(burst())
    assert len(admitted) == native.transport.call.await_count == 8
    assert probe_counts == [1], f'Health callback waited behind {probe_counts} synchronous admissions'
    with native.manager.get_new_session() as db:
        assert db.query(Order).count() == db.query(SubmissionAttempt).count() == 8
        assert not db.query(Order).filter(Order.creation_pending.is_(True)).count()
        assert {x.state for x in db.query(SubmissionAttempt)} == {'acknowledged'}
    assert not native.store.read()['recovery_state'].get('halt')


def test_stop_between_native_admissions_prevents_the_rest_of_the_burst(native):
    original = native.recorder.record_order_intent
    admitted = []

    def record(*args):
        original(*args)
        admitted.append(args[1].client_order_id)
        if len(admitted) == 1:
            asyncio.get_running_loop().call_soon(native.owner.begin_shutdown)

    async def burst():
        with session_order_scope(native.session.id, native.session.generation):
            tasks = [
                asyncio.create_task(native.market._create_order(
                    TradeType.BUY, 'stopped-' + str(i), 'LIFE-USDT', Decimal('5'), OrderType.LIMIT_MAKER, Decimal('10')))
                for i in range(8)]
        await asyncio.gather(*tasks)
        await asyncio.sleep(0)
        assert not native.owner.submission_tasks

    with patch.object(native.recorder, 'record_order_intent', side_effect=record):
        native.loop.run_until_complete(burst())
    assert len(admitted) == native.transport.call.await_count == 1
    with native.manager.get_new_session() as db:
        assert db.query(Order).count() == db.query(SubmissionAttempt).count() == 1
        assert not db.query(Order).filter(Order.creation_pending.is_(True)).count()
    assert not native.store.read()['recovery_state'].get('halt')


def test_native_burst_paired_callback_wait_with_real_postgres(native):
    from test.integration.postgres.test_performance import queries, report

    from hummingbot.core import order_submission_guard as gates
    original_record, current = native.recorder.record_order_intent, gates.before_order

    async def predecessor(connector, order):
        gates.active_guard().before_order(connector, order)

    # The same bounded synthetic round-trip cost is present in both schedules.
    def delayed_roundtrip(*args):
        time.sleep(.002)

    async def run(legacy):
        admitted, receipt = [], {}
        started = time.perf_counter()

        def record(*args):
            original_record(*args)
            admitted.append(args[1].client_order_id)
            if len(admitted) == 1:
                queued_at = time.perf_counter()

                def probe():
                    receipt.update(admissions_before_probe=len(admitted),
                                   callback_wait_ms=(time.perf_counter() - queued_at) * 1000)

                asyncio.get_running_loop().call_soon(probe)

        with patch.object(native.recorder, 'record_order_intent', side_effect=record), \
                patch.object(gates, 'before_order', predecessor if legacy else current), \
                queries(native.engine) as statements:
            with session_order_scope(native.session.id, native.session.generation):
                tasks = [asyncio.create_task(native.market._create_order(
                    TradeType.BUY, f'paired-{legacy}-{i}', 'LIFE-USDT', Decimal('5'),
                    OrderType.LIMIT_MAKER, Decimal('10'))) for i in range(8)]
            await asyncio.gather(*tasks)
            await asyncio.sleep(0)
        receipt['burst_completion_ms'] = (time.perf_counter() - started) * 1000
        receipt['sql_statements'] = len(statements)
        assert len(admitted) == 8
        return receipt

    event.listen(native.engine, 'after_cursor_execute', delayed_roundtrip)
    try:
        before = native.loop.run_until_complete(run(True))
        after = native.loop.run_until_complete(run(False))
    finally:
        event.remove(native.engine, 'after_cursor_execute', delayed_roundtrip)
    assert before['admissions_before_probe'] == 8 and after['admissions_before_probe'] == 1
    assert native.transport.call.await_count == 16
    with native.manager.get_new_session() as db:
        assert db.query(Order).count() == db.query(SubmissionAttempt).count() == 16
        assert not db.query(Order).filter(Order.creation_pending.is_(True)).count()
        assert all(row.amount == Decimal('5') and row.price == Decimal('10') for row in db.query(Order))
    report('submission_admission_callback_wait', dict(before=before, after=after, injected_sql_delay_seconds=.002,
                                                      scope='Native connector and real PostgreSQL; not Cloud HTTP latency'))


@pytest.mark.parametrize('retirement', ['canceled', 'removed'])
def test_native_order_retired_while_queued_cannot_leave_an_intent_or_halt(native, retirement):
    async def run():
        await native.owner.submission_admission.acquire()
        with session_order_scope(native.session.id, native.session.generation):
            task = asyncio.create_task(native.market._create_order(
                TradeType.BUY, 'retired', 'LIFE-USDT', Decimal('5'), OrderType.LIMIT_MAKER, Decimal('10')))
        await asyncio.sleep(0)
        assert task in native.owner.submission_tasks
        if retirement == 'canceled':
            native.market._order_tracker.process_order_update(OrderUpdate(
                trading_pair='LIFE-USDT', update_timestamp=time.time(), new_state=OrderState.CANCELED,
                client_order_id='retired'))
        else:
            native.market.stop_tracking_order('retired')
        native.owner.submission_admission.release()
        await task
        await asyncio.sleep(0)
        assert not native.owner.submission_tasks

    native.loop.run_until_complete(run())
    native.transport.call.assert_not_awaited()
    with native.manager.get_new_session() as db:
        assert db.query(Order).count() == db.query(SubmissionAttempt).count() == 0
    assert not native.store.read()['recovery_state'].get('halt')


@pytest.mark.parametrize('legacy', [True, False], ids=['predecessor', 'candidate'])
def test_fresh_burst_callback_and_completion_work(native, legacy):
    """Equal empty ledgers avoid growing the candidate's workload after a replay."""
    from test.integration.postgres.test_performance import queries, report

    from hummingbot.core import order_submission_guard as gates
    current, original_record = gates.before_order, native.recorder.record_order_intent
    admitted, timing = [], {}

    async def predecessor(connector, order):
        gates.active_guard().before_order(connector, order)

    def record(*args):
        original_record(*args)
        admitted.append(args[1].client_order_id)
        if len(admitted) == 1:
            queued_at = time.perf_counter()

            def observe():
                timing.update(admissions_before_callback=len(admitted),
                              callback_wait_ms=(time.perf_counter() - queued_at) * 1000)

            asyncio.get_running_loop().call_soon(observe)

    def delay(*args):
        time.sleep(.002)

    async def run():
        with session_order_scope(native.session.id, native.session.generation):
            tasks = [asyncio.create_task(native.market._create_order(
                TradeType.BUY, 'fresh-' + str(i), 'LIFE-USDT', Decimal('5'),
                OrderType.LIMIT_MAKER, Decimal('10'))) for i in range(8)]
        await asyncio.gather(*tasks)
        await asyncio.sleep(0)

    event.listen(native.engine, 'after_cursor_execute', delay)
    try:
        with patch.object(native.recorder, 'record_order_intent', side_effect=record), \
                patch.object(gates, 'before_order', predecessor if legacy else current), queries(native.engine) as statements:
            started = time.perf_counter()
            native.loop.run_until_complete(run())
            timing.update(burst_completion_ms=(time.perf_counter() - started) * 1000, sql_statements=len(statements))
    finally:
        event.remove(native.engine, 'after_cursor_execute', delay)
    assert timing['admissions_before_callback'] == (8 if legacy else 1)
    assert len(admitted) == native.transport.call.await_count == 8
    with native.manager.get_new_session() as db:
        assert db.query(Order).count() == db.query(SubmissionAttempt).count() == 8
        assert not db.query(Order).filter(Order.creation_pending.is_(True)).count()
    report('fresh_burst_predecessor' if legacy else 'fresh_burst_candidate', timing)
