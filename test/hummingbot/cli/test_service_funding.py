import asyncio
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hummingbot.cli.service import ServiceHost
from hummingbot.client.config.runtime_config import RuntimeSettings
from hummingbot.core.funding_sync import FundingReader


@pytest.mark.asyncio
async def test_credential_change_detaches_idle_funding_reader_and_cancels_old_batch():
    host = ServiceHost(RuntimeSettings.from_environment({'DATABASE_URL': 'postgresql://unused/test'}),
                       asyncio.Event(), MagicMock())
    host.funding.sync = SimpleNamespace(getter=object())
    host.funding.task = asyncio.create_task(asyncio.sleep(100))
    await asyncio.sleep(0)
    task = host.funding.task
    await host.funding.credentials_changed()
    assert task.cancelled() and host.funding.task is None and host.funding.sync is None
    assert not host.funding.configured


class Store:
    def __init__(self):
        self.state, self.rows = {}, []

    def scope(self):
        return {'BTC-USDT'}, 1799999998000

    def load(self):
        return copy.deepcopy(self.state)

    def commit(self, owner, generation, rows, checkpoint):
        self.rows.extend(rows)
        self.state = copy.deepcopy(checkpoint)


@pytest.mark.asyncio
@pytest.mark.parametrize('phase', ['running', 'stopped', 'expired', 'halted', 'spot_only'])
async def test_idle_funding_does_not_activate_or_mutate_trading(phase):
    settings = RuntimeSettings.from_environment({'DATABASE_URL': 'postgresql://unused/test'})
    host = ServiceHost(settings, asyncio.Event(), MagicMock())
    host.phase = phase
    host.running = phase in ("running", "spot_only")
    host.owner = SimpleNamespace(check_owner=MagicMock(return_value={'database_now': 1800000000}),
                                 owner_uuid='owner', generation=1, allow_new_orders=False)
    host.hb = SimpleNamespace(trading_core=SimpleNamespace(connector_manager=SimpleNamespace(connectors={})))
    host.funding.store = Store()
    host.funding.template_pairs = set()
    host.start_strategy = AsyncMock()
    connector = SimpleNamespace(trading_environment='demo', _api_get=AsyncMock(side_effect=[
        {'code': '0', 'data': [{'uid': 'account'}]},
        {'code': '0', 'data': [dict(billId='1', ts='1799999999000', instId='BTC-USDT-SWAP',
                                    type='8', subType='174', pnl='0.00000001', ccy='USDT')]}]))
    with patch.object(FundingReader, 'from_environment', return_value=FundingReader(connector)):
        await host.collect_funding()
    assert host.phase == phase and host.owner.allow_new_orders is False
    assert host.funding.state['covered_until_ms'] == 1800000000000
    assert len(host.funding.store.rows) == 1 and host.funding.error is None
    host.start_strategy.assert_not_awaited()
    assert connector._api_get.await_count == 2


@pytest.mark.asyncio
async def test_service_close_cancels_funding_observer_before_disposal():
    host = ServiceHost(RuntimeSettings.from_environment({'DATABASE_URL': 'postgresql://unused/test'}),
                       asyncio.Event(), MagicMock())
    stopped = asyncio.Event()

    async def observing():
        try:
            await asyncio.sleep(1000)
        finally:
            stopped.set()
    host.funding.task = asyncio.create_task(observing())
    await asyncio.sleep(0)
    host.engine = MagicMock()
    assert await host.close()
    assert stopped.is_set() and host.funding.task.cancelled()
    host.engine.dispose.assert_called_once()


