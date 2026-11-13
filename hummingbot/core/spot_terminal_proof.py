"""Positive complete-fill proof from native executions and committed cashflow.

No terminal flag, approximate quantity or empty exchange lookup suffices. The
rolling projection only receives fills after the recorder transaction commits.
"""
import json
from decimal import Decimal

from hummingbot.core.data_type.common import TradeType
from hummingbot.core.data_type.in_flight_order import OrderState


def committed_full_fill(projection, connector, order):
    from hummingbot.connector.markets_recorder import MarketsRecorder
    if (projection is None or projection.reason() is not None or connector.name != 'okx'
            or order.trade_type != TradeType.BUY or order.current_state != OrderState.FILLED
            or not order.exchange_order_id or order.trading_pair != projection.pair):
        return False
    receipt = projection.reservations.get(order.client_order_id)
    if (receipt is None or not order.amount.is_finite() or order.amount <= 0
            or receipt.amount != order.amount or receipt.committed != order.amount
            or order.executed_amount_base != order.amount or not receipt.seen
            or len(receipt.seen) != len(order.order_fills)):
        return False
    total = Decimal(0)
    quote = Decimal(0)
    for identity, native in order.order_fills.items():
        key = connector.name, order.client_order_id, identity
        saved = projection.entries.get(key)
        if (not isinstance(identity, str) or not isinstance(native.trade_id, str)
                or not native.trade_id.isascii() or not native.trade_id.isdigit()
                or key not in receipt.seen or saved is None or saved[2] is None
                or native.trade_id != identity or native.client_order_id != order.client_order_id
                or native.exchange_order_id != order.exchange_order_id or native.trading_pair != order.trading_pair
                or native.fill_base_amount <= 0 or native.fill_price <= 0
                or native.fill_base_amount != Decimal(saved[1]) or native.fill_price != Decimal(saved[4][0])
                or saved[4][1] != 'BUY' or native.fill_quote_amount != native.fill_base_amount * native.fill_price
                or not MarketsRecorder._same_trade_fee(json.loads(saved[4][2]), native.fee.to_json())):
            return False
        total += native.fill_base_amount
        quote += native.fill_quote_amount
    return total == order.amount and quote == order.executed_amount_quote
