"""Optional publication work cannot strand an acknowledged exchange order."""
import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from test.integration.postgres.test_ownership import intent, owned as _owned
from test.integration.postgres.test_service_availability import publication as _publication

import pytest
from sqlalchemy import create_engine, event, func, select, text, update
from sqlalchemy.exc import OperationalError

from hummingbot.core.data_type.common import OrderType
from hummingbot.core.event.events import BuyOrderCreatedEvent, MarketEvent
from hummingbot.model.order import Order

owned = _owned
publication = _publication


def test_positive_venue_receipt_survives_create_ack_fence_timeout(owned):
    """An accepted exchange order must not become an assumed local failure.

    Reproduce the observed boundary: send receipt committed, native create ACK
    arrives, then the recorder cannot obtain its financial ownership fence.
    Removing the blocker restores SQL access but cannot repair the lost ACK.
    """
    from hummingbot.model.runtime_journal import SubmissionAttempt
    from hummingbot.model.trade_fill import TradeFill

    order = intent(owned)
    receipt = {'code': '0', 'http_status': 200,
               'order': {'ordId': 'accepted-venue-order', 'clOrdId': order.client_order_id, 'sCode': '0'}}
    with owned.engine.begin() as connection:
        assert connection.execute(update(SubmissionAttempt).where(
            SubmissionAttempt.order_id == order.client_order_id).values(
                state='possibly_sent', receipt=receipt)).rowcount == 1
    ack = BuyOrderCreatedEvent(timestamp=1700000001, creation_timestamp=order.creation_timestamp,
                               type=OrderType.LIMIT, trading_pair=order.trading_pair, amount=order.amount,
                               price=order.price, order_id=order.client_order_id,
                               exchange_order_id='accepted-venue-order')
    with owned.engine.begin() as blocker:
        blocker.execute(text('SELECT bot_id FROM bot_state FOR UPDATE')).all()
        with pytest.raises(OperationalError) as error:
            owned.recorder._did_create_order(MarketEvent.BuyOrderCreated.value, owned.market, ack)
        assert error.value.orig.pgcode == '55P03'
    assert not owned.manager.persistence_healthy
    # A successful ping after the blocking transaction ends is not proof that
    # the ACK was stored, nor permission to retry an exchange POST.
    with owned.engine.connect() as connection:
        assert connection.scalar(text('SELECT 1')) == 1
        saved = connection.execute(select(*Order.__table__.c).where(Order.id == order.client_order_id)).mappings().one()
        attempt = connection.execute(select(*SubmissionAttempt.__table__.c).where(
            SubmissionAttempt.order_id == order.client_order_id)).mappings().one()
        assert saved['creation_pending'] and saved['exchange_order_id'] is None
        assert attempt['state'] == 'possibly_sent' and attempt['receipt'] == receipt
        assert connection.scalar(select(func.count()).select_from(TradeFill)) == 0
    with pytest.raises(RuntimeError, match='reconciliation'):
        owned.manager.assert_persistence_healthy()


def publish(owned, status=None):
    return owned.store.publish(owner_uuid=owned.owner.owner_uuid, owner_generation=owned.owner.generation,
                               version=1, status=status if status is not None else {'running': True})


def test_native_recorder_does_not_retry_while_its_own_publication_commits(owned):
    order = intent(owned)
    published, release, staged = threading.Event(), threading.Event(), threading.Event()
    fences = []

    def hold_publication(connection, cursor, statement, parameters, context, executemany):
        if context.execution_options.get('ownership_row_writer') == 'publication':
            published.set()
            assert release.wait(3)

    def count_fences(connection, cursor, statement, parameters, context, executemany):
        if 'FROM bot_state' in statement and 'FOR SHARE NOWAIT' in statement:
            fences.append(statement)

    def write(session):
        staged.set()
        return session.execute(update(Order).where(Order.id == order.client_order_id).values(price=order.price)).rowcount

    async def exercise():
        owner = owned.owner
        status = asyncio.create_task(asyncio.to_thread(
            owner.store.publish, owner_uuid=owner.owner_uuid, owner_generation=owner.generation,
            version=1, status={'running': True}, save_balances=False))
        try:
            assert await asyncio.to_thread(published.wait, 2)
            recorder = asyncio.create_task(owned.recorder._run_native_write(write))
            assert await asyncio.to_thread(staged.wait, 2)
            try:
                # The preceding UPDATE holds its row until COMMIT. The recorder
                # can stage concurrently but must not issue a doomed NOWAIT.
                await asyncio.sleep(.03)
                assert not fences
                assert not recorder.done()
            finally:
                release.set()
                assert await status
                assert await recorder == (1, None)
        finally:
            release.set()
            await status

    event.listen(owned.engine, 'after_cursor_execute', hold_publication)
    event.listen(owned.engine, 'before_cursor_execute', count_fences)
    try:
        owned.loop.run_until_complete(exercise())
    finally:
        event.remove(owned.engine, 'before_cursor_execute', count_fences)
        event.remove(owned.engine, 'after_cursor_execute', hold_publication)
    assert len(fences) == 1 and owned.manager.persistence_healthy


