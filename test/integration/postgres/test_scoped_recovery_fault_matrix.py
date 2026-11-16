"""Cross-boundary faults while another native Spot BUY remains unproved."""
import asyncio
import json
import time
from decimal import Decimal
from test.integration.postgres.test_automatic_recovery import executor_info
from test.integration.postgres.test_scoped_uncertain_recovery import (
    attached_core,
    base_owned as base_owned_fixture,
    clone_pending,
    isolate,
    managed_native as managed_native_fixture,
    owned as owned_fixture,
)
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import text

from hummingbot.connector.okx_order_status import OKXOrderNotFoundError
from hummingbot.core.data_type.common import OrderType, TradeType
from hummingbot.core.order_submission_guard import session_order_scope
from hummingbot.model.order import Order
from hummingbot.model.runtime_journal import RuntimeJournal, SubmissionAttempt
from hummingbot.model.trade_fill import TradeFill

base_owned = base_owned_fixture
owned = owned_fixture
managed_native = managed_native_fixture


def evidence():
    status = dict(clOrdId='auto-order', ordId='venue-auto', instId='BTC-USDT', side='buy', ordType='post_only',
                  px='100', sz='.1', accFillSz='.04', uTime='1700000001000', state='canceled')
    trade = dict(tradeId='late-fill', ordId='venue-auto', instId='BTC-USDT', side='buy',
                 fillSz='.04', fillPx='100', fee='-.01', feeCcy='USDT', ts='1700000001000')
    return status, trade


def proof_reads(exchange, status):
    async def read(order):
        if order.client_order_id != 'auto-order':
            raise OKXOrderNotFoundError('51603')
        return {'code': '0', 'data': [status]}
    return patch.object(exchange, '_request_order_update', AsyncMock(side_effect=read))


def assert_one_fill(context):
    with context.manager.get_new_session() as db:
        fill, = db.query(TradeFill).filter_by(order_id='auto-order').all()
        assert fill.amount == Decimal('.04') and fill.price == Decimal('100')
        assert fill.trade_fee['flat_fees'] == [{'token': 'USDT', 'amount': '0.01'}]
        assert db.query(RuntimeJournal).filter_by(kind='fill', order_id='auto-order').count() == 1


def test_simultaneous_native_sells_cannot_each_spend_the_same_unreserved_cash(managed_native):
    context, exchange, recorder, transport = managed_native
    isolate(context, exchange, recorder)
    response = transport.call.return_value
    response.status = 200
    body = {'code': '0', 'data': [{'clOrdId': 'first-sale', 'ordId': 'venue-sale', 'sCode': '0'}]}
    response.json = AsyncMock(return_value=body)
    response.text = AsyncMock(return_value=json.dumps(body))

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        posted = []

        async def delayed_post(request):
            posted.append(json.loads(request.data)['clOrdId'])
            entered.set()
            await release.wait()
            return response

        async def create(identity):
            config = executor_info(identity).config.model_copy(update={
                'side': TradeType.SELL, 'mm_session_id': 'mm-auto', 'mm_generation': 0,
                'level_id': 'sell_' + identity, 'amount': Decimal('.25')})
            with session_order_scope('mm-auto', 0, executor=config.model_dump(mode='json')):
                await exchange._create_order(TradeType.SELL, identity, 'BTC-USDT', Decimal('.25'),
                                             OrderType.LIMIT_MAKER, Decimal('100'))

        with patch.object(transport, 'call', AsyncMock(side_effect=delayed_post)):
            first = asyncio.create_task(create('first-sale'))
            try:
                await asyncio.wait_for(entered.wait(), 2)
                # Cash .5 - unknown BUY .1 - first possibly-sent SELL .25 = .15.
                await asyncio.wait_for(create('second-sale'), 2)
                assert posted == ['first-sale']
                with context.manager.get_new_session() as db:
                    assert db.get(SubmissionAttempt, 'first-sale').state == 'possibly_sent'
                    second = db.get(Order, 'second-sale')
                    assert second is None or not second.creation_pending
            finally:
                release.set()
                await asyncio.wait_for(first, 2)
    context.loop.run_until_complete(scenario())
    with context.manager.get_new_session() as db:
        assert db.get(Order, 'first-sale').exchange_order_id == 'venue-sale'
        assert db.get(SubmissionAttempt, 'auto-order').state == 'uncertain'
    assert not context.store.read()['recovery_state'].get('halt')


