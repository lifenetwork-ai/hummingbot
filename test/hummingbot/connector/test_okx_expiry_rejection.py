"""Only a correlated, explicit OKX expired-request response proves rejection."""
import pytest

from hummingbot.connector.okx_order_status import OKXExpiredOrderRejection, check_order_submission


@pytest.mark.parametrize("code", ["0", "1"])
@pytest.mark.parametrize('rejection', ['50036', '50037'])
def test_expired_rejection_requires_matching_client_id_and_no_exchange_id(code, rejection):
    response = {"code": code, "data": [{"clOrdId": "a", "ordId": "", "sCode": rejection}]}
    with pytest.raises(OKXExpiredOrderRejection) as exc:
        check_order_submission(response, "a")
    assert exc.value.client_order_id == "a"


@pytest.mark.parametrize("response", [
    {"code": "0", "data": [{"clOrdId": "other", "ordId": "", "sCode": "50036"}]},
    {"code": "0", "data": [{"clOrdId": "a", "ordId": "42", "sCode": "50036"}]},
    {"code": "0", "data": [{"ordId": "", "sCode": "50036"}]},
    {"code": "0", "data": []},
    {"code": "0", "data": [{"clOrdId": "a", "ordId": "", "sCode": "50011"}]},
])
def test_ambiguous_or_different_failure_never_proves_rejection(response):
    with pytest.raises(IOError) as exc:
        check_order_submission(response, "a")
    assert not isinstance(exc.value, OKXExpiredOrderRejection)


@pytest.mark.asyncio
@pytest.mark.parametrize('market', ['spot', 'perpetual'])
@pytest.mark.parametrize('rejection', ['50036', '50037'])
async def test_connectors_propagate_only_correlated_expiry(market, rejection):
    from decimal import Decimal
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from hummingbot.connector.derivative.okx_perpetual.okx_perpetual_derivative import OkxPerpetualDerivative
    from hummingbot.connector.exchange.okx.okx_exchange import OkxExchange
    from hummingbot.core.data_type.common import OrderType, PositionAction, PositionMode, TradeType

    response = {"code": "1", "data": [{"clOrdId": "a", "ordId": "", "sCode": rejection, "sMsg": "expired"}]}
    source = SimpleNamespace(exchange_symbol_associated_to_pair=AsyncMock(return_value='BTC-USDT'),
                             _api_request=AsyncMock(return_value=response), _api_post=AsyncMock(return_value=response),
                             position_mode=PositionMode.HEDGE, _format_amount_to_size=lambda pair, amount: amount)
    with pytest.raises(OKXExpiredOrderRejection):
        if market == 'spot':
            await OkxExchange._place_order(source, 'a', 'BTC-USDT', Decimal('1'), TradeType.BUY,
                                           OrderType.LIMIT_MAKER, Decimal('100'))
        else:
            await OkxPerpetualDerivative._place_order(source, 'a', 'BTC-USDT', Decimal('1'), TradeType.BUY,
                                                      OrderType.LIMIT_MAKER, Decimal('100'), PositionAction.OPEN)
