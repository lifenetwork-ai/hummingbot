import asyncio
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from hummingbot.cli.credential_cleanup_runtime import CleanupExchangeRuntime
from hummingbot.connector.credential_adapters import CredentialAdapterError


def runtime():
    connector = SimpleNamespace(name="okx_perpetual", client_order_id_prefix="HB", in_flight_orders={},
                                trading_rules={"BTC-USDT": SimpleNamespace(min_order_size=Decimal("0.001"), min_notional_size=Decimal(0))},
                                quantize_order_amount=lambda **kwargs: kwargs["amount"], cancel_all=AsyncMock(),
                                _api_get=AsyncMock(return_value={"code": "0", "data": [{"instId": "BTC-USDT-SWAP", "last": "100"}]}))
    core = SimpleNamespace(connector_manager=SimpleNamespace(connectors={"okx_perpetual": connector}),
                           trade_fill_db=SimpleNamespace(assert_persistence_healthy=Mock()))
    owner = SimpleNamespace(owner_uuid="owner", generation=1, _open_orders=AsyncMock(return_value=[]), _refresh_orders=AsyncMock(),
                            check_owner=Mock(return_value=dict(database_now=1000, control_version=2)), connectors=core.connector_manager.connectors)
    host = SimpleNamespace(hb=SimpleNamespace(trading_core=core), owner=owner)
    store = SimpleNamespace()
    instance = CleanupExchangeRuntime(host, store)
    instance.initialized = True
    return instance, connector


@pytest.mark.asyncio
async def test_prepared_cleanup_publishes_native_engine_status_without_a_strategy(monkeypatch):
    from hummingbot.cli.credential_cleanup_runtime import CLEANUP_CONFIG
    from hummingbot.cli.service_availability import ServiceAvailability
    from hummingbot.cli.service_observations import ServiceObservations
    from hummingbot.client.config.client_config_map import ClientConfigMap
    from hummingbot.core.data_type.common import PositionMode
    from hummingbot.core.trading_core import TradingCore

    core = TradingCore(ClientConfigMap(), enable_gateway_monitor=False)
    connector = SimpleNamespace(
        trading_pair_symbol_map_ready=lambda: True, trading_rules={}, get_all_balances=lambda: {},
        _fetch_account_position_mode=AsyncMock(return_value=PositionMode.HEDGE),
        _perpetual_trading=SimpleNamespace(set_position_mode=Mock()))
    core.connector_manager = SimpleNamespace(connectors={'okx_perpetual': connector}, get_status=lambda: {})
    monkeypatch.setattr('hummingbot.core.trading_core.SQLConnectionManager.get_trade_fills_instance', Mock())
    recorder = Mock()
    monkeypatch.setattr('hummingbot.core.trading_core.MarketsRecorder', recorder)
    observations, availability = ServiceObservations(), ServiceAvailability()
    captured = []

    async def initialize_markets(markets):
        core.initialize_markets_recorder()
        captured.append(observations.capture(core, owner, availability))

    async def start_clock():
        core._is_running = True
        return True

    core.initialize_markets, core.start_clock = initialize_markets, start_clock
    owner = SimpleNamespace(owner_uuid='owner', generation=1, _mutate_recovery=Mock())
    host = SimpleNamespace(hb=SimpleNamespace(trading_core=core), owner=owner,
                           stop_core=AsyncMock(return_value=True), detach_credential_readers=AsyncMock(),
                           managed_credentials=SimpleNamespace(reload_cleanup=AsyncMock(return_value={'okx_perpetual': 3})))
    store = SimpleNamespace(disconnect_snapshot=Mock(return_value={
        'state': {'recovery_state': {}, 'control_version': 7},
        'positions': [], 'orders': [], 'tracking_states': []}))
    instance = CleanupExchangeRuntime(host, store)
    await instance.prepare({'id': 'disconnect'})
    captured.append(observations.capture(core, owner, availability))

    assert availability.snapshot()['incidents'] == []
    for value in captured:
        assert not value['reporting_unavailable']
        assert value['engine']['strategy_name'] is None
        assert value['engine']['strategy_type'] is None
        assert value['engine']['strategy_file_name'] == CLEANUP_CONFIG
        assert value['engine']['strategy_running'] is False
        assert value['engine']['markets_recorder_active'] is True
    assert captured[-1]['engine']['clock_running'] is True
    assert core.strategy is None and instance.initialized
    assert owner.allow_new_orders is False and owner.cleanup_operation_id == 'disconnect'
    assert core.markets_recorder.cleanup_operation_id == 'disconnect'
    # Existing recorder fallback retains the cleanup configuration identity;
    # there is no executable strategy and no new recorder/identity mechanism.
    assert recorder.call_args.args[2:4] == (CLEANUP_CONFIG, CLEANUP_CONFIG)


