"""The fee stimulus is authenticated and scoped before packaged qualification."""
import json
from test.hummingbot.cli.test_managed_lifecycle_fixture import exchange, signed

import pytest

from hummingbot.connector.okx_reduction_fee import FeeObservation


@pytest.mark.asyncio
async def test_fee_fixture_uses_exact_family_and_failure_is_never_a_zero_rate():
    import time
    venue = exchange()
    request = signed('/api/v5/account/trade-fee?instType=SWAP&instFamily=LIFE-USDT')
    request.path, request.query = '/api/v5/account/trade-fee', dict(instType='SWAP', instFamily='LIFE-USDT')
    response = json.loads((await venue.handle(request)).text)
    assert FeeObservation.decode(response, wall_now=time.time(), monotonic_now=50).current(50).rate > 0
    await venue.control({'reduction_fee_error': True})
    with pytest.raises(ValueError):
        FeeObservation.decode(json.loads((await venue.handle(request)).text), wall_now=time.time(), monotonic_now=50)
