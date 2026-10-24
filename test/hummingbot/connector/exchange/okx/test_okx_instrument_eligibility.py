import pytest

from hummingbot.connector.exchange.okx.okx_utils import is_exchange_information_valid


@pytest.mark.parametrize("state", ["suspend", "preopen", "test", "", None])
def test_spot_instrument_must_be_live(state):
    assert not is_exchange_information_valid(dict(instType="SPOT", baseCcy="LIFE", quoteCcy="USDT", state=state))


def test_live_spot_instrument_is_supported():
    assert is_exchange_information_valid(dict(instType="SPOT", baseCcy="LIFE", quoteCcy="USDT", state="live"))
