"""Immutable preallocation identity is separate from a captured runner quote."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from uuid import uuid4

import pytest

from hummingbot.core.data_type.common import TradeType
from hummingbot.strategy_v2.life_liquidity.storage.contracts import OwnerToken, StorageError
from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot_intent import PgSpotQuoteIntent


def intent():
    at = datetime.now(timezone.utc)
    owner = OwnerToken(uuid4(), uuid4(), 1, uuid4(), at, at + timedelta(minutes=1))
    return PgSpotQuoteIntent('new-quote', 'main', owner, 1, TradeType.BUY, 0, D('1'), D('.97'))


def test_preallocation_wire_is_fixed_before_quote_capture_and_binds_all_business_fields():
    from dataclasses import replace
    value = intent()
    wire = value.wire_id
    assert len(wire) == 32 and wire.isalnum()
    assert value.wire_id == wire
    assert replace(value, quantity_base=D('1.000'), price_usdt=D('.9700')).wire_id == wire
    for field, changed in [('intent_id', 'different'), ('side', TradeType.SELL), ('level', 1),
                           ('quantity_base', D('2')), ('price_usdt', D('.96')), ('config_version', 2)]:
        assert replace(value, **{field: changed}).wire_id != wire


@pytest.mark.parametrize('fault', ['negative', 'nan', 'side', 'level', 'version'])
def test_invalid_business_intent_never_obtains_a_wire(fault):
    from dataclasses import replace
    value = intent()
    changes = {'negative': dict(quantity_base=D('-1')), 'nan': dict(price_usdt=D('NaN')),
               'side': dict(side='BUY'), 'level': dict(level=-1), 'version': dict(config_version=0)}
    with pytest.raises(StorageError):
        replace(value, **changes[fault])
