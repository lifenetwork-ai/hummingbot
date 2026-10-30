"""Observe trading progress while one real daily-report statement times out.

This test uses the existing simulator, PostgreSQL and shipping image. A test-only
API SQL hook injects one server-side wait or CPU-heavy expansion. Neither case
establishes universal isolation or certifies Cloud Run capacity.
"""
import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.test_e2e_offline import Harness, docker, poll  # noqa: E402
from scripts.test_mm_load import assets_for, command, read_metrics, seed_history, verify_ledger  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True, help="Retained e526 full-load attempt")
    parser.add_argument("--fault-mode", choices=("wait", "cpu"), default="wait",
                        help="Inject a server wait or CPU-heavy expansion into one daily query")
    args = parser.parse_args()
    baseline = json.loads((args.baseline / "result.json").read_text())
    assert baseline["enhancements"] and baseline["volatility_defense"]
    image = baseline["image_id"]
    report = ROOT / "build" / "mm-reporting-isolation" / (time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6])
    report.mkdir(parents=True)
    evidence = {"image_id": image, "scope": __doc__, "fault_mode": args.fault_mode, "passed": False, "release_scope_complete": False,
                "baseline_passed": baseline.get("passed"), "duration_seconds": 120}
    try:
        with tempfile.TemporaryDirectory(prefix="hbot-slow-report-") as temporary:
            assets = Path(temporary)
            assets_for(assets)
            (assets / "life_mm_profiles.yaml").write_bytes((args.baseline / "effective-life-mm-profiles.yaml").read_bytes())
            for name in ("life_mm_profiles.yaml", "load_client.py", "load_health.py", "exchange.py", "fixtures.json"):
                assert hashlib.sha256((assets / name).read_bytes()).hexdigest() == baseline["fixture_sha256"][name]
            health = assets / "load_health.py"
            health_source = health.read_text()
            health_source = health_source.replace('"""Semantic failures count even when an endpoint returns HTTP 200."""',
                                                  '"""Semantic failures and test-only stale metadata."""\nimport json')
            health_source = health_source.replace("            return 'daily_statistics_degraded'", """            with open('/tmp/slow-report-stale.jsonl', 'a') as stream:
                record = {key: body.get(key) for key in ('freshness', 'observation_age_seconds', 'retry_after_seconds', 'server_time')} if isinstance(body, dict) else {}
                stream.write(json.dumps(record) + '\\n')
            return 'daily_statistics_degraded'""")
            assert "slow-report-stale.jsonl" in health_source
            health.write_text(health_source)
            service = assets / "load_service.py"
            source = service.read_text()
            injection = (ROOT / "test/e2e/offline/slow_reporting_injection.py").read_text()
            injection = injection.replace('SLOW_REPORT_MODE = "wait"', f'SLOW_REPORT_MODE = "{args.fault_mode}"')
            assert source.count("service.main()") == 1
            service.write_text(source.replace("service.main()", injection + "\nservice.main()"))
            evidence["injection_sha256"] = hashlib.sha256(injection.encode()).hexdigest()
            harness = Harness(image, assets, report, "demo")
            harness.env.update(ADMIN_API_ENABLED="true", ADMIN_API_USERNAME="operator", ADMIN_API_PASSWORD="offline-admin",
                               ADMIN_STATISTICS_CACHE_TTL_SECONDS="15", FUNDING_SYNC_INTERVAL_SECONDS="5")
            bot = None
            try:
                harness.setup()
                harness.control(include_own_orders=True)
                docker("update", "--cpus", "1", "--memory", "512m", "--memory-swap", "512m", harness.simulator)
                docker("update", "--cpus", "2", "--memory", "512m", "--memory-swap", "512m", harness.pg)
                bot, port = harness.start("slow-report", instrumented=True, cpus=2)
                docker("update", "--memory", "2g", "--memory-swap", "2g", bot)
                command(harness, port, "start", market_mode="spot", selection="fixed", profile_id="sideways", duration_seconds=600)
                poll(lambda: bool(harness.state()["recovery_state"].get("spot_outflow", {}).get("binding")),
                     description="empty-DB outflow origin")
                command(harness, port, "stop")
                seed_history(harness, baseline["history_orders"], active_outflow=True)
                command(harness, port, "start", market_mode="both", selection="fixed", profile_id="follow_market", duration_seconds=600)
                poll(lambda: len([row for row in harness.ledger()["orders"] if row["state"] == "live"]) == 12,
                     description="both markets have working quotes")
                metrics = poll(lambda: read_metrics(harness, bot), description="initial process metrics")
                before = harness.state()
                assert metrics["runtime_running"] and not before["recovery_state"].get("halt")
                status, daily = harness.admin(port, "/statistics/daily")
                assert status == 200 and daily["freshness"] == "fresh"
                docker("exec", bot, "touch", "/tmp/slow-daily-arm")
                client = harness.prefix + "-load-client"
                harness.run_container(client, ["--platform", "linux/amd64", "--cpus", "1", "--memory", "512m",
                                               "--memory-swap", "512m", "--read-only", "--tmpfs", "/tmp:rw,nosuid,size=128m",
                                               "--mount", f"type=bind,source={assets},target=/test-assets,readonly"],
                                      command=["python", "-c", "import time;time.sleep(20000)"])
                from scripts.test_mm_load import run_window
                witness = {}
                observer_stop = threading.Event()
                log_stream = subprocess.Popen(["docker", "logs", "--follow", "--since", str(int(time.time())), bot],
                                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)

                def observe_durable_fill():
                    for line in log_stream.stdout:
                        if observer_stop.is_set():
                            return
                        try:
                            event = json.loads(line)
                        except ValueError:
                            continue
                        if event.get("event") != "test_slow_daily_started":
                            continue
                        start = json.loads(docker("exec", bot, "cat", "/tmp/slow-daily-result.json").stdout)["started_at"]
                        started_ms = int(start * 1000) + 1
                        deadline = time.monotonic() + 2.5
                        while time.monotonic() < deadline and not observer_stop.is_set():
                            row = json.loads(harness.sql("SELECT json_build_object('observed_at',extract(epoch from clock_timestamp()),"
                                                         "'fills',count(*)) FROM trade_fills WHERE config_file_path <> 'load-history.yaml' "
                                                         f"AND timestamp >= {started_ms}"))
                            if row["fills"]:
                                witness.update(row)
                                return
                            observer_stop.wait(.15)
                        return

                observer = threading.Thread(target=observe_durable_fill, daemon=True)
                observer.start()
                print("Slow-report isolation evidence:", report, flush=True)
                try:
                    data = run_window(harness, bot, client, "slow-report", 120, report, options=("--operations",))
                finally:
                    observer_stop.set()
                    log_stream.terminate()
                    try:
                        log_stream.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        log_stream.kill()
                        log_stream.wait(timeout=5)
                    observer.join(timeout=10)
                assert not observer.is_alive(), "Durable-fill observer did not finish"
                fault = json.loads(docker("exec", bot, "cat", "/tmp/slow-daily-result.json").stdout)
                stale = [json.loads(line) for line in docker("exec", client, "cat", "/tmp/slow-report-stale.jsonl").stdout.splitlines()]
                evidence["observation"] = dict(fault=fault, witness=witness, stale=stale,
                                               market_updates_per_second=data["market_updates_per_second"],
                                               fills_per_second=data["fills_per_second"],
                                               unexpected_errors=data["unexpected_errors"], errors=data["errors"])
                (report / "result.json").write_text(json.dumps(evidence, indent=2) + "\n")
                assert fault["sqlstate"] == "57014" and fault["finished_at"] > fault["started_at"]
                assert witness.get("fills", 0) > 0 and fault["started_at"] <= float(witness["observed_at"]) <= fault["finished_at"], \
                    "No independently observed durable fill while the report query was blocked"
                assert data["unexpected_errors"] == len(data["errors"]) > 0
                assert len(stale) == data["unexpected_errors"]
                assert all(item["freshness"] == "stale" and 0 <= item["observation_age_seconds"] < 60
                           and 1 <= item["retry_after_seconds"] <= 5 and item["server_time"] for item in stale)
                assert len({item["server_time"] for item in stale}) == 1
                assert all(item.get("reason") == "daily_statistics_degraded" and item.get("status") == 200
                           and fault["started_at"] <= item["at"] <= fault["finished_at"] + 8 for item in data["errors"])
                assert set(data["statuses"]) == {"200"} and set(data["auxiliary"]["statuses"]) == {"200"}
                samples = json.loads((report / "slow-report-runtime-minutes.json").read_text())
                assert samples and all(sample["runtime_phase"] == "running" for sample in samples)
                assert all(sample["processes"]["engine"]["pid"] == metrics["processes"]["engine"]["pid"] for sample in samples)
                after = harness.state()
                assert not after["recovery_state"].get("halt") and after["owner_uuid"] == before["owner_uuid"]
                assert after["owner_generation"] == before["owner_generation"] and after["mm_session"]["id"] == before["mm_session"]["id"]
                ledger = harness.ledger()
                during = [item for item in ledger["fills"] if fault["started_at"] <= int(item["ts"]) / 1000 <= fault["finished_at"]]
                assert during, "No exchange fill observed while the reporting query was blocked"
                reconciled = verify_ledger(harness, port)
                status, fresh = harness.admin(port, "/statistics/daily")
                assert status == 200 and fresh["freshness"] == "fresh"
                command(harness, port, "stop")
                assert all(row["state"] in ("filled", "canceled") for row in harness.ledger()["orders"])
                unexpected_logs = []
                for line in docker("logs", bot).stdout.splitlines():
                    try:
                        row = json.loads(line)
                        timestamp = datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00")).timestamp()
                        if row.get("severity") in ("ERROR", "CRITICAL") and fault["started_at"] <= timestamp <= fault["finished_at"] + 8:
                            unexpected_logs.append({key: row.get(key) for key in ("timestamp", "severity", "event", "message")})
                    except (ValueError, KeyError, TypeError):
                        continue
                evidence["unexpected_error_logs_during_fault"] = unexpected_logs
                assert not unexpected_logs
                assert data["fills_per_second"] >= 1 - 1 / data["duration_seconds"]
                assert data["market_updates_per_second"] >= 100 - 4 / data["duration_seconds"]
                evidence.update(passed=True, fault=fault, stale_read_count=data["unexpected_errors"],
                                stale_metadata_bounded=True, stale_original_server_time_preserved=True,
                                exchange_fills_during_timeout=len(during), durable_fill_witness=witness, ledger=reconciled,
                                engine_pid_unchanged=True, ownership_and_session_unchanged=True, no_halt=True,
                                later_daily_fresh=True, clean_stop=True,
                                observed_market_updates_per_second=data["market_updates_per_second"],
                                observed_fills_per_second=data["fills_per_second"],
                                limitation=("Server-side wait holds one API connection but does not model CPU-heavy SQL or all Cloud Run workloads."
                                            if args.fault_mode == "wait" else
                                            "Synthetic CPU-heavy SQL does not establish isolation for all queries or Cloud Run workloads."))
            finally:
                if bot is not None:
                    try:
                        if harness.state()["desired_state"] != "stopped":
                            command(harness, port, "stop")
                    except Exception as cleanup_error:
                        evidence["stop_cleanup_error"] = type(cleanup_error).__name__
                harness.cleanup()
    except Exception as error:
        evidence.update(failure_type=type(error).__name__, failure=str(error))
        raise
    finally:
        (report / "result.json").write_text(json.dumps(evidence, indent=2) + "\n")
        print("Slow-report isolation evidence:", report, flush=True)


if __name__ == "__main__":
    main()
