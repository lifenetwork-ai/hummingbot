"""P4.14 write-ahead identity survives an ambiguous send outcome."""

import pytest

from hummingbot.strategy_v2.life_liquidity.state import IntentWAL


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