def test_late_ws_fill_and_duplicate_rest_fill_release_only_the_proved_order(managed_native):
    context, exchange, recorder, transport = managed_native
    isolate(context, exchange, recorder)
    clone_pending(context, 'still-unknown')
    recovery = context.owner.automatic_recovery
    recovery._isolate(recovery._pending())
    core = attached_core(context, exchange, recorder)
    status, raw = evidence()

    async def scenario():
        for attempt in recovery._pending():
            recovery._restore_order(exchange, attempt)
        recovery.attach_quarantined_entries(core)
        order = exchange.in_flight_orders['auto-order']
        trade = exchange._trade_update_from_fill(order, raw)

        async def lagging_fills(*args, **kwargs):
            # Private WS arrives during an older REST snapshot; it is durable
            # evidence of a partial fill, not evidence of complete cancellation.
            exchange._order_tracker.process_trade_update(trade)
            return {'code': '0', 'data': []}

        with proof_reads(exchange, status), patch.object(exchange, '_request_order_fills', AsyncMock(side_effect=lagging_fills)):
            await recovery.step(core, force=True)
        assert set(recovery.quarantined) == {'auto-order', 'still-unknown'}
        assert Decimal(recovery.status['reserved_base']['BTC']) == Decimal('.2')
        with context.manager.get_new_session() as db:
            assert db.get(SubmissionAttempt, 'auto-order').state == 'uncertain'
        assert_one_fill(context)

        with proof_reads(exchange, status), patch.object(exchange, '_request_order_fills', AsyncMock(
                return_value={'code': '0', 'data': [raw, raw]})):
            await recovery.step(core, force=True)
            await recovery.step(core, force=True)
        assert set(recovery.quarantined) == {'still-unknown'}
        assert Decimal(recovery.status['reserved_base']['BTC']) == Decimal('.1')
        exchange._order_tracker.process_trade_update(trade)  # Duplicate private delivery after REST.
        exchange.account_balance_observations['USDT'] = {'cash_balance': Decimal('100'), 'observed_at': time.time()}
        for executor in list(core.strategy.executor_orchestrator.active_executors['maker']):
            await executor.control_task()
        for _ in range(2):
            core.strategy.executor_orchestrator._update_positions_from_done_executors()
        held, = core.strategy.executor_orchestrator.positions_held['maker']
        assert held.net_amount_base == Decimal('.04') and held.cum_fees_quote == Decimal('.01')
        core.strategy.buy.assert_not_called()
        core.strategy.sell.assert_not_called()
    context.loop.run_until_complete(scenario())
    assert_one_fill(context)
    assert transport.call.await_count == 1 and not context.store.read()['recovery_state'].get('halt')


def test_late_private_ack_waits_for_proof_and_does_not_release_a_different_unknown(managed_native):
    from hummingbot.core.data_type.in_flight_order import OrderState, OrderUpdate

    context, exchange, recorder, transport = managed_native
    isolate(context, exchange, recorder)
    clone_pending(context, 'still-unknown')
    recovery = context.owner.automatic_recovery
    recovery._isolate(recovery._pending())
    core = SimpleNamespace(markets_recorder=recorder, trade_fill_db=context.manager)

    async def scenario():
        update = OrderUpdate('BTC-USDT', 1700000001, OrderState.OPEN, 'auto-order', 'venue-auto')
        await exchange._order_tracker.process_order_update(update)
        await exchange._order_tracker.process_order_update(update)  # Duplicate WS ACK.
        with context.manager.get_new_session() as db:
            assert not db.get(Order, 'auto-order').creation_pending
            assert db.get(SubmissionAttempt, 'auto-order').state == 'uncertain'
        with patch.object(exchange, '_request_order_update', AsyncMock(side_effect=OKXOrderNotFoundError('51603'))):
            await recovery.step(core, force=True)
        # ACK identifies a venue order, but cannot prove its complete fill set.
        assert set(recovery.quarantined) == {'auto-order', 'still-unknown'}
        assert Decimal(recovery.status['reserved_base']['BTC']) == Decimal('.2')
        status, _ = evidence()
        status.update(state='live', accFillSz='0')
        with proof_reads(exchange, status), patch.object(exchange, '_request_order_fills', AsyncMock(
                return_value={'code': '0', 'data': []})):
            await recovery.step(core, force=True)
        assert set(recovery.quarantined) == {'still-unknown'}
        assert Decimal(recovery.status['reserved_base']['BTC']) == Decimal('.1')
        assert recovery.isolated and context.owner.allow_new_orders
    context.loop.run_until_complete(scenario())
    with context.manager.get_new_session() as db:
        assert db.get(SubmissionAttempt, 'still-unknown').state == 'uncertain'
        assert db.query(TradeFill).count() == 0
    assert transport.call.await_count == 1 and not context.store.read()['recovery_state'].get('halt')


