"""Immutable runner hints. Only independent account reconciliation books money."""
import json
import math
from dataclasses import dataclass
from decimal import Decimal

from hummingbot.core.data_type.common import OrderType, TradeType
from hummingbot.core.data_type.trade_fee import AddedToCostTradeFee, TokenAmount, TradeFeeBase
from hummingbot.core.event.events import OrderCancelledEvent, OrderFilledEvent
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError, encode, identifier
from hummingbot.strategy_v2.life_liquidity.storage.financial import decimal, signed_decimal


@dataclass(frozen=True)
class RunnerHint:
    kind: str
    payload: bytes

    @classmethod
    def capture(cls, kind, event):
        expected = OrderFilledEvent if kind == 'fill' else OrderCancelledEvent if kind == 'cancel' else None
        if expected is None or type(event) is not expected:
            raise StorageError('RUNNER_HINT_TYPE_INVALID')
        identifier(event.order_id)
        if (type(event.timestamp) not in (float, int) or not math.isfinite(event.timestamp)
                or event.timestamp < 0):
            raise StorageError('RUNNER_HINT_TIME_INVALID')
        body = {'timestamp': str(Decimal(str(event.timestamp))), 'wire': event.order_id, 'exchange': event.exchange_order_id}
        if event.exchange_order_id is not None:
            identifier(event.exchange_order_id)
        if kind == 'fill':
            fee = event.trade_fee
            identifier(event.exchange_trade_id)
            if (event.trading_pair != 'LIFE-USDT' or event.trade_type not in (TradeType.BUY, TradeType.SELL)
                    or event.order_type != OrderType.LIMIT_MAKER or not event.exchange_order_id
                    or not isinstance(fee, TradeFeeBase) or fee.percent != 0 or len(fee.flat_fees) != 1
                    or fee.flat_fees[0].token not in ('LIFE', 'USDT')
                    or fee.percent_token not in (None, fee.flat_fees[0].token)):
                raise StorageError('RUNNER_HINT_FILL_INVALID')
            body.update(side=event.trade_type.name, price=str(decimal(event.price, positive=True)),
                        amount=str(decimal(event.amount, positive=True)), trade=event.exchange_trade_id,
                        fee_token=fee.flat_fees[0].token, fee=str(signed_decimal(fee.flat_fees[0].amount)))
        payload = encode(body)
        if len(payload) > 4096:
            raise StorageError('RUNNER_HINT_TOO_LARGE')
        return cls(kind, payload)

    def event(self):
        value = json.loads(self.payload)
        if self.kind == 'cancel':
            return OrderCancelledEvent(float(value['timestamp']), value['wire'], value['exchange'])
        return OrderFilledEvent(float(value['timestamp']), value['wire'], 'LIFE-USDT', TradeType[value['side']],
                                OrderType.LIMIT_MAKER, Decimal(value['price']), Decimal(value['amount']),
                                AddedToCostTradeFee(flat_fees=[TokenAmount(value['fee_token'], Decimal(value['fee']))]),
                                value['trade'], value['exchange'])
