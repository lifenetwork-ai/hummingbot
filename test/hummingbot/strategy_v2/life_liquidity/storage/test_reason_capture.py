"""Imported diagnostic aliases must not retain a finished pytest item's state."""
from collections import deque
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.reason_capture import capture_codes, normalize_codes
from types import SimpleNamespace

from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import sql_error


def test_imported_alias_tracks_current_capture_after_original_capture_has_closed():
    first, second = deque(maxlen=2), deque(maxlen=2)
    with capture_codes(first):
        alias = normalize_codes(sql_error)
        assert str(alias(SimpleNamespace(message='LIFE_EXECUTION_UNVERIFIED'))) == 'PG_EXECUTION_UNVERIFIED'
    assert list(first) == ['PG_EXECUTION_UNVERIFIED']
    # Same alias can outlive the fixture that patched runtime.sql_error.
    assert str(alias(SimpleNamespace(message='LIFE_EXECUTION_UNVERIFIED'))) == 'PG_EXECUTION_UNVERIFIED'
    assert len(first) == 1
    with capture_codes(second):
        alias(SimpleNamespace(message='LIFE_DIFFERENT_UNVERIFIED'))
    assert list(first) == ['PG_EXECUTION_UNVERIFIED']
    assert list(second) == ['PG_DIFFERENT_UNVERIFIED']


def test_nested_captures_reset_and_never_record_driver_payloads():
    alias = normalize_codes(sql_error)
    outer, inner = deque(maxlen=2), deque(maxlen=2)
    with capture_codes(outer):
        alias(SimpleNamespace(message='LIFE_ORIGINAL'))
        with capture_codes(inner):
            alias(SimpleNamespace(message='secret sql arguments'))
        alias(SimpleNamespace(message='LIFE_AFTER'))
    assert list(inner) == ['PG_CONSTRAINT_REJECTED']
    assert list(outer) == ['PG_ORIGINAL', 'PG_AFTER']