def test_native_engine_status_still_rejects_an_unknown_strategy():
    from hummingbot.client.config.client_config_map import ClientConfigMap
    from hummingbot.core.trading_core import TradingCore

    core = TradingCore(ClientConfigMap(), enable_gateway_monitor=False)
    core.strategy_name = 'missing_cleanup_review_strategy'
    with pytest.raises(ValueError, match='Unknown strategy: missing_cleanup_review_strategy'):
        core.get_status()


@pytest.mark.asyncio
async def test_unknown_orders_block_completion_without_canceling_their_ids():
    instance, connector = runtime()
    order = SimpleNamespace(client_order_id="HB-known", is_done=True)
    instance.orders = {"HB-known": (connector, order)}
    instance.owner._open_orders.return_value = [dict(clOrdId="HB-unknown", instId="BTC-USDT-SWAP", instType="SWAP")]
    with pytest.raises(CredentialAdapterError, match="cleanup_unknown_orders"):
        await instance.cancel_orders()
    connector.cancel_all.assert_awaited_once()
    assert set(instance.orders) == {"HB-known"}


@pytest.mark.asyncio
async def test_uncloseable_residual_is_not_rounded_to_zero_or_submitted():
    instance, connector = runtime()
    connector.quantize_order_amount = lambda **kwargs: Decimal(0)
    connector.buy = Mock()
    connector.sell = Mock()
    with pytest.raises(CredentialAdapterError, match="cleanup_uncloseable_residual"):
        await instance.close_position(dict(id="held", side="BUY", trading_pair="BTC-USDT", amount=Decimal("0.00000001")),
                                      dict(id="disconnect", affected_versions={"okx_perpetual": 1}))
    connector.buy.assert_not_called()
    connector.sell.assert_not_called()


@pytest.mark.asyncio
async def test_close_uses_existing_connector_market_close_with_scoped_authority():
    from hummingbot.core.cleanup_authority import current_cleanup_scope
    from hummingbot.core.data_type.common import OrderType, PositionAction
    instance, connector = runtime()
    order = SimpleNamespace(client_order_id="close", is_done=True, executed_amount_base=Decimal("0.1"), is_failure=False)

    def close(*args, **kwargs):
        scope = current_cleanup_scope()
        assert scope.operation_id == "disconnect" and scope.position_side == "LONG"
        assert kwargs["position_action"] == PositionAction.CLOSE and kwargs["order_type"] == OrderType.MARKET
        connector.in_flight_orders["close"] = order
        return "close"

    connector.sell = Mock(side_effect=close)
    connector.buy = Mock()
    await instance.close_position(dict(id="held", side="BUY", trading_pair="BTC-USDT", amount=Decimal("0.1")),
                                  dict(id="disconnect", affected_versions={"okx_perpetual": 1}))
    connector.sell.assert_called_once()
    connector.buy.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('ticker', [
    {'code': '50011', 'data': [{'instId': 'BTC-USDT-SWAP', 'last': '100'}]},
    {'code': '0', 'data': []},
    {'code': '0', 'data': [{'instId': 'ETH-USDT-SWAP', 'last': '100'}]},
    {'code': '0', 'data': [{'instId': 'BTC-USDT-SWAP', 'last': '100'}] * 2},
    *[{'code': '0', 'data': [{'instId': 'BTC-USDT-SWAP', 'last': price}]}
      for price in ('0', '-1', 'NaN', 'Infinity', 'invalid')],
    {'code': '0'},
], ids=['exchange_error', 'empty', 'wrong_pair', 'ambiguous_pair', 'zero', 'negative', 'nan', 'infinity',
        'invalid_decimal', 'missing_data'])
