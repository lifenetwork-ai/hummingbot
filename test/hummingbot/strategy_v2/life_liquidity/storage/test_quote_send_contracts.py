"""Strict exact JSON bytes for the financial send boundary; no transport grant."""
import json
from test.hummingbot.strategy_v2.life_liquidity.storage.test_action_preparation_contracts import (
    action,
    admission,
    prepare,
)
from types import SimpleNamespace

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
from hummingbot.strategy_v2.life_liquidity.storage.quote_send import ArmQuoteAction


@pytest.mark.parametrize('raw', ['{}', '[]', '{"px":"1","px":"2"}', 'NaN', '{"px":NaN}', '{"x":', '\ud800'])
def test_invalid_or_duplicate_wire_json_is_rejected(raw):
    cmd = SimpleNamespace(action=action(), admission=admission(), prepare=prepare())
    with pytest.raises(StorageError, match='QUOTE_WIRE_INVALID'):
        ArmQuoteAction(cmd.prepare.operation_id, cmd.action, cmd.admission, raw)


def test_wire_encoding_roundtrip_retains_exact_bytes_and_fixed_digest():
    cmd = SimpleNamespace(action=action(), admission=admission(), prepare=prepare())
    raw = '{ "clOrdId":"wire1", "instId":"LIFE-USDT", "side":"buy", "ordType":"post_only", "tdMode":"cash", "px":"1.00", "sz":"2" }'
    bound = ArmQuoteAction(cmd.prepare.operation_id, cmd.action, cmd.admission, raw)
    assert ArmQuoteAction.from_json(json.loads(json.dumps(bound.json()))) == bound
    assert bound.json()['wire_json'] == raw and len(bound.wire_sha256) == 64
