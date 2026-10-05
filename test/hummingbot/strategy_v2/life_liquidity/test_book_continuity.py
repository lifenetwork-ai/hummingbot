from types import SimpleNamespace

from hummingbot.connector.exchange.okx.okx_book_health import BookFeedHealth
from hummingbot.strategy_v2.life_liquidity.market_data import BookContinuityGate, SnapshotQualityGate


def _health(epoch=1, *, connected=True, synchronized=True, received=1.0, last=1.0):
    return BookFeedHealth(connected=connected, synchronized=synchronized, epoch=epoch,
                          sequence_id=10, snapshot_exchange_timestamp_ms=2000,
                          snapshot_received_monotonic=received, last_message_monotonic=last,
                          reason_code="BOOK_FEED_SYNCHRONIZED")


def test_resync_requires_new_rest_snapshot_and_revokes_old_epoch():
    now = {"value": 2.0}
    quality = SnapshotQualityGate("LIFE-USDT", 2000, clock=lambda: now["value"])
    quality.ready = True
    quality.snapshot = SimpleNamespace(received_monotonic=2.0, observed_age_ms=0)
    gate = BookContinuityGate()
    old_rest = SimpleNamespace(received_monotonic=0.5, exchange_timestamp_ms=1900, sequence_id=1)
    assert not gate.confirm(_health(), old_rest, request_started_monotonic=0.5)
    assert gate.reason_code == "BOOK_SNAPSHOT_BEFORE_RESYNC"
    fresh_rest = SimpleNamespace(received_monotonic=2.0, exchange_timestamp_ms=2001, sequence_id=11)
    assert not gate.confirm(_health(), fresh_rest, request_started_monotonic=0.5)
    same_time_old_sequence = SimpleNamespace(received_monotonic=2.0, exchange_timestamp_ms=2000,
                                             sequence_id=9)
    sequenced_health = BookFeedHealth(
        connected=True, synchronized=True, epoch=1, sequence_id=10, snapshot_sequence_id=10,
        snapshot_exchange_timestamp_ms=2000, snapshot_received_monotonic=1.0,
        last_message_monotonic=1.0)
    assert not gate.confirm(sequenced_health, same_time_old_sequence, request_started_monotonic=1.5)
    assert gate.confirm(_health(), fresh_rest, request_started_monotonic=1.5)
    assert gate.permit(_health(), quality)
    assert not gate.permit(_health(2, synchronized=False), quality)
    assert not gate.permit(_health(2), quality)
    assert gate.confirm(_health(2, received=2.1), fresh_rest, request_started_monotonic=2.0) is False
    assert gate.confirm(_health(2, received=1.9), fresh_rest, request_started_monotonic=2.0)
    assert gate.permit(_health(2, received=1.9), quality)
    now["value"] = 67
    assert not gate.permit(_health(2, received=1.9), quality)