@pytest.mark.parametrize('settled_before_error', [False, True], ids=['before-receipt', 'after-receipt'])
def test_retry_between_durable_fill_and_verification_receipt_does_not_apply_twice(managed_native, settled_before_error):
    context, exchange, recorder, transport = managed_native
    isolate(context, exchange, recorder)
    clone_pending(context, 'still-unknown')
    recovery = context.owner.automatic_recovery
    recovery._isolate(recovery._pending())
    core = SimpleNamespace(markets_recorder=recorder, trade_fill_db=context.manager)
    status, raw = evidence()
    record = recovery._record_verified

    def interrupted(*args):
        assert_one_fill(context)
        if settled_before_error:
            record(*args)
        raise IOError('Interrupted verification receipt boundary')

    with proof_reads(exchange, status), patch.object(exchange, '_request_order_fills', AsyncMock(
            return_value={'code': '0', 'data': [raw]})):
        with patch.object(recovery, '_record_verified', interrupted):
            context.loop.run_until_complete(recovery.step(core, force=True))
        with context.manager.get_new_session() as db:
            assert db.get(SubmissionAttempt, 'auto-order').state == ('terminal' if settled_before_error else 'uncertain')
            assert db.get(SubmissionAttempt, 'still-unknown').state == 'uncertain'
        assert set(recovery.quarantined) == ({'still-unknown'} if settled_before_error else {'auto-order', 'still-unknown'})
        context.loop.run_until_complete(recovery.step(core, force=True))
    assert_one_fill(context)
    assert set(recovery.quarantined) == {'still-unknown'}
    assert Decimal(recovery.status['reserved_base']['BTC']) == Decimal('.1')
    assert context.owner.allow_new_orders and transport.call.await_count == 1
    assert not context.store.read()['recovery_state'].get('halt')


@pytest.mark.parametrize('interruption', ['stop', 'risk', 'owner_loss', 'persistence'])
def test_delayed_scoped_proof_never_overrides_a_newer_authority_or_failed_database(managed_native, interruption):
    from hummingbot.core.bot_ownership import BotOwnership, OwnershipLost
    from hummingbot.core.data_type.in_flight_order import InFlightOrder
    from hummingbot.core.order_submission_guard import SubmissionBlocked

    context, exchange, recorder, transport = managed_native
    isolate(context, exchange, recorder)
    clone_pending(context, 'still-unknown')
    recovery = context.owner.automatic_recovery
    recovery._isolate(recovery._pending())
    core = SimpleNamespace(markets_recorder=recorder, trade_fill_db=context.manager)
    status, raw = evidence()
    successor = None

    async def scenario():
        nonlocal successor
        entered, finish = asyncio.Event(), asyncio.Event()

        async def delayed(order):
            if order.client_order_id != 'auto-order':
                raise OKXOrderNotFoundError('51603')
            entered.set()
            await finish.wait()
            return {'code': '0', 'data': [status]}

        with patch.object(exchange, '_request_order_update', AsyncMock(side_effect=delayed)), \
                patch.object(exchange, '_request_order_fills', AsyncMock(return_value={'code': '0', 'data': [raw]})):
            task = asyncio.create_task(recovery.step(core, force=True))
            try:
                await asyncio.wait_for(entered.wait(), 2)
                if interruption == 'stop':
                    with context.engine.begin() as db:
                        db.execute(text("UPDATE bot_state SET desired_state='stopped',control_version=control_version+1"))
                elif interruption == 'risk':
                    context.owner.require_recovery('drawdown_controller_limit')
                elif interruption == 'persistence':
                    context.manager.persistence_healthy = False
                else:
                    with context.engine.begin() as db:
                        db.execute(text('SELECT pg_terminate_backend(:pid)'), {'pid': context.owner.backend_pid})
                    successor = BotOwnership(context.engine, context.store.bot_id, scoped_recovery=True)
                    assert successor.try_acquire()
            finally:
                finish.set()
            if interruption in ('owner_loss', 'persistence'):
                with pytest.raises(OwnershipLost if interruption == 'owner_loss' else RuntimeError):
                    await task
            else:
                await task
        sale = InFlightOrder('sale', 'BTC-USDT', OrderType.LIMIT_MAKER, TradeType.SELL,
                             Decimal('.1'), time.time(), Decimal('100'))
        with pytest.raises((SubmissionBlocked, RuntimeError)):
            recovery.authorize_isolated_order(exchange, sale)

    try:
        context.loop.run_until_complete(scenario())
        with context.manager.get_new_session() as db:
            assert db.get(SubmissionAttempt, 'still-unknown').state == 'uncertain'
            if interruption in ('owner_loss', 'persistence'):
                assert db.get(SubmissionAttempt, 'auto-order').state == 'uncertain'
                assert db.query(TradeFill).count() == 0
        assert transport.call.await_count == 1
        if interruption == 'risk':
            assert context.store.read()['recovery_state']['reason'] == 'drawdown_controller_limit'
        if successor:
            assert context.store.read()['owner_uuid'] == successor.owner_uuid
    finally:
        context.manager.persistence_healthy = True
        if successor:
            successor.release()