async def test_untrusted_cleanup_price_never_submits_or_leaks_close_authority(ticker):
    from hummingbot.core.cleanup_authority import current_cleanup_scope
    instance, connector = runtime()
    connector._api_get.return_value = ticker
    connector.buy, connector.sell = Mock(), Mock()
    with pytest.raises(CredentialAdapterError, match='cleanup_price_unavailable'):
        await instance.close_position(dict(id='held', side='BUY', trading_pair='BTC-USDT', amount=Decimal('.1')),
                                      dict(id='disconnect', affected_versions={'okx_perpetual': 1}))
    connector.buy.assert_not_called()
    connector.sell.assert_not_called()
    instance.owner.check_owner.assert_not_called()
    assert current_cleanup_scope() is None


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', [asyncio.TimeoutError(), OSError('offline transport failure')],
                         ids=['timeout', 'transport'])
async def test_cleanup_ticker_dependency_failure_has_safe_error_and_no_submission(failure):
    instance, connector = runtime()
    connector._api_get.side_effect = failure
    connector.buy, connector.sell = Mock(), Mock()
    with pytest.raises(CredentialAdapterError, match='cleanup_price_unavailable'):
        await instance.close_position(dict(id='held', side='BUY', trading_pair='BTC-USDT', amount=Decimal('.1')),
                                      dict(id='disconnect', affected_versions={'okx_perpetual': 1}))
    connector.buy.assert_not_called()
    connector.sell.assert_not_called()


@pytest.mark.asyncio
async def test_owner_loss_during_ticker_read_blocks_close_before_authority_is_installed():
    from hummingbot.core.cleanup_authority import current_cleanup_scope
    instance, connector = runtime()
    instance.owner.check_owner.side_effect = RuntimeError('owner lost while awaiting ticker')
    connector.buy, connector.sell = Mock(), Mock()
    with pytest.raises(RuntimeError, match='owner lost'):
        await instance.close_position(dict(id='held', side='BUY', trading_pair='BTC-USDT', amount=Decimal('.1')),
                                      dict(id='disconnect', affected_versions={'okx_perpetual': 1}))
    connector._api_get.assert_awaited_once()
    connector.buy.assert_not_called()
    connector.sell.assert_not_called()
    assert current_cleanup_scope() is None


@pytest.mark.asyncio
@pytest.mark.parametrize('side', ['BUY', 'SELL'])
async def test_terminal_partial_close_waits_for_reconciliation_without_resubmitting(side):
    from hummingbot.core.cleanup_authority import current_cleanup_scope
    instance, connector = runtime()
    order = SimpleNamespace(client_order_id='partial', is_done=True, executed_amount_base=Decimal('.04'))

    def partial(**kwargs):
        assert current_cleanup_scope().amount == Decimal('.1')
        connector.in_flight_orders['partial'] = order
        return 'partial'

    connector.buy, connector.sell = Mock(side_effect=partial), Mock(side_effect=partial)
    with pytest.raises(CredentialAdapterError, match='cleanup_orders_pending'):
        await instance.close_position(dict(id='held', side=side, trading_pair='BTC-USDT', amount=Decimal('.1')),
                                      dict(id='disconnect', affected_versions={'okx_perpetual': 1}))
    assert connector.buy.call_count + connector.sell.call_count == 1
    assert instance.orders['partial'] == (connector, order)
    instance.owner._refresh_orders.assert_awaited_once()
    assert current_cleanup_scope() is None


