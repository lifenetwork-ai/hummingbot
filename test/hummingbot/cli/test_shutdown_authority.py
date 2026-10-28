"""Process shutdown closes new risk before optional cleanup yields."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from hummingbot.cli.service import ServiceHost
from hummingbot.client.config.runtime_config import RuntimeSettings
from hummingbot.core.bot_ownership import ACCOUNT_PATHS, CANCEL_PATHS, ORDER_PATH, BotOwnership
from hummingbot.core.order_submission_guard import SubmissionBlocked


def service(monkeypatch):
    from hummingbot.connector.markets_recorder import MarketsRecorder
    item = ServiceHost(RuntimeSettings.from_environment({'DATABASE_URL': 'postgresql://unused/test'}),
                       asyncio.Event(), MagicMock())
    owner = BotOwnership(MagicMock(), 'hummingbot')
    row = dict(bot_id='hummingbot', owner_uuid=owner.owner_uuid, owner_generation=1, backend_pid=31,
               desired_state='running', control_version=1, config_file_path='controller.yml',
               config_type='controller', recovery_state={})
    owner.connection = MagicMock()
    owner.connection.closed = owner.connection.invalidated = False
    owner.connection.execute.return_value.mappings.return_value.first.return_value = row
    owner.backend_pid, owner.version, owner.config_file_path = 31, 1, 'controller.yml'
    owner.allow_new_orders = owner.reconciling = True
    owner._check_intents = MagicMock()
    owner.active_intents.add('queued')
    recorder = MagicMock()
    recorder.sql_manager.engine = owner.engine
    monkeypatch.setattr(MarketsRecorder, '_shared_instance', recorder)
    item.owner, item.engine = owner, owner.engine
    item.hb = SimpleNamespace(trading_core=SimpleNamespace(connector_manager=SimpleNamespace(connectors={}), strategy=None))
    item.stop_core = AsyncMock(return_value=True)
    item.snapshot = lambda: {}
    owner.store.publish = MagicMock(return_value=True)
    owner.release = MagicMock()
    return item, owner


@pytest.mark.asyncio
@pytest.mark.parametrize('path', [ORDER_PATH, *sorted(ACCOUNT_PATHS)])
@pytest.mark.parametrize('signal_requested', [True, False])
async def test_shutdown_revokes_order_and_setup_before_optional_cleanup(monkeypatch, path, signal_requested):
    item, owner = service(monkeypatch)
    entered, release = asyncio.Event(), asyncio.Event()

    async def optional_cleanup():
        entered.set()
        await release.wait()

    item.funding.close = optional_cleanup
    if signal_requested:
        item.stop.set()
    closing = asyncio.create_task(item.close())
    try:
        await asyncio.wait_for(entered.wait(), 1)
        assert not owner.allow_new_orders and not owner.reconciling
        with pytest.raises(SubmissionBlocked, match='shutdown'):
            await owner.authorize_rest(SimpleNamespace(url='https://www.okx.com' + path,
                                                       data=json.dumps({'clOrdId': 'queued'}), headers={}))
        assert not owner.account_lock.locked()
        assert not owner.sent_intents
        for cancel in CANCEL_PATHS:
            assert await owner.authorize_rest(SimpleNamespace(url='https://www.okx.com' + cancel)) is None
        assert item.stop_core.await_count == 0
    finally:
        release.set()
        await closing


@pytest.mark.asyncio
async def test_signal_blocks_order_even_before_close_and_after_inflight_db_check(monkeypatch):
    item, owner = service(monkeypatch)
    # Same wiring used when constructing the process owner.
    owner.stopping = item.stop.is_set
    owner.connection.commit.side_effect = item.stop.set
    with pytest.raises(SubmissionBlocked, match='shutdown'):
        await owner.authorize_rest(SimpleNamespace(url='https://www.okx.com' + ORDER_PATH,
                                                   data=json.dumps({'clOrdId': 'queued'}), headers={}))
    assert not owner.sent_intents
    assert not item.runtime_healthy()
