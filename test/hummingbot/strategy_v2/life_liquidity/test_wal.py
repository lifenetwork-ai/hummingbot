"""P4.14 write-ahead identity survives an ambiguous send outcome."""

import pytest

from hummingbot.strategy_v2.life_liquidity import state
from hummingbot.strategy_v2.life_liquidity.state import IntentWAL


def test_empty_wal_can_be_durably_initialized_only_once(tmp_path):
    path = tmp_path / "intents.json"
    wal = IntentWAL(path)
    wal.initialize_empty()
    assert IntentWAL(path).all_records() == ()
    with pytest.raises(ValueError, match="WAL_INITIALIZATION_UNSAFE"):
        wal.initialize_empty()
    with pytest.raises(ValueError, match="WAL_INITIALIZATION_UNSAFE"):
        IntentWAL(path).initialize_empty()
    wal.begin("first", client_order_id="life-0001", session_id="s1",
              epoch=1, reservation_id="first")
    assert IntentWAL(path).get("first").state == "PREPARED"


def test_wire_id_reservation_and_epoch_exist_on_disk_before_sender_runs(tmp_path):
    path = tmp_path / "intents.json"
    wal = IntentWAL(path)
    observed = []

    def sender(wire_id):
        persisted = IntentWAL(path).get("intent-1")
        observed.append((persisted.client_order_id, persisted.session_id,
                         persisted.epoch, persisted.reservation_id, persisted.state))
        return "exchange-order-1"

    result = wal.send_once("intent-1", client_order_id="life-0001", session_id="s1",
                           epoch=1, reservation_id="reservation-1", sender=sender)
    assert result == "exchange-order-1"
    assert observed == [("life-0001", "s1", 1, "reservation-1", "SEND_UNKNOWN")]
    assert IntentWAL(path).get("intent-1").state == "ACKED"


def test_lost_ack_or_restart_cannot_blindly_resend(tmp_path):
    path = tmp_path / "intents.json"
    wal = IntentWAL(path)

    def ambiguous(_):
        raise TimeoutError("ACK lost")

    with pytest.raises(TimeoutError):
        wal.send_once("intent-1", client_order_id="life-0001", session_id="s1",
                      epoch=1, reservation_id="reservation-1", sender=ambiguous)
    restarted = IntentWAL(path)
    assert restarted.get("intent-1").state == "SEND_UNKNOWN"
    with pytest.raises(ValueError, match="RECONCILE_BEFORE_RETRY"):
        restarted.send_once("intent-1", client_order_id="life-0001", session_id="s1",
                            epoch=1, reservation_id="reservation-1", sender=lambda _: None)
    assert restarted.pending_reconciliation("s1", 1) == ("life-0001",)


def test_pre_send_identity_can_be_armed_once_or_aborted_without_exchange_send(tmp_path):
    wal = IntentWAL(tmp_path / "intents.json")
    wal.begin("intent-1", client_order_id="life-0001", session_id="s1",
              epoch=1, reservation_id="intent-1")
    assert IntentWAL(wal.path).get("intent-1").state == "PREPARED"
    assert wal.arm_send("intent-1", client_order_id="life-0001", session_id="s1",
                        epoch=1, reservation_id="intent-1")
    with pytest.raises(ValueError, match="RECONCILE_BEFORE_RETRY"):
        wal.arm_send("intent-1", client_order_id="life-0001", session_id="s1",
                     epoch=1, reservation_id="intent-1")
    with pytest.raises(ValueError, match="ORDER_MAY_HAVE_BEEN_SENT"):
        wal.abort_before_send("intent-1")

    wal.begin("intent-2", client_order_id="life-0002", session_id="s1",
              epoch=1, reservation_id="intent-2")
    assert wal.abort_before_send("intent-2")
    assert not wal.abort_before_send("intent-2")
    assert "life-0002" not in wal.scoped_order_ids("s1", 1)


def test_stale_prepared_snapshot_cannot_abort_armed_disk_record(tmp_path):
    path = tmp_path / "intents.json"
    stale = IntentWAL(path)
    stale.begin("intent-1", client_order_id="life-0001", session_id="s1",
                epoch=1, reservation_id="intent-1")
    newer = IntentWAL(path)
    newer.arm_send("intent-1", client_order_id="life-0001", session_id="s1",
                   epoch=1, reservation_id="intent-1")

    with pytest.raises(ValueError, match="WAL_STATE_UNCERTAIN"):
        stale.abort_before_send("intent-1")
    assert IntentWAL(path).get("intent-1").state == "SEND_UNKNOWN"