def test_recovered_unknown_submission_is_tracked_for_readback_without_sending_again():
    from hummingbot.connector.client_order_tracker import ClientOrderTracker
    from hummingbot.core.data_type.common import OrderType, PositionAction, TradeType
    from hummingbot.core.data_type.in_flight_order import InFlightOrder, OrderState

    class Connector:
        name = "okx_perpetual"
        current_timestamp = 1000

        def __init__(self):
            self._order_tracker = ClientOrderTracker(self)

        def restore_tracking_states(self, states):
            self._order_tracker.restore_tracking_states(states)

        @property
        def in_flight_orders(self):
            return self._order_tracker.active_orders

    connector = Connector()
    order = InFlightOrder(client_order_id="unknown-close", trading_pair="BTC-USDT", order_type=OrderType.MARKET,
                          trade_type=TradeType.SELL, amount=Decimal("0.1"), price=Decimal("100"), creation_timestamp=1000,
                          position=PositionAction.CLOSE, initial_state=OrderState.FAILED)
    saved = order.to_json()
    restored = CleanupExchangeRuntime.restore_orders(connector, {order.client_order_id: saved}, {
        order.client_order_id: dict(creation_pending=True, amount=Decimal("0.1"), symbol="BTC-USDT")})
    assert restored[order.client_order_id].current_state == OrderState.PENDING_CREATE
    assert saved["last_state"] == str(OrderState.FAILED.value)


@pytest.mark.asyncio
async def test_cancel_ack_without_terminal_readback_does_not_count_as_cleanup(monkeypatch):
    instance, connector = runtime()
    order = SimpleNamespace(client_order_id='HB-known', is_done=False)
    instance.orders = {'HB-known': (connector, order)}
    times = iter([0, 0, 16])
    monkeypatch.setattr('hummingbot.cli.credential_cleanup_runtime.time', SimpleNamespace(monotonic=lambda: next(times)))
    with pytest.raises(CredentialAdapterError, match='cleanup_orders_pending'):
        await instance.cancel_orders()
    assert connector.cancel_all.await_count == 1
    instance.owner._refresh_orders.assert_awaited_once()


@pytest.mark.asyncio
async def test_cleanup_price_read_uses_registered_native_throttle_and_requested_instrument():
    from hummingbot.connector.derivative.okx_perpetual.okx_perpetual_derivative import OkxPerpetualDerivative
    from hummingbot.connector.derivative.okx_perpetual.okx_perpetual_web_utils import get_rest_api_limit_id_for_endpoint
    instance, connector = runtime()
    native = OkxPerpetualDerivative(okx_perpetual_api_key='offline-key', okx_perpetual_secret_key='offline-secret',
                                    okx_perpetual_passphrase='offline-pass', trading_pairs=['BTC-USDT'])

    async def throttled_get(path_url, **kwargs):
        async with native._throttler.execute_task(get_rest_api_limit_id_for_endpoint('GET', path_url)):
            return dict(code='0', data=[dict(instId='LIFE-USDT-SWAP', last='.1'), dict(instId='BTC-USDT-SWAP', last='100')])

    connector._api_get = throttled_get
    order = SimpleNamespace(client_order_id='close', is_done=True, executed_amount_base=Decimal('.1'))

    def close(**kwargs):
        assert kwargs['price'] == Decimal('100')
        connector.in_flight_orders['close'] = order
        return 'close'

    connector.sell = Mock(side_effect=close)
    await instance.close_position(dict(id='held', side='BUY', trading_pair='BTC-USDT', amount=Decimal('.1')),
                                  dict(id='disconnect', affected_versions={'okx_perpetual': 1}))
    connector.sell.assert_called_once()


