from datetime import datetime, timedelta, timezone
from decimal import Decimal

from hummingbot.cli.admin_models import HistoryQuery, OrdersPage, PositionMarket


def test_nested_pair_observation_timestamps_are_utc_like_direct_fields():
    observed = datetime(2026, 1, 1, 7, tzinfo=timezone(timedelta(hours=7)))
    model = PositionMarket(market='perpetual', connector='okx_perpetual', coverage='observed',
                           observed_at=observed, pair_observations={'LIFE-USDT': observed}, items=[])
    body = model.model_dump(mode='json')
    assert body['observed_at'] == '2026-01-01T00:00:00Z'
    assert body['pair_observations']['LIFE-USDT'] == body['observed_at']
    assert model.model_dump()['pair_observations']['LIFE-USDT'] == observed


def test_history_query_keeps_python_offset_and_utc_json_without_changing_filter():
    value = datetime(2026, 1, 1, 7, tzinfo=timezone(timedelta(hours=7)))
    query = HistoryQuery.model_validate({'from': value})
    assert query.from_time.utcoffset() == timedelta(hours=7)
    assert query.model_dump(mode='json')['from_time'] == '2026-01-01T00:00:00Z'
    assert query.filters()['start_ms'] == 1767225600000


def test_large_history_page_keeps_exact_decimals_and_nested_utc_contract():
    observed = datetime(2026, 1, 1, 7, tzinfo=timezone(timedelta(hours=7)))
    row = dict(id='order', connector='okx', market='spot', pair='LIFE-USDT', side='BUY',
               amount=Decimal('0.00000001234567890123456789'), price=Decimal('0.1234567890123456789'),
               order_type='LIMIT_MAKER', position='OPEN', leverage=1, status='BuyOrderCompleted',
               creation_pending=False, exchange_order_id='x', created_at=observed, updated_at=observed)
    page = OrdersPage.model_validate(dict(items=[dict(row, id=str(index)) for index in range(500)], next_cursor=None))
    body = page.model_dump(mode='json')
    assert len(body['items']) == 500
    assert body['items'][0]['amount'] == str(row['amount'])
    assert body['items'][-1]['created_at'] == '2026-01-01T00:00:00Z'
    assert body['items'][-1]['price'] == str(row['price'])