@pytest.mark.parametrize('stage', ['daily', 'history', 'receipt'])
def test_slow_publication_projection_does_not_fail_real_create_ack(owned, monkeypatch, stage):
    from hummingbot.model import admin_reporting, mm_event
    from hummingbot.model.admin_store import AdminStore

    original = {'daily': admin_reporting.save_daily_balances, 'history': mm_event.record_guards,
                'receipt': AdminStore.record_observation}[stage]
    entered, release = threading.Event(), threading.Event()

    def delayed(*args, **kwargs):
        entered.set()
        assert release.wait(5), 'Test did not release the delayed projection'
        return original(*args, **kwargs)

    target, attribute = {'daily': (admin_reporting, 'save_daily_balances'),
                         'history': (mm_event, 'record_guards'),
                         'receipt': (AdminStore, 'record_observation')}[stage]
    monkeypatch.setattr(target, attribute, delayed)
    from hummingbot.model.bot_state import BotState
    from hummingbot.model.mm_event import MMEvent
    with owned.engine.begin() as connection:
        connection.execute(update(BotState).where(BotState.bot_id == owned.store.bot_id).values(mm_session=dict(
            id='active-mm', generation=0, profile_id='follow_market', status='running')))
    status = dict(running=True, mm_session=dict(id='active-mm', generation=0),
                  mm_markets=[dict(market='spot', ready=True)])
    order = intent(owned)
    ack = BuyOrderCreatedEvent(timestamp=1700000001, creation_timestamp=order.creation_timestamp,
                               type=OrderType.LIMIT, trading_pair=order.trading_pair, amount=order.amount,
                               price=order.price, order_id=order.client_order_id, exchange_order_id='venue-ack')
    with ThreadPoolExecutor(max_workers=1) as work:
        task = work.submit(publish, owned, status)
        try:
            assert entered.wait(3)
            owned.recorder._did_create_order(MarketEvent.BuyOrderCreated.value, owned.market, ack)
            assert owned.manager.persistence_healthy
            with owned.manager.get_new_session() as session:
                saved = session.get(Order, order.client_order_id)
                assert saved.exchange_order_id == 'venue-ack' and not saved.creation_pending
        finally:
            release.set()
            task.result(timeout=5)
    with owned.engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(MMEvent)) == 1


def test_publication_yields_to_an_existing_financial_fence_without_queueing(owned):
    before = owned.store.read()
    with owned.engine.begin() as financial, ThreadPoolExecutor(max_workers=1) as work:
        financial.execute(text('SELECT bot_id FROM bot_state FOR SHARE')).all()
        # This must return without joining the exclusive-lock wait queue. Such
        # a waiter can otherwise hold up a second compatible financial reader.
        assert work.submit(publish, owned).result(timeout=3) is False
        with owned.manager.get_new_session() as session, session.begin():
            owned.owner.fence_session(session)
            assert session.execute(select(Order.id)).all() == []
        assert owned.manager.persistence_healthy
        row = owned.store.read()
        assert (row['heartbeat'], row['status'], row['acknowledged_version']) == (
            before['heartbeat'], before['status'], before['acknowledged_version'])
    assert publish(owned)


@pytest.mark.parametrize('drift', ['control', 'owner', 'guard', 'session', 'halt'])
def test_speculative_balance_write_rolls_back_when_final_basis_changes(owned, monkeypatch, drift):
    from hummingbot.model.admin_reporting import save_daily_balances
    from hummingbot.model.bot_state import BotState
    from hummingbot.model.daily_balance_snapshot import DailyBalanceSnapshot

    entered, release = threading.Event(), threading.Event()
    status = dict(running=True, reporting_timezone='UTC', reporting={'accounts': [dict(
        identity_verified=True, account_id='verified-account', environment='demo', balances=[dict(
            currency='USDT', connector='okx', source='okx_account_balance', cash_balance='100',
            observed_at=datetime.now(timezone.utc).timestamp())])]})

    def write_then_pause(*args, **kwargs):
        count = save_daily_balances(*args, **kwargs)
        assert count == 1
        entered.set()
        assert release.wait(5)
        return count

    monkeypatch.setattr('hummingbot.model.admin_reporting.save_daily_balances', write_then_pause)
    before = owned.store.read()
    with ThreadPoolExecutor(max_workers=1) as work:
        task = work.submit(publish, owned, status)
        try:
            assert entered.wait(3)
            if drift == 'control':
                owned.store.control(expected_version=1, desired_state='stopped')
            elif drift == 'halt':
                owned.owner.require_recovery('drawdown_controller_limit')
            else:
                values = {'owner': dict(owner_uuid='replacement', owner_generation=owned.owner.generation + 1),
                          'guard': dict(status={'mm_guard_observation': {'sequence': 42}}),
                          'session': dict(mm_session={'id': 'replaced-session', 'generation': 1})}[drift]
                with owned.engine.begin() as connection:
                    connection.execute(update(BotState).where(BotState.bot_id == owned.store.bot_id).values(**values))
        finally:
            release.set()
        assert task.result(timeout=5) is False
    after = owned.store.read()
    assert after['acknowledged_version'] == before['acknowledged_version']
    assert after['heartbeat'] == before['heartbeat']
    with owned.engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(DailyBalanceSnapshot)) == 0
    assert after['status'] == ({'mm_guard_observation': {'sequence': 42}} if drift == 'guard' else before['status'])
    if drift == 'halt':
        assert after['recovery_state']['halt'] is True