@pytest.mark.asyncio
async def test_cleanup_poll_defers_terminal_rereads_until_pending_orders_finish(monkeypatch):
    instance, connector = runtime()
    fast = SimpleNamespace(client_order_id='fast', is_done=False)
    slow = SimpleNamespace(client_order_id='slow', is_done=False)
    already_done = SimpleNamespace(client_order_id='done', is_done=True)
    instance.orders = {o.client_order_id: (connector, o) for o in (fast, slow, already_done)}
    reads = []

    async def refresh(client, orders):
        order, = orders
        reads.append(order.client_order_id)
        order.is_done = order is not slow or reads.count('slow') == 3

    instance.owner._refresh_orders.side_effect = refresh
    monkeypatch.setattr('hummingbot.cli.credential_cleanup_runtime.asyncio.sleep', AsyncMock())
    await instance.cancel_orders()
    # Terminal entries get the first read and one final reconciliation, never
    # another read on every intervening pending-order poll.
    assert reads.count('done') == reads.count('fast') == 2
    assert reads.count('slow') == 3
    assert set(instance.orders) == {'fast', 'slow', 'done'}
    instance.core.trade_fill_db.assert_persistence_healthy.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('final_timeout', [False, True], ids=['late_fill', 'final_read_timeout'])
async def test_cleanup_rechecks_early_terminal_fills_before_reporting_all_orders_verified(monkeypatch, final_timeout):
    instance, connector = runtime()
    fast = SimpleNamespace(client_order_id='fast', is_done=False)
    slow = SimpleNamespace(client_order_id='slow', is_done=False)
    instance.orders = {'fast': (connector, fast), 'slow': (connector, slow)}
    reads, fills_caught_up = [], []

    async def refresh(client, orders):
        order, = orders
        reads.append(order.client_order_id)
        order.is_done = order is fast or reads.count('slow') == 3
        if order is fast and slow.is_done:
            if final_timeout:
                raise asyncio.TimeoutError()
            fills_caught_up.append('late partial cancel fill')

    instance.owner._refresh_orders.side_effect = refresh
    monkeypatch.setattr('hummingbot.cli.credential_cleanup_runtime.asyncio.sleep', AsyncMock())
    if final_timeout:
        with pytest.raises(CredentialAdapterError, match='cleanup_orders_pending'):
            await instance.cancel_orders()
        assert not fills_caught_up and set(instance.orders) == {'fast', 'slow'}
        instance.core.trade_fill_db.assert_persistence_healthy.assert_not_called()
    else:
        await instance.cancel_orders()
        assert fills_caught_up == ['late partial cancel fill']
        instance.core.trade_fill_db.assert_persistence_healthy.assert_called_once()
    assert reads == ['fast', 'slow', 'slow', 'slow', 'fast']


@pytest.mark.asyncio
async def test_cleanup_refresh_timeout_preserves_tracking_and_reports_retryable_dependency():
    instance, connector = runtime()
    order = SimpleNamespace(client_order_id='pending', is_done=False)
    instance.orders = {'pending': (connector, order)}
    instance.owner._refresh_orders.side_effect = asyncio.TimeoutError()
    with pytest.raises(CredentialAdapterError, match='cleanup_orders_pending') as error:
        await instance.cancel_orders()
    assert error.value.status == 503
    assert instance.orders == {'pending': (connector, order)}
    instance.core.trade_fill_db.assert_persistence_healthy.assert_not_called()