@pytest.mark.parametrize('committed', [False, True], ids=['commit-not-delivered', 'commit-reply-lost'])
def test_physical_verification_commit_loss_preserves_the_other_unknown_and_closes_send_gate(pg_engine, committed):
    import os
    import subprocess
    import sys
    from pathlib import Path
    from test.integration.postgres.response_loss_proxy import response_loss_proxy

    async def scenario():
        async with response_loss_proxy(pg_engine.url) as proxy:
            process = subprocess.Popen([sys.executable, '-m', 'test.integration.postgres.scoped_commit_worker'],
                                       cwd=Path(__file__).resolve().parents[3], stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       env={**os.environ, 'HBOT_IO_TEST_URL': proxy.url.render_as_string(hide_password=False)})
            try:
                ready = await asyncio.wait_for(asyncio.to_thread(process.stdout.readline), 30)
                if ready != b'COMMIT_READY\n':
                    diagnostic = (await asyncio.to_thread(process.stderr.read)).decode(errors='replace')
                    pytest.fail(diagnostic.replace(str(pg_engine.url.password), '[redacted]')[-2000:])
                proxy.drop_responses, proxy.drop_requests = True, not committed
                process.stdin.write(b'go\n')
                process.stdin.flush()
                result = json.loads(await asyncio.wait_for(asyncio.to_thread(process.stdout.readline), 7))
                assert result == {'stage': 'uncertain', 'closed': True}
                proxy.drop_responses = proxy.drop_requests = False
                process.stdin.write(b'restored\n')
                process.stdin.flush()
                blocked = json.loads(await asyncio.wait_for(asyncio.to_thread(process.stdout.readline), 7))
                assert blocked == {'stage': 'blocked', 'blocked': True, 'posts': 1}
                assert await asyncio.to_thread(process.wait, timeout=5) == 0
            finally:
                if process.poll() is None:
                    process.kill()
                await asyncio.to_thread(process.wait, timeout=3)
                for stream in (process.stdin, process.stdout, process.stderr):
                    stream.close()
    asyncio.run(scenario())
    # Independent server-side truth: identical client errors, different receipts.
    with pg_engine.connect() as db:
        states = dict(db.execute(text('SELECT order_id,state FROM submission_attempts')).all())
        assert states == {'auto-order': 'terminal' if committed else 'uncertain', 'still-unknown': 'uncertain'}
        assert db.execute(text("SELECT count(*) FROM trade_fills WHERE order_id='auto-order'")).scalar_one() == 1
        assert db.execute(text("SELECT count(*) FROM runtime_journal WHERE kind='fill' AND NOT applied")).scalar_one() == 1
        assert db.execute(text("SELECT count(*) FROM admin_audit_events WHERE action='automatic_recovery_order_verified'")).scalar_one() == int(committed)
