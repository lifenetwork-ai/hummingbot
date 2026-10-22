"""Configured retained-fill maker cycles with real shared SDK quota contention."""
import asyncio
import json
import os
import time
from datetime import timedelta
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_service_cycle import cancellation_feed
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_service_fills import partial_spot_fill
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_service_trading import configured

import pytest

from hummingbot.core.data_type.common import TradeType


@pytest.mark.asyncio
@pytest.mark.timeout(360)
async def test_configured_makers_replenish_after_fill_history_with_competing_shared_sdk_reads(database, monkeypatch):
    # This complete workflow includes the original 300-second owner lease,
    # retained-fill setup, two generations, retirement and joined cleanup. Give
    # the outer CI watchdog that lease plus 60 seconds of fixture/cleanup room;
    # the 1000-ms quote and all actual operation/owner limits stay independent.
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.flow_trace import QuoteFlowTrace
    trace = QuoteFlowTrace(monkeypatch).install() if os.environ.get('LIFE_PG_TEST_FLOW_TRACE') == '1' else None
    values = {}

    def model(body):
        body.update(maker_schedule=dict(schedule_id='synthetic-retained-fill-cycles', levels=[0], refresh_interval_ms=1, max_cycles=2),
                    quote_schedule_timeout_ms=60000)
    t, service = await configured(
        database, maker_cycle=True, model_change=model,
        independent_fill_value=lambda product, trade, at: values[(product, trade)],
        spot_overrides=dict(owner_duration=timedelta(seconds=300), session_duration=timedelta(seconds=360)))
    stream, traffic, primary = None, None, None
    if trace is not None:
        trace.http(t.session)
    request_times = []
    try:
        f = await partial_spot_fill(t, service, values, TradeType.BUY, 'USDT')
        stream = f.stream
        await service.events.drain()
        await service.reporting.flush_deferred()
        await service.reporting.drain()
        f.order['state'] = 'canceled'
        await t.connector._user_stream_tracker.user_stream.put(dict(
            arg=dict(channel='orders'), data=[dict(f.order, tradeId='', uTime=str(f.at + 1))]))
        async with asyncio.timeout(5):
            while f.order['clOrdId'] in t.connector._order_tracker.active_orders:
                await asyncio.sleep(.001)
        await service.cancel_makers((f.action.executor_config.id,))
        stream.cancel()
        await asyncio.gather(stream, return_exceptions=True)
        stream, canceled = await cancellation_feed(t)
        cost = (await service.financial.snapshot(t.cfg.binding)).budget.session_execution_loss_usdt
        reads = []
        http = t.session.request

        async def timed_http(**kwargs):
            request_times.append((kwargs['method'], kwargs['url'].rsplit('/', 1)[1], time.time()))
            return await http(**kwargs)
        t.session.request = timed_http

        async def background():
            # Fixed bounded synthetic traffic uses both original SDKs and
            # their actual shared limits; it has no financial/source authority.
            for sdk in (t.connector, t.swap_connector, t.connector, t.swap_connector):
                reads.append(await sdk.get_account_uid())
                await asyncio.sleep(1)
        traffic = asyncio.create_task(background())
        await service.start_makers()
        await traffic
        assert reads == ['12345'] * 4
        assert service.makers.completed_cycles == 2 and service.makers.failure is None
        assert len(canceled) == len(set(canceled)) == 4
        assert not service.runner.executor_orchestrator.active_executors['main']
        assert not t.connector._order_tracker.active_orders
        assert not await service.source.capital.claims(t.cfg.binding)
        balance = await service.financial.balance(t.cfg.binding)
        assert (balance.total_base_life, balance.total_quote_usdt) == (f.expected_base, f.expected_quote)
        assert balance.reserved_base_life == balance.reserved_quote_usdt == 0
        assert (await service.financial.snapshot(t.cfg.binding)).budget.session_execution_loss_usdt == cost == f.fee
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.spot_fill_facts') == 1
        assert len([r for r in t.requests if r['method'] == 'POST' and r['url'].endswith('/order')]) == 5
        assert service.runtime.healthy
        if trace is not None:
            print('bounded_quote_flow_summary=' + json.dumps(trace.summaries()))
            print('bounded_service_revocations=' + json.dumps(list(trace.revocations)))
    except BaseException as error:
        primary = error
        if trace is not None:
            error.add_note('bounded_service_revocations=' + json.dumps(list(trace.revocations)))
            error.add_note('bounded_monotonic_flow=' + json.dumps(trace.rows()))
            error.add_note('bounded_quote_flow_summary=' + json.dumps(trace.summaries()))
            error.add_note('flow_spans_dropped=' + str(trace.dropped))
            error.add_note('flow_detail_spans_dropped=' + str(trace.details_dropped))
        error.add_note('completed_maker_cycles=' + str(service.makers.completed_cycles))
        if request_times:
            origin = request_times[0][2]
            error.add_note('bounded_sdk_request_times_ms=' + json.dumps([
                (method, route, round((at - origin) * 1000, 3)) for method, route, at in request_times[-80:]]))
        raise
    finally:
        if traffic is not None:
            await asyncio.gather(traffic, return_exceptions=True)
        try:
            await service.close()
        except BaseException as error:
            if primary is None:
                raise
            primary.add_note('cleanup_failure=' + type(error).__name__ + ':' + str(error))
        finally:
            try:
                # The test owns the fake SDK and disposable DB. A bounded
                # close timeout cannot release those dependencies while the
                # original physical cleanup still uses them.
                if service._close_task is not None:
                    await service.wait_closed()
            except BaseException as error:
                if primary is None:
                    raise
                primary.add_note('cleanup_join_failure=' + type(error).__name__ + ':' + str(error))
            finally:
                assert service._close_task.done()
                assert not service.runtime._tasks
                assert all(pool._closed for pool in service.runtime._pools.values())
                if stream is not None:
                    stream.cancel()
                    await asyncio.gather(stream, return_exceptions=True)
