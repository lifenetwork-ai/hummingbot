"""Display assets follow the actual observed pair, never profile/reference defaults."""
from copy import deepcopy
from test.hummingbot.cli.test_admin_api import client

import pytest

from hummingbot.cli.admin_api import ADMIN_PREFIX
from hummingbot.cli.admin_models import MarketReadiness


@pytest.mark.parametrize('pair,expected', [
    ('BTC-USDT', ('BTC', 'USDT')), ('LIFE-USDT', ('LIFE', 'USDT')),
    ('ETH-BTC', ('ETH', 'BTC')), ('1000PEPE-USDT', ('1000PEPE', 'USDT')),
    ('', (None, None)), ('BTC-USDT-SWAP', (None, None)), ('malformed', (None, None)),
])
def test_market_response_assets_come_from_pair(pair, expected):
    model = MarketReadiness(market='spot', connector='okx', pair=pair, ready=False, admitted=False,
                            reference_price=None, reason='not_started')
    body = model.model_dump(mode='json')
    assert (body['base_asset'], body['quote_asset']) == expected


def test_static_metadata_cache_cannot_retain_readiness_or_previous_pair_context():
    from hummingbot.cli.admin_models import _observed_pair_assets
    _observed_pair_assets.cache_clear()
    fields = dict(market='spot', connector='okx', ready=False, admitted=False, reference_price=None, reason='not_started')
    first = MarketReadiness(pair='BTC-USDT', **fields)
    second = MarketReadiness(pair='BTC-USDT', **{**fields, 'ready': True, 'admitted': True, 'reason': None})
    assert not first.ready and second.ready
    assert _observed_pair_assets.cache_info().hits == 1
    switched = MarketReadiness(pair='ETH-BTC', **fields)
    assert (switched.base_asset, switched.quote_asset) == ('ETH', 'BTC')
    for index in range(200):
        MarketReadiness(pair=f'TOKEN{index}-USDT', **fields)
    assert _observed_pair_assets.cache_info().currsize <= 128
    assert (first.base_asset, first.quote_asset) == ('BTC', 'USDT')


@pytest.mark.asyncio
@pytest.mark.parametrize('age', [0, 30])
async def test_status_http_keeps_pair_units_independent_of_readiness_and_freshness(age):
    market = dict(market='spot', connector='okx', pair='BTC-USDT', ready=True, admitted=True,
                  reference_price='60000', reason=None)
    state = dict(bot_id='hummingbot', desired_state='running', control_version=1, acknowledged_version=1,
                 owner_uuid='owner', heartbeat_age_s=age, database_now=1200, mm_session=None,
                 status=dict(phase='running', running=True, mm_session=None, mm_markets=[market]))
    before = deepcopy(state)
    async with client(state=state) as (http, storage):
        response = await http.get(ADMIN_PREFIX + '/status')
        assert response.status == 200
        assert response.headers['Cache-Control'] == 'no-store'
        row = (await response.json())['markets'][0]
        assert row['pair'] == 'BTC-USDT' and row['base_asset'] == 'BTC' and row['quote_asset'] == 'USDT'
        if age:
            assert row['ready'] is False and row['admitted'] is False
        storage.command.assert_not_called()
    assert state == before
