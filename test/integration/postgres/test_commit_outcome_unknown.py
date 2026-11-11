"""Equal client errors must not imply equal server-side COMMIT outcomes."""
import asyncio
import json
import os
import subprocess
import sys
from decimal import Decimal
from pathlib import Path
from test.integration.postgres.response_loss_proxy import response_loss_proxy
from test.integration.postgres.test_cleanup_fill_checkpoint import prepare
from test.integration.postgres.test_recorder import fill, recording as recording_fixture

import pytest

from hummingbot.core.bot_ownership import BotOwnership
from hummingbot.core.data_type.common import OrderType, PositionAction, TradeType
from hummingbot.core.event.events import MarketEvent
from hummingbot.core.order_submission_guard import SubmissionBlocked
from hummingbot.model.market_state import MarketState
from hummingbot.model.order import Order
from hummingbot.model.order_status import OrderStatus
from hummingbot.model.position import Position
from hummingbot.model.trade_fill import TradeFill

recording = recording_fixture


@pytest.mark.parametrize('committed', [False, True], ids=['commit-not-delivered', 'commit-reply-lost'])
def test_position_executor_and_application_receipt_share_the_unknown_commit_outcome(recording, committed):
    from hummingbot.model.executors import Executors
    from hummingbot.model.runtime_journal import RuntimeJournal
    recorder, manager, market = recording
    asyncio.run(cut_commit(manager.engine, 'hold', committed))
    with manager.get_new_session() as session:
        assert session.query(Position).count() == int(committed)
        assert session.query(Executors).count() == int(committed)
        assert session.query(RuntimeJournal).filter_by(event_key='hold:held').count() == int(committed)
        assert session.query(RuntimeJournal).filter_by(event_key='applied-order:held-order').count() == int(committed)


async def cut_commit(engine, mode, committed):
    async with response_loss_proxy(engine.url) as proxy:
        process = subprocess.Popen(
            [sys.executable, '-m', 'test.integration.postgres.commit_outcome_worker', mode],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=Path(__file__).resolve().parents[3],
            env={**os.environ, 'HBOT_IO_TEST_URL': proxy.url.render_as_string(hide_password=False)})
        try:
            ready = await asyncio.wait_for(asyncio.to_thread(process.stdout.readline), 15)
            if ready != b'COMMIT_READY\n':
                diagnostic = (await asyncio.to_thread(process.stderr.read)).decode(errors='replace')
                diagnostic = diagnostic.replace(str(engine.url.password), '[redacted]')
                pytest.fail('Child did not reach COMMIT: ' + diagnostic[-2500:])
            proxy.drop_responses = True
            proxy.drop_requests = not committed
            process.stdin.write(b'go\n')
            process.stdin.flush()
            uncertain = json.loads(await asyncio.wait_for(asyncio.to_thread(process.stdout.readline), 7))
            assert uncertain['stage'] == 'uncertain' and uncertain['closed']
            assert not uncertain['persistence_healthy'] and not uncertain['acknowledged']
            proxy.drop_responses = proxy.drop_requests = False
            process.stdin.write(b'restored\n')
            process.stdin.flush()
            blocked = json.loads(await asyncio.wait_for(asyncio.to_thread(process.stdout.readline), 7))
            assert blocked == dict(stage='blocked', blocked=True, transport_calls=0, acknowledged=False)
            assert await asyncio.to_thread(process.wait, timeout=5) == 0
        finally:
            if process.poll() is None:
                process.kill()
            await asyncio.to_thread(process.wait, timeout=3)
            for stream in (process.stdin, process.stdout, process.stderr):
                stream.close()


@pytest.mark.parametrize('committed', [False, True], ids=['commit-not-delivered', 'commit-reply-lost'])
@pytest.mark.parametrize('mode', ['intent', 'fill', 'cleanup'])
def test_network_loss_at_commit_keeps_gate_closed_and_restart_reads_durable_truth(recording, mode, committed):
    recorder, manager, market = prepare(recording) if mode == 'cleanup' else recording
    if mode == 'cleanup':
        from hummingbot.model.runtime_journal import RuntimeCheckpoint, financial_hash
        with manager.get_new_session() as session, session.begin():
            session.add(RuntimeCheckpoint(config_file_path='life_mm_profiles.yaml', financial_hash=financial_hash(session),
                                          bindings_hash='keep-account-binding', watermark=0, payload={}))
    asyncio.run(cut_commit(manager.engine, mode, committed))
    # This manager/recorder is in the parent process: no child-side session/cache
    # or success acknowledgement can influence its independent PostgreSQL read.
    with manager.get_new_session() as session:
        assert session.query(TradeFill).count() == (int(committed) if mode != 'intent' else 0)
        if mode == 'cleanup':
            position = session.get(Position, 'long-hold')
            assert position.amount == (Decimal('.2') if committed else Decimal('.3'))
            assert position.realized_pnl_quote == (Decimal('1') if committed else 0)
            checkpoint = session.get(RuntimeCheckpoint, 'life_mm_profiles.yaml')
            assert checkpoint.financial_hash == financial_hash(session)
            assert bool(checkpoint.watermark) is committed and checkpoint.bindings_hash == 'keep-account-binding'
        else:
            for model in (Order, OrderStatus, MarketState):
                assert session.query(model).count() == int(committed)
    if mode == 'intent':
        successor = BotOwnership(manager.engine, 'hummingbot')
        assert successor.try_acquire()
        try:
            assert successor.check_owner()['recovery_state']['halt']
            with pytest.raises(SubmissionBlocked):
                successor.check_owner(new_risk=True)
            with manager.get_new_session() as session:
                order = session.get(Order, 'order-1')
                assert (order is not None) is committed
                if order is not None:
                    assert order.creation_pending and order.exchange_order_id is None
        finally:
            successor.release()
        return
    evt = (fill(trade_type=TradeType.SELL, order_type=OrderType.MARKET, amount=Decimal('.1'),
                price=Decimal('100'), position=PositionAction.CLOSE.value) if mode == 'cleanup' else fill())
    # Replay the same authoritative trade twice after a fresh-process read. Both
    # possible COMMIT outcomes must converge without charging/closing twice.
    for _ in range(2):
        recorder._did_fill_order(MarketEvent.OrderFilled.value, market, evt)
    with manager.get_new_session() as session:
        assert session.query(TradeFill).count() == 1
        assert session.query(OrderStatus).filter_by(status='OrderFilled').count() == 1
        trade = session.query(TradeFill).one()
        assert trade.amount == evt.amount and trade.price == evt.price
        assert trade.trade_fee_in_quote == Decimal('.2')
        assert trade.trade_fee == evt.trade_fee.to_json()
        if mode == 'cleanup':
            position = session.get(Position, 'long-hold')
            assert position.amount == Decimal('.2') and position.realized_pnl_quote == 1
            assert position.cum_fees_quote == Decimal('.2') and position.volume_traded_quote == 37
