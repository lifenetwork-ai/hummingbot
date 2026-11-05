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
                                _api_get=AsyncMock(return_value={"code": "0", "data": [{"last": "100"}]}))
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
    times = iter([0, 16])
    monkeypatch.setattr('hummingbot.cli.credential_cleanup_runtime.time', SimpleNamespace(monotonic=lambda: next(times)))
    with pytest.raises(CredentialAdapterError, match='cleanup_orders_pending'):
        await instance.cancel_orders()
    assert connector.cancel_all.await_count == 1
    instance.owner._refresh_orders.assert_awaited_once()