@pytest.mark.asyncio
async def test_funding_scheduler_keeps_one_inflight_batch_even_after_poll_deadline():
    host = ServiceHost(RuntimeSettings.from_environment({'DATABASE_URL': 'postgresql://unused/test'}),
                       asyncio.Event(), MagicMock())
    host.hb = SimpleNamespace(trading_core=SimpleNamespace())
    waiting = asyncio.Event()

    async def collect(*args):
        await waiting.wait()
    host.funding.collect = AsyncMock(side_effect=collect)
    with patch('hummingbot.client.config.environment_credentials.configured_environment_connectors', return_value=['okx_perpetual']):
        host.poll_funding()
        task = host.funding.task
        await asyncio.sleep(0)
        host.funding.next_poll = 0
        host.poll_funding()
    assert host.funding.task is task
    host.funding.collect.assert_awaited_once()
    await host.funding.close()
    assert task.cancelled()


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', [OSError('temporary read error'), asyncio.CancelledError()])
async def test_idle_funding_retries_catalogue_after_failed_first_load(tmp_path, failure):
    host = ServiceHost(RuntimeSettings.from_environment({'DATABASE_URL': 'postgresql://unused/test'}),
                       asyncio.Event(), MagicMock())
    host.owner = SimpleNamespace(check_owner=MagicMock(return_value={'database_now': 1800000000}),
                                 owner_uuid='owner', generation=1, allow_new_orders=False)
    host.hb = SimpleNamespace(trading_core=SimpleNamespace(connector_manager=SimpleNamespace(connectors={})))
    host.funding.store = Store()
    host.funding.store.scope = lambda: (set(), 1799999998000)
    (tmp_path / 'life_mm_profiles.yaml').touch()
    catalogue = SimpleNamespace(markets={'perpetual': SimpleNamespace(trading_pair='BTC-USDT')})
    connector = SimpleNamespace(trading_environment='demo', _api_get=AsyncMock(side_effect=[
        {'code': '0', 'data': [{'uid': 'account'}]},
        {'code': '0', 'data': [dict(billId='1', ts='1799999999000', instId='BTC-USDT-SWAP',
                                    type='8', subType='174', pnl='0.00000001', ccy='USDT')]}]))
    with patch('hummingbot.client.settings.CONF_DIR_PATH', tmp_path), \
            patch('hummingbot.client.config.mm_profiles.MMProfilesConfig.load',
                  new=AsyncMock(side_effect=[failure, catalogue])) as load, \
            patch.object(FundingReader, 'from_environment', return_value=FundingReader(connector)) as reader:
        if isinstance(failure, asyncio.CancelledError):
            with pytest.raises(asyncio.CancelledError):
                await host.collect_funding()
        else:
            await host.collect_funding()
        reader.assert_not_called()
        await host.collect_funding()
    assert load.await_count == 2
    assert host.funding.template_pairs == {'BTC-USDT'}
    assert len(host.funding.store.rows) == 1 and host.funding.error is None
    assert host.owner.allow_new_orders is False
    assert host.hb.trading_core.connector_manager.connectors == {}


@pytest.mark.asyncio
@pytest.mark.parametrize('file_exists', [False, True])
async def test_idle_funding_empty_catalogue_remains_read_only(tmp_path, file_exists):
    host = ServiceHost(RuntimeSettings.from_environment({'DATABASE_URL': 'postgresql://unused/test'}),
                       asyncio.Event(), MagicMock())
    host.owner = SimpleNamespace(check_owner=MagicMock(return_value={'database_now': 1800000000}),
                                 owner_uuid='owner', generation=1, allow_new_orders=False)
    host.hb = SimpleNamespace(trading_core=SimpleNamespace(connector_manager=SimpleNamespace(connectors={})))
    host.funding.store = Store()
    host.funding.store.scope = lambda: (set(), None)
    if file_exists:
        (tmp_path / 'life_mm_profiles.yaml').touch()
    with patch('hummingbot.client.settings.CONF_DIR_PATH', tmp_path), \
            patch('hummingbot.client.config.mm_profiles.MMProfilesConfig.load',
                  new=AsyncMock(return_value=SimpleNamespace(markets={}))) as load, \
            patch.object(FundingReader, 'from_environment') as reader:
        await host.collect_funding()
        await host.collect_funding()
    assert load.await_count == int(file_exists)
    assert host.funding.template_pairs == set()
    reader.assert_not_called()
    assert host.hb.trading_core.connector_manager.connectors == {}
