from hummingbot.connector.exchange.okx.okx_book_health import OkxBookHealthTracker


def _book(seq, prev, *, ts="2000", bids=None, asks=None):
    return {"seqId": seq, "prevSeqId": prev, "ts": ts,
            "bids": [] if bids is None else bids, "asks": [] if asks is None else asks}


def test_snapshot_updates_heartbeat_and_sequence_reset_keep_continuity():
    tracker = OkxBookHealthTracker(["LIFE-USDT"], clock=lambda: 1.0)
    tracker.on_connect()
    assert tracker.on_message("LIFE-USDT", "snapshot", _book(10, -1))
    epoch = tracker.status("LIFE-USDT").epoch
    assert tracker.on_message("LIFE-USDT", "update", _book(15, 10, bids=[["1", "1"]]))
    assert tracker.on_message("LIFE-USDT", "update", _book(15, 15))
    assert tracker.on_message("LIFE-USDT", "update", _book(3, 15, asks=[["2", "1"]]))
    health = tracker.status("LIFE-USDT")
    assert health.synchronized and health.sequence_id == 3 and health.epoch == epoch
    assert health.snapshot_exchange_timestamp_ms == 2000


def test_gap_and_disconnect_revoke_until_new_snapshot():
    tracker = OkxBookHealthTracker(["LIFE-USDT"], clock=lambda: 1.0)
    tracker.on_connect()
    assert tracker.on_message("LIFE-USDT", "snapshot", _book(10, -1))
    epoch = tracker.status("LIFE-USDT").epoch
    assert not tracker.on_message("LIFE-USDT", "update", _book(16, 9))
    assert tracker.status("LIFE-USDT").reason_code == "BOOK_SEQUENCE_GAP"
    assert tracker.status("LIFE-USDT").epoch > epoch
    assert not tracker.on_message("LIFE-USDT", "update", _book(17, 16))
    tracker.on_disconnect()
    assert not tracker.status("LIFE-USDT").connected
    tracker.on_connect()
    assert not tracker.status("LIFE-USDT").synchronized
    assert tracker.on_message("LIFE-USDT", "snapshot", _book(20, -1))


def test_update_before_snapshot_and_missing_sequence_fail_closed():
    tracker = OkxBookHealthTracker(["LIFE-USDT"], clock=lambda: 1.0)
    tracker.on_connect()
    assert not tracker.on_message("LIFE-USDT", "update", _book(11, 10))
    assert tracker.status("LIFE-USDT").reason_code == "BOOK_SNAPSHOT_REQUIRED"
    malformed = _book(20, -1)
    del malformed["prevSeqId"]
    assert not tracker.on_message("LIFE-USDT", "snapshot", malformed)
    assert tracker.status("LIFE-USDT").reason_code == "BOOK_SEQUENCE_INVALID"
    malformed = _book(20, -1)
    del malformed["bids"]
    assert not tracker.on_message("LIFE-USDT", "snapshot", malformed)


def test_new_snapshot_on_same_connection_revokes_previous_confirmation_epoch():
    tracker = OkxBookHealthTracker(["LIFE-USDT"], clock=lambda: 1.0)
    tracker.on_connect()
    assert tracker.on_message("LIFE-USDT", "snapshot", _book(10, -1))
    first_epoch = tracker.status("LIFE-USDT").epoch
    assert tracker.on_message("LIFE-USDT", "snapshot", _book(20, -1))
    assert tracker.status("LIFE-USDT").epoch > first_epoch
