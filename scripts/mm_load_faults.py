"""Fault windows on the same isolated image/PG/ledger harness as healthy load."""
import json
import threading
import time
from decimal import Decimal

from scripts.test_e2e_offline import docker, poll


def fault_windows(harness, bot, port, client, report, run_window, verify):
    results = {}

    def window(name, action, duration=45, delay=8):
        errors = []

        def invoke():
            try:
                action()
            except Exception as error:
                errors.append(repr(error))
        timer = threading.Timer(delay, invoke)
        timer.start()
        try:
            data = run_window(harness, bot, client, "fault-" + name, duration, report,
                              options=("--no-fills", "--no-controls"))
        finally:
            timer.join(timeout=60)
        assert not timer.is_alive() and not errors, errors
        assert not any(data["statuses"].get(str(status), 0) for status in (400, 422, 500)), data["errors"]
        data["classification"] = "injected_fault_excluded_from_healthy_latency"
        results[name] = data
        print("Fault under load passed:", name, flush=True)

    def start():
        harness.mm_command(port, "start", market_mode="both", selection="fixed", profile_id="follow_market", duration_seconds=600)
        harness.wait_running(port)

    # Standalone faults need real retained positions too, not only the seeded
    # reporting history. The full healthy profile already supplies these fills.
    if not harness.ledger()["fills"]:
        for instrument in ("LIFE-USDT", "LIFE-USDT-SWAP"):
            order = next(row for row in harness.ledger()["orders"]
                         if row["instId"] == instrument and row["side"] == "buy" and row["state"] == "live")
            harness.control(fill={"clOrdId": order["clOrdId"], "amount": order["sz"], "fee": "0.01"}, duplicate=True)
        poll(lambda: len(harness.ledger()["fills"]) == 2, description="standalone fault fills in both markets")
        verify(harness, port)

    def feed():
        deadline = harness.state()["mm_session"]["expires_at"]
        harness.control(stale_pairs=["BTC-USDT"])
        poll(lambda: harness.state()["status"]["phase"] == "waiting_for_mm_readiness", timeout=25, description="loaded stale basket closes both markets")
        sent = len(harness.ledger()["accepted"])
        time.sleep(2)
        assert len(harness.ledger()["accepted"]) == sent
        harness.control(stale_pairs=[])
        harness.wait_running(port)
        assert harness.state()["mm_session"]["expires_at"] == deadline
    harness.mm_command(port, "switch", market_mode="both", selection="fixed", profile_id="follow_market", duration_seconds=600)
    window("feed", feed)

    def restart():
        before = harness.state()["mm_session"]
        harness.terminate(bot)
        docker("start", bot)
        harness.wait_running(port)
        after = harness.state()["mm_session"]
        assert all(after[key] == before[key] for key in ("id", "started_at", "expires_at", "anchors"))
    window("owner-restart", restart)
    verify(harness, port)

    harness.mm_command(port, "switch", market_mode="both", selection="fixed", profile_id="follow_market", duration_seconds=60)
    expiry = harness.state()["mm_session"]["expires_at"]

    def queued_expiry():
        harness.control(target_updates_per_second=500)
        docker("pause", bot)
        try:
            time.sleep(2)
        finally:
            docker("unpause", bot)
        poll(lambda: harness.state()["mm_session"]["status"] == "expired", timeout=15, description="expiry despite queued book events")
        poll(lambda: all(row["state"] in ("filled", "canceled") for row in harness.ledger()["orders"]), description="expiry cancel under load")
        assert all(Decimal(row["cTime"]) < Decimal(str(expiry)) * 1000 for row in harness.ledger()["accepted"])
        harness.control(target_updates_per_second=100)
    window("queued-expiry", queued_expiry, duration=75, delay=max(.1, expiry - time.time() - 1))
    start()

    def delayed_database():
        # A real catalog lock blocks runtime SELECTs; runtime lock/statement
        # timeouts must fail closed rather than keeping trading on cached state.
        harness.sql("BEGIN; LOCK TABLE bot_state IN ACCESS EXCLUSIVE MODE; SELECT pg_sleep(4); COMMIT")
        poll(lambda: json.loads(docker("inspect", bot).stdout)[0]["State"]["Status"] == "exited", timeout=12, description="delayed DB closes owner")
        sent = len(harness.ledger()["accepted"])
        docker("start", bot)
        poll(lambda: harness.state()["status"].get("phase") == "halted", timeout=30, description="delayed DB restart retains HALT")
        assert len(harness.ledger()["accepted"]) == sent
    window("database-delay", delayed_database)
    verify(harness, port)
    # Reuse explicit test-only recovery only after independent exposure review.
    harness.reviewed_test_recovery()
    harness.mm_command(port, "stop")
    start()

    def failed_database():
        docker("stop", "--time", "5", harness.pg)
        try:
            poll(lambda: json.loads(docker("inspect", bot).stdout)[0]["State"]["Status"] == "exited", timeout=12, description="failed DB closes loaded owner")
        finally:
            docker("start", harness.pg)
        poll(lambda: docker("exec", harness.pg, "pg_isready", "-U", "postgres", check=False).returncode == 0, timeout=30, description="fault PG restored")
        sent = len(harness.ledger()["accepted"])
        docker("start", bot)
        poll(lambda: harness.state()["status"].get("phase") == "halted", timeout=30, description="failed DB restart retains HALT")
        assert harness.state()["recovery_state"]["halt"] and len(harness.ledger()["accepted"]) == sent
    window("database-failure", failed_database)
    verify(harness, port)
    return results
