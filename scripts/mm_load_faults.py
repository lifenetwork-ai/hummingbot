"""Fault windows on the same isolated image/PG/ledger harness as healthy load."""
import json
import threading
import time
from datetime import datetime
from decimal import Decimal

from scripts.test_e2e_offline import docker, poll


def cancel_upper_bound(baseline, observed, stimulus_ms):
    """Input precedes restriction decisions, so this bounds decision-to-ACK time."""
    by_id = {row['clOrdId']: row for row in observed}
    maximum = {}
    assert baseline, 'Cancellation measurement needs known working orders'
    for before in baseline:
        row = by_id.get(before['clOrdId']) or {}
        assert row.get('state') == 'canceled' and row.get('cancel_terminal_ms'), 'Missing first terminal proof'
        latency = max(0, int(row['cancel_terminal_ms']) - stimulus_ms)
        maximum[before['instId']] = max(maximum.get(before['instId'], 0), latency)
    return maximum


def fault_windows(harness, bot, port, client, report, run_window, verify, *, on_result=None, volatility_defense=False):
    results = {}

    def window(name, action, duration=45, delay=8, details=None, clean_reads=False):
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
        if clean_reads:
            assert data.get('unexpected_errors', 1) == 0, data['errors']
        data["classification"] = "injected_fault_excluded_from_healthy_latency"
        if details is not None:
            data.update(details)
        results[name] = data
        if on_result is not None:
            on_result(name, data)
        print("Fault under load passed:", name, flush=True)

    def start():
        harness.mm_command(port, "start", market_mode="both", selection="fixed", profile_id="follow_market", duration_seconds=600)
        harness.wait_running(port)

    # Standalone faults need real retained positions too, not only the seeded
    # reporting history. The full healthy profile already supplies these fills.
    if not harness.ledger()["fills"]:
        for instrument in ("LIFE-USDT", "LIFE-USDT-SWAP"):
            order = poll(lambda: next((row for row in harness.ledger()["orders"]
                                       if row["instId"] == instrument and row["side"] == "buy" and row["state"] == "live"), None),
                         description=f'working {instrument} BUY for standalone fault setup')
            harness.control(fill={"clOrdId": order["clOrdId"], "amount": order["sz"], "fee": "0.01"}, duplicate=True)
        poll(lambda: len(harness.ledger()["fills"]) == 2, description="standalone fault fills in both markets")
        poll(lambda: harness.sql('SELECT count(*) FROM trade_fills') == '2', description='standalone fault fills committed')
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

    if volatility_defense:
        proof = {}

        def defense_states():
            return [(row.get('spread_policy') or {}).get('defense') or {}
                    for row in harness.state()['status'].get('mm_markets', [])]

        def working():
            return [row for row in harness.ledger()['orders'] if row['state'] in ('live', 'partially_filled')]

        def defense():
            positions = harness.ledger()['positions']
            baseline = working()
            started = time.time()
            harness.control(candle_width='.015')
            poll(lambda: len(defense_states()) == 2 and all(row.get('state') == 'reduced' for row in defense_states()),
                 description='loaded NATR reduction')

            def smaller():
                rows = working()
                allocation = sum((Decimal(row['px']) * Decimal(row['sz']) *
                                  (Decimal('.01') if row['instId'].endswith('-SWAP') else 1) for row in rows), Decimal(0))
                return len(rows) == 12 and allocation <= 100
            poll(smaller, description='loaded reduced orders respect the effective allocation')
            cancel_ms = cancel_upper_bound(baseline, harness.ledger()['orders'], started * 1000)
            assert all(value <= 5000 for value in cancel_ms.values()), cancel_ms
            harness.control(candle_width='.03')
            poll(lambda: len(defense_states()) == 2 and all(row.get('state') == 'paused' for row in defense_states()) and not working(),
                 description='loaded extreme volatility cancels both quote sets')
            poll(lambda: len((harness.state()['recovery_state'].get('volatility_defense') or {}).get('pauses', {})) == 2,
                 description='loaded pause checkpoints are durable')
            ledger = harness.ledger()
            decisions = {}
            for line in docker('logs', bot).stdout.splitlines():
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if row.get('event') == 'mm_volatility_defense_changed' and 'state=paused ' in row.get('message', ''):
                    stamp = datetime.fromisoformat(row['timestamp']).timestamp()
                    if stamp >= started:
                        decisions.setdefault(row['scope'], stamp * 1000)
            assert set(decisions) == {'life_spot', 'life_perpetual'}, 'Both sampled pause decisions must be observable'
            pause_cancel_ms, pause_working = {}, {}
            # Joint readiness closes both markets on the first pause. Earlier
            # spread widening may already have removed every working order.
            decision = min(decisions.values())
            for instrument in ('LIFE-USDT', 'LIFE-USDT-SWAP'):
                terminals = [int(row['cancel_terminal_ms']) for row in ledger['orders']
                             if row['instId'] == instrument and int(row.get('cancel_terminal_ms', 0)) > decision]
                pause_working[instrument] = len(terminals)
                pause_cancel_ms[instrument] = max(stamp - decision for stamp in terminals) if terminals else None
            assert all(value is None or value <= 5000 for value in pause_cancel_ms.values()), pause_cancel_ms
            accepted = len(ledger['accepted'])
            harness.control(candle_stale=True)
            time.sleep(6)
            assert not working() and len(harness.ledger()['accepted']) == accepted
            assert harness.ledger()['positions'] == positions
            recovery = time.monotonic()
            harness.control(candle_stale=False, candle_width='.005')
            harness.wait_running(port)
            poll(lambda: len(defense_states()) == 2 and all(row.get('state') == 'normal' for row in defense_states()),
                 description='loaded fresh candles recover normal sizing')
            assert not harness.state()['recovery_state']['volatility_defense']['pauses']
            proof.update(stimulus_to_terminal_cancel_ms=cancel_ms,
                         latency_scope='Conservative upper bound: input precedes controller restriction; first terminal proof, not duplicate acknowledgment time',
                         working_at_joint_pause=pause_working, joint_pause_to_terminal_cancel_ms=pause_cancel_ms,
                         reduced_allocation_verified=True,
                         pause_checkpoint_count=2, no_submissions_during_stale_pause=True,
                         positions_preserved=True, recovery_seconds=time.monotonic() - recovery)

        window('volatility-defense', defense, duration=90, details={'defense': proof}, clean_reads=True)
        verify(harness, port)

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