@pytest.mark.asyncio
async def test_close_readback_timeout_keeps_original_identity_for_recovery():
    from hummingbot.core.cleanup_authority import current_cleanup_scope
    instance, connector = runtime()
    order = SimpleNamespace(client_order_id='close-pending', is_done=False, executed_amount_base=Decimal(0))

    def place(**kwargs):
        connector.in_flight_orders[order.client_order_id] = order
        return order.client_order_id

    connector.sell, connector.buy = Mock(side_effect=place), Mock()
    instance.owner._refresh_orders.side_effect = asyncio.TimeoutError()
    with pytest.raises(CredentialAdapterError, match='cleanup_orders_pending') as error:
        await instance.close_position(dict(id='held', side='BUY', trading_pair='BTC-USDT', amount=Decimal('.1')),
                                      dict(id='disconnect', affected_versions={'okx_perpetual': 1}))
    assert error.value.status == 503 and current_cleanup_scope() is None
    assert instance.orders == {'close-pending': (connector, order)}
    connector.sell.assert_called_once()
    connector.buy.assert_not_called()


@pytest.mark.asyncio
async def test_cleanup_each_refresh_uses_only_the_remaining_poll_deadline(monkeypatch):
    instance, connector = runtime()
    orders = [SimpleNamespace(client_order_id=str(i), is_done=False) for i in range(2)]
    instance.orders = {o.client_order_id: (connector, o) for o in orders}
    clock = iter([0, 0, 12])
    monkeypatch.setattr('hummingbot.cli.credential_cleanup_runtime.time', SimpleNamespace(monotonic=lambda: next(clock)))
    actual_wait_for, timeouts = asyncio.wait_for, []

    async def wait_for(awaitable, timeout):
        timeouts.append(timeout)
        return await actual_wait_for(awaitable, timeout)

    async def refresh(client, refreshed):
        refreshed[0].is_done = True

    monkeypatch.setattr('hummingbot.cli.credential_cleanup_runtime.asyncio.wait_for', wait_for)
    instance.owner._refresh_orders.side_effect = refresh
    await instance.cancel_orders()
    assert timeouts == [10, 3]


@pytest.mark.asyncio
async def test_close_waits_between_unfinished_rest_reads_instead_of_immediately_repeating(monkeypatch):
    instance, connector = runtime()
    order = SimpleNamespace(client_order_id='pending', is_done=False, executed_amount_base=Decimal('.1'))
    events = []

    def place(**kwargs):
        connector.in_flight_orders['pending'] = order
        return 'pending'

    async def refresh(client, orders):
        events.append('read')
        order.is_done = events.count('read') == 2

    async def sleep(delay):
        if delay == .1:
            events.append('poll_wait')

    connector.sell = Mock(side_effect=place)
    instance.owner._refresh_orders.side_effect = refresh
    monkeypatch.setattr('hummingbot.cli.credential_cleanup_runtime.asyncio.sleep', sleep)
    await instance.close_position(dict(id='held', side='BUY', trading_pair='BTC-USDT', amount=Decimal('.1')),
                                  dict(id='disconnect', affected_versions={'okx_perpetual': 1}))
    assert events == ['read', 'poll_wait', 'read']


@pytest.mark.asyncio
async def test_expired_cleanup_poll_budget_does_not_start_another_rest_request(monkeypatch):
    instance, connector = runtime()
    instance.orders = {'pending': (connector, SimpleNamespace(client_order_id='pending', is_done=False))}
    clock = iter([0, 16])
    monkeypatch.setattr('hummingbot.cli.credential_cleanup_runtime.time', SimpleNamespace(monotonic=lambda: next(clock)))
    with pytest.raises(CredentialAdapterError, match='cleanup_orders_pending'):
        await instance.cancel_orders()
    instance.owner._refresh_orders.assert_not_awaited()
    instance.core.trade_fill_db.assert_persistence_healthy.assert_not_called()


@pytest.mark.asyncio
async def test_cleanup_status_read_cancellation_propagates_without_losing_identity():
    instance, connector = runtime()
    instance.orders = {'pending': (connector, SimpleNamespace(client_order_id='pending', is_done=False))}
    instance.owner._refresh_orders.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await instance.cancel_orders()
    assert list(instance.orders) == ['pending']
    instance.core.trade_fill_db.assert_persistence_healthy.assert_not_called()
