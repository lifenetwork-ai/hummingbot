import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, Mock

import pytest

from hummingbot.connector.derivative.okx_perpetual.okx_perpetual_derivative import OkxPerpetualDerivative
from hummingbot.connector.exchange.okx.okx_exchange import OkxExchange


@pytest.mark.asyncio
@pytest.mark.parametrize('connector_type', [OkxExchange, OkxPerpetualDerivative])
@pytest.mark.parametrize('backend,trading,has_balances', [
    ('postgres', True, False), ('postgres', True, True), ('postgres', False, False), ('file', True, False),
])
async def test_managed_okx_starts_its_first_account_poll_without_waiting_for_the_clock(
        monkeypatch, connector_type, backend, trading, has_balances):
    monkeypatch.setenv('STORAGE_BACKEND', backend)
    monkeypatch.delenv('K_SERVICE', raising=False)
    keys = ('offline-key', 'offline-secret', 'offline-pass')
    config = dict(trading_pairs=['BTC-USDT'], trading_required=trading)
    connector = (OkxExchange(*keys, **config) if connector_type is OkxExchange else
                 OkxPerpetualDerivative(okx_perpetual_api_key=keys[0], okx_perpetual_secret_key=keys[1],
                                        okx_perpetual_passphrase=keys[2], **config))
    connector._set_current_timestamp(101)
    if has_balances:
        connector._account_balances['USDT'] = Decimal('1')

    monkeypatch.setattr(connector.order_book_tracker, 'start', Mock())
    monkeypatch.setattr(connector, '_create_user_stream_tracker_task', Mock(return_value=None))
    for method in ('_trading_rules_polling_loop', '_trading_fees_polling_loop', '_user_stream_event_listener',
                   '_lost_orders_update_polling_loop', '_update_time_synchronizer', '_status_polling_loop_fetch_updates'):
        monkeypatch.setattr(connector, method, AsyncMock())
    if connector_type is OkxPerpetualDerivative:
        for method in ('_listen_for_funding_info', '_initialize_position_mode', '_funding_payment_polling_loop'):
            monkeypatch.setattr(connector, method, AsyncMock())

    await connector.start_network()
    try:
        # The real polling worker gets a loop turn while the first clock tick
        # is still withheld. A tick within the same interval adds no duplicate.
        await asyncio.sleep(0)
        eager = backend == 'postgres' and trading and not has_balances
        poll = connector._status_polling_loop_fetch_updates
        assert poll.await_count == int(eager)
        if eager:
            connector.tick(102)
            await asyncio.sleep(0)
            poll.assert_awaited_once()
            connector.tick(105)
            await asyncio.sleep(0)
            assert poll.await_count == 2
    finally:
        worker = connector._status_polling_task
        await connector.stop_network()
        if worker is not None:
            await asyncio.gather(worker, return_exceptions=True)