@pytest.mark.parametrize('change', ['busy', 'stop'])
def test_speculative_receipt_and_balances_never_escape_a_rejected_publication(publication, monkeypatch, change):
    from hummingbot.model.admin_reporting import save_daily_balances
    from hummingbot.model.admin_store import AdminStore
    from hummingbot.model.daily_balance_snapshot import DailyBalanceSnapshot

    store, owner, status = publication
    status.update(reporting_timezone='UTC', reporting={'accounts': [dict(
        identity_verified=True, account_id='verified-account', environment='demo', balances=[dict(
            currency='USDT', connector='okx', source='okx_account_balance', cash_balance='100',
            observed_at=datetime.now(timezone.utc).timestamp())])]})
    entered, release = threading.Event(), threading.Event()

    def write_then_pause(*args, **kwargs):
        result = save_daily_balances(*args, **kwargs)
        assert result == 1
        entered.set()
        assert release.wait(5)
        return result

    monkeypatch.setattr('hummingbot.model.admin_reporting.save_daily_balances', write_then_pause)
    before = store.read()
    with ThreadPoolExecutor(max_workers=1) as work:
        task = work.submit(store.publish, owner_uuid=owner.owner_uuid, owner_generation=owner.generation,
                           version=1, status=status)
        try:
            assert entered.wait(3)
            if change == 'busy':
                with store.engine.begin() as financial:
                    financial.execute(text('SELECT bot_id FROM bot_state FOR SHARE')).all()
                    release.set()
                    assert task.result(timeout=3) is False
            else:
                store.control(expected_version=1, desired_state='stopped')
                release.set()
                assert task.result(timeout=3) is False
        finally:
            release.set()
            task.result(timeout=5)
    row = store.read()
    assert (row['acknowledged_version'], row['status'], row['heartbeat']) == (
        before['acknowledged_version'], before['status'], before['heartbeat'])
    assert AdminStore(store).command_by_id('stop-command')['status'] == 'accepted'
    with store.engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(DailyBalanceSnapshot)) == 0


def test_large_session_is_decoded_once_before_locking_recorder_authority(owned):
    import json

    from hummingbot.model.bot_state import BotState
    from hummingbot.model.bot_state_store import BotStateStore

    entered, release = threading.Event(), threading.Event()
    large = dict(id='mm-session', generation=0, profile_id='follow_market', catalogue={'retained': 'x' * 700_000})
    with owned.engine.begin() as connection:
        connection.execute(update(BotState).where(BotState.bot_id == owned.store.bot_id).values(mm_session=large))
    large_decodes = []

    def decode(raw):
        if len(raw) > 700_000:
            large_decodes.append(len(raw))
            if len(large_decodes) == 1:
                entered.set()
                assert release.wait(5)
        return json.loads(raw)

    engine = create_engine(owned.engine.url, json_deserializer=decode)
    try:
        with ThreadPoolExecutor(max_workers=1) as work:
            task = work.submit(BotStateStore(engine, owned.store.bot_id).publish,
                               owner_uuid=owned.owner.owner_uuid, owner_generation=owned.owner.generation,
                               version=1, status={'running': True}, save_balances=False)
            try:
                assert entered.wait(3)
                with owned.manager.get_new_session() as session, session.begin():
                    owned.owner.fence_session(session)
            finally:
                release.set()
            assert task.result(timeout=5)
        assert len(large_decodes) == 1
        assert owned.store.read()['mm_session'] == large
    finally:
        release.set()
        engine.dispose()


def test_captured_running_snapshot_cannot_publish_after_halt_before_worker_starts(owned):
    captured = {'running': True, 'phase': 'running'}
    owned.owner.require_recovery('drawdown_controller_limit')
    before = owned.store.read()
    assert publish(owned, captured) is False
    after = owned.store.read()
    assert (after['status'], after['heartbeat'], after['acknowledged_version']) == (
        before['status'], before['heartbeat'], before['acknowledged_version'])
    # HALT diagnostics still publish normally; only the old running observation
    # is rejected, without changing the risk flag or stopping recovery reporting.
    assert publish(owned, {'running': False, 'phase': 'halted'})
    assert owned.store.read()['recovery_state']['halt'] is True
