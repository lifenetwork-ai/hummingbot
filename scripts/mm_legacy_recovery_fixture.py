"""Test-only operator proof for the legacy E2E startup path, never a runtime API."""
from decimal import Decimal

from hummingbot.connector.exchange.okx.okx_utils import DEFAULT_FEES
from hummingbot.core.data_type.common import TradeType
from hummingbot.core.data_type.trade_fee import TokenAmount, TradeFeeBase
from hummingbot.model.runtime_journal import digest


def review(ledger, orders, recorded, environment):
    assert not ledger['unknown'] and not ledger.get('positions'), 'Legacy fixture scope must be dedicated Spot only'
    remote = {order['clOrdId']: order for order in ledger['orders']}
    assert 0 < len(remote) == len(ledger['orders']) == len(orders) <= 64 and set(remote) == {order['id'] for order in orders}
    trades = {}
    for fill in ledger['fills']:
        identity = fill['clOrdId'], fill['tradeId']
        assert identity not in trades and identity[0] in remote, 'Conflicting or foreign venue fill'
        order = remote[identity[0]]
        assert fill['ordId'] == order['ordId'] and fill['instId'] == order['instId'] and fill['side'] == order['side']
        assert fill['feeCcy'] and Decimal(fill['fee']).is_finite()
        trades[identity] = fill
    for local in orders:
        order = remote[local['id']]
        original = (local.get('attempt_context') or {}).get('order') or {}
        context = local.get('attempt_context') or {}
        assert context.get('environment') == environment
        assert context.get('credential_fingerprint') == digest(['credential_identity', environment, 'okx', 'offline-key'])
        assert local['config_file_path'] == 'pmm.yml' and local['market'] == 'okx'
        assert local['symbol'] == order['instId'] == original.get('trading_pair')
        assert order['side'] == str(original.get('trade_type')).lower()
        assert order['ordType'] == {'LIMIT': 'limit', 'LIMIT_MAKER': 'post_only'}.get(original.get('order_type'))
        assert Decimal(str(local['amount'])) == Decimal(order['sz']) == Decimal(original['amount'])
        assert Decimal(str(local['price'])) == Decimal(order['px']) == Decimal(original['price'])
        assert order['ordId'] and (not local['exchange_order_id'] or local['exchange_order_id'] == order['ordId'])
        amount = sum((Decimal(fill['fillSz']) for (cid, _), fill in trades.items() if cid == local['id']), Decimal(0))
        assert amount == Decimal(order['accFillSz']) and 0 <= amount <= Decimal(order['sz'])
    for local in recorded:
        fill = trades.get((local['order_id'], local['exchange_trade_id']))
        assert fill is not None, 'A durable fill is missing from the full venue proof'
        assert local['market'] == 'okx' and local['symbol'] == fill['instId'] and local['trade_type'].lower() == fill['side']
        assert Decimal(str(local['amount'])) == Decimal(fill['fillSz']) and Decimal(str(local['price'])) == Decimal(fill['fillPx'])
        fee = TradeFeeBase.new_spot_fee(DEFAULT_FEES, trade_type=TradeType[local['trade_type']], percent_token=fill['feeCcy'],
                                        flat_fees=[TokenAmount(fill['feeCcy'], -Decimal(fill['fee']))])
        assert TradeFeeBase.from_json(local['trade_fee']) == fee, 'Immutable original fees differ'
    return dict(orders=len(orders), venue_fills=len(trades), durable_fills=len(recorded),
                pending_intents=sum(bool(row['creation_pending']) for row in orders), automatic=False)
