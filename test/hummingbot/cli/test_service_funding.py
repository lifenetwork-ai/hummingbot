import asyncio
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hummingbot.cli.service import ServiceHost
from hummingbot.client.config.runtime_config import RuntimeSettings
from hummingbot.core.funding_sync import FundingReader


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
    host.funding_store = Store()
    host._funding_template_pairs = set()
    host.start_strategy = AsyncMock()
    connector = SimpleNamespace(trading_environment='demo', _api_get=AsyncMock(side_effect=[
        {'code': '0', 'data': [{'uid': 'account'}]},
        {'code': '0', 'data': [dict(billId='1', ts='1799999999000', instId='BTC-USDT-SWAP',
                                    type='8', subType='174', pnl='0.00000001', ccy='USDT')]}]))
    with patch.object(FundingReader, 'from_environment', return_value=FundingReader(connector)):
        await host.collect_funding()
    assert host.phase == phase and host.owner.allow_new_orders is False
    assert host.funding_state['covered_until_ms'] == 1800000000000
    assert len(host.funding_store.rows) == 1 and host.funding_error is None
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
    host.funding_task = asyncio.create_task(observing())
    await asyncio.sleep(0)
    host.engine = MagicMock()
    assert await host.close()
    assert stopped.is_set() and host.funding_task.cancelled()
    host.engine.dispose.assert_called_once()
