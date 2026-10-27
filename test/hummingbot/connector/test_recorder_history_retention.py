from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from hummingbot.connector.markets_recorder import MarketsRecorder


@pytest.mark.parametrize('dialect,name,restores', [('postgresql', 'okx', False),
                                                   ('postgresql', 'okx_perpetual', False),
                                                   ('sqlite', 'okx', True),
                                                   ('postgresql', 'binance', True)])
def test_new_market_only_warms_history_caches_used_by_its_reconstruction_path(dialect, name, restores):
    recorder = MarketsRecorder.__new__(MarketsRecorder)
    recorder._sql_manager = SimpleNamespace(engine=SimpleNamespace(dialect=SimpleNamespace(name=dialect)))
    recorder._config_file_path = 'profile.yaml'
    recorder._markets = []
    recorder._event_pairs = []
    recorder.get_trades_for_config = MagicMock(return_value=[])
    recorder.get_orders_for_config_and_market = MagicMock(return_value=[])
    market = SimpleNamespace(name=name, display_name=name, add_trade_fills_from_market_recorder=MagicMock(),
                             add_exchange_order_ids_from_market_recorder=MagicMock())
    recorder.add_market(market)
    assert recorder._markets == [market]
    assert recorder.get_trades_for_config.call_count == int(restores)
    assert recorder.get_orders_for_config_and_market.call_count == int(restores)
    assert market.add_trade_fills_from_market_recorder.call_count == int(restores)