def test_aborting_one_intent_cannot_overwrite_another_armed_disk_record(tmp_path):
    path = tmp_path / "intents.json"
    stale = IntentWAL(path)
    stale.begin("intent-1", client_order_id="life-0001", session_id="s1",
                epoch=1, reservation_id="intent-1")
    stale.begin("intent-2", client_order_id="life-0002", session_id="s1",
                epoch=1, reservation_id="intent-2")
    newer = IntentWAL(path)
    newer.arm_send("intent-2", client_order_id="life-0002", session_id="s1",
                   epoch=1, reservation_id="intent-2")

    with pytest.raises(ValueError, match="WAL_STATE_UNCERTAIN"):
        stale.abort_before_send("intent-1")
    assert IntentWAL(path).get("intent-1").state == "PREPARED"
    assert IntentWAL(path).get("intent-2").state == "SEND_UNKNOWN"


def test_failed_directory_fsync_cannot_rewind_terminal_wal(tmp_path, monkeypatch):
    path = tmp_path / "intents.json"
    wal = IntentWAL(path)
    wal.prepare("intent-1", client_order_id="life-0001", session_id="s1",
                epoch=1, reservation_id="intent-1")
    wal.acknowledge("intent-1", "exchange-1")
    original_fsync = state.os.fsync
    calls = 0

    def fail_directory_fsync(descriptor):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("crash after WAL replacement")
        return original_fsync(descriptor)

    with monkeypatch.context() as patcher:
        patcher.setattr(state.os, "fsync", fail_directory_fsync)
        with pytest.raises(OSError, match="crash after WAL replacement"):
            wal.mark_terminal("intent-1", "exchange-1")

    assert calls == 2
    assert wal.get("intent-1").state == "ACKED"
    assert IntentWAL(path).get("intent-1").state == "TERMINAL"
    with pytest.raises(ValueError, match="WAL_STATE_UNCERTAIN"):
        wal.mark_cancel_requested("intent-1")
    assert IntentWAL(path).get("intent-1").state == "TERMINAL"
    assert IntentWAL(path).scoped_order_ids("s1", 1) == ()


def test_stale_wal_instance_cannot_rewind_terminal_state(tmp_path):
    path = tmp_path / "intents.json"
    stale = IntentWAL(path)
    stale.prepare("intent-1", client_order_id="life-0001", session_id="s1",
                  epoch=1, reservation_id="intent-1")
    stale.acknowledge("intent-1", "exchange-1")
    newer = IntentWAL(path)
    newer.mark_terminal("intent-1", "exchange-1")

    with pytest.raises(ValueError, match="WAL_STATE_UNCERTAIN"):
        stale.mark_cancel_requested("intent-1")
    assert IntentWAL(path).get("intent-1").state == "TERMINAL"


def test_failed_arm_send_checkpoint_remains_unknown_after_restart(tmp_path, monkeypatch):
    path = tmp_path / "intents.json"
    wal = IntentWAL(path)
    wal.begin("intent-1", client_order_id="life-0001", session_id="s1",
              epoch=1, reservation_id="intent-1")
    original_fsync = state.os.fsync
    calls = 0

    def fail_directory_fsync(descriptor):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("arm commit uncertain")
        return original_fsync(descriptor)

    with monkeypatch.context() as patcher:
        patcher.setattr(state.os, "fsync", fail_directory_fsync)
        with pytest.raises(OSError, match="arm commit uncertain"):
            wal.arm_send("intent-1", client_order_id="life-0001", session_id="s1",
                         epoch=1, reservation_id="intent-1")

    assert wal.get("intent-1").state == "PREPARED"
    with pytest.raises(ValueError, match="WAL_STATE_UNCERTAIN"):
        wal.abort_before_send("intent-1")
    restarted = IntentWAL(path)
    assert restarted.get("intent-1").state == "SEND_UNKNOWN"
    assert restarted.pending_reconciliation("s1", 1) == ("life-0001",)


def test_pre_replace_wal_failure_can_retry_without_claiming_a_send(tmp_path, monkeypatch):
    path = tmp_path / "intents.json"
    wal = IntentWAL(path)
    wal.begin("intent-1", client_order_id="life-0001", session_id="s1",
              epoch=1, reservation_id="intent-1")

    def fail_before_replacement(_descriptor):
        raise OSError("before WAL replacement")

    with monkeypatch.context() as patcher:
        patcher.setattr(state.os, "fsync", fail_before_replacement)
        with pytest.raises(OSError, match="before WAL replacement"):
            wal.arm_send("intent-1", client_order_id="life-0001", session_id="s1",
                         epoch=1, reservation_id="intent-1")

    assert IntentWAL(path).get("intent-1").state == "PREPARED"
    assert wal.arm_send("intent-1", client_order_id="life-0001", session_id="s1",
                        epoch=1, reservation_id="intent-1")
    assert IntentWAL(path).get("intent-1").state == "SEND_UNKNOWN"
