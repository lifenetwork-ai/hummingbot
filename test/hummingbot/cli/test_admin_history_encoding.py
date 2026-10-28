"""History response encoding preserves the FE wire contract at maximum page size."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from aiohttp import BasicAuth, web
from aiohttp.test_utils import TestClient, TestServer

from hummingbot.cli.admin_api import ADMIN_PREFIX, AdminAPI, admin_middleware
from hummingbot.cli.admin_models import FillsPage, OrdersPage
from hummingbot.client.config.admin_config import AdminSettings


@pytest.mark.asyncio
@pytest.mark.parametrize('route,model', [('orders', OrdersPage), ('fills', FillsPage)])
async def test_history_json_preserves_exact_values_utc_unicode_and_cursor(route, model):
    stamp = datetime(2026, 1, 1, 7, tzinfo=timezone(timedelta(hours=7)))
    common = dict(id='order-"\\-é', connector='okx', market='spot', pair='LIFE-USDT', side='BUY',
                  amount=Decimal('0.00000001234567890123456789'), price=Decimal('0.1234567890123456789'),
                  position='OPEN', leverage=1)
    if route == 'orders':
        row = dict(common, order_type='LIMIT_MAKER', status='BuyOrderCompleted', creation_pending=False,
                   exchange_order_id=None, created_at=stamp, updated_at=stamp)
    else:
        row = dict(common, order_id='order', timestamp=stamp, quote_currency='USDT',
                   fee_in_quote=Decimal('0.000001'), conversion_status='recorded',
                   fee=dict(percent='0', flat_fees=[dict(token='USDT', amount='0.000001')]))
    page = dict(items=[dict(row, id=str(i) + row['id']) for i in range(500)], next_cursor='opaque+/=')
    expected = model.model_validate(page).model_dump(mode='json')
    settings = AdminSettings(True, 'operator', 'test-password')
    app = web.Application(middlewares=[admin_middleware(settings)])
    AdminAPI(None, settings, store=SimpleNamespace(**{route: lambda **kwargs: page})).register(app)
    async with TestClient(TestServer(app), headers={'Authorization': BasicAuth('operator', 'test-password').encode()}) as client:
        response = await client.get(ADMIN_PREFIX + '/' + route + '?limit=500')
        assert response.status == 200 and response.content_type == 'application/json'
        actual = await response.json()
        assert actual == expected
        assert actual['items'][0]['amount'] == str(common['amount'])
        assert actual['items'][0]['created_at' if route == 'orders' else 'timestamp'] == '2026-01-01T00:00:00Z'
