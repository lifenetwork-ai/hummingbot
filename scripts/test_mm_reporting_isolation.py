"""Observe trading progress during bounded real reporting timeouts.

This test uses the existing simulator, PostgreSQL and shipping image. A test-only
API SQL hook injects a server-side wait or CPU-heavy expansion. Neither case
establishes universal isolation or certifies Cloud Run capacity.
"""
import argparse
import hashlib
import json
import select
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

from scripts.mm_load_preparation import external_book_levels, prepare_history  # noqa: E402
from scripts.test_e2e_offline import Harness, docker, poll  # noqa: E402
from scripts.test_mm_load import assets_for, command, read_metrics, seed_history, verify_ledger  # noqa: E402


def wait_running_metrics(harness, bot):
    def running():
        row = read_metrics(harness, bot)
        return row if row and row.get('runtime_running') else None

    return poll(running, description='running process metrics')


class DurableFillReader:
    """One prewarmed independent backend; no Docker/connection startup in the fault window."""

    def __init__(self, pg_container):
        self.process = subprocess.Popen(
            ['docker', 'exec', '-i', pg_container, 'psql', '-XAt', '-v', 'ON_ERROR_STOP=1',
             '-U', 'postgres', '-d', 'hbot_test_offline'],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        try:
            assert self.read("SELECT json_build_object('ready', true)") == {'ready': True}
        except BaseException:
            self.close()
            raise

    def read(self, statement):
        self.process.stdin.write(statement + ';\n')
        self.process.stdin.flush()
        assert select.select([self.process.stdout], [], [], 5)[0], 'Durable-fill observer backend timed out'
        return json.loads(self.process.stdout.readline())

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)
        self.process.stdin.close()
        self.process.stdout.close()


def restore_market_fixture(harness, baseline):
    """Restore external depth as well as YAML; guards need independent L2."""
    if baseline.get('external_book_levels', 1) == 2:
        quantity = baseline.get('external_book_base_quantity')
        fixtures = json.loads((harness.assets / 'fixtures.json').read_text()) if quantity is not None else {}
        harness.control(book_overrides=external_book_levels(fixtures, base_quantity=quantity))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True, help="Retained verified-image load result directory")
    parser.add_argument("--fault-mode", choices=("wait", "cpu"), default="wait",
                        help="Inject a server wait or CPU-heavy expansion into one daily query")
    parser.add_argument('--timeouts', type=int, choices=(1, 2), default=2,
                        help='Two timeouts exhaust the bounded retry (default); use 1 to test recovery')
    parser.add_argument('--report', choices=('daily', 'overview'), default='daily')
    parser.add_argument('--memory-mib', type=int, choices=(1024, 2048), default=2048)
    parser.add_argument('--expect-retry', action='store_true',
                        help='Require a single timeout to recover without stale replies; use --timeouts=1')
    parser.add_argument("--supervision-source-overlay", action="store_true",
                        help="Diagnostic only: mount the two local supervision modules over the base image")
    args = parser.parse_args()
    if args.report == 'overview' and (args.fault_mode != 'wait' or args.expect_retry):
        parser.error('Overview uses --fault-mode=wait without the daily --expect-retry option')
    if args.expect_retry and args.timeouts != 1:
        parser.error('--expect-retry requires --timeouts=1')
    baseline = json.loads((args.baseline / "result.json").read_text())
    assert baseline["enhancements"] and baseline["volatility_defense"]
    image = baseline["image_id"]
    report = ROOT / "build" / "mm-reporting-isolation" / (time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6])
    report.mkdir(parents=True)
    evidence = {"image_id": image, "scope": __doc__, "report": args.report, "fault_mode": args.fault_mode, "passed": False, "release_scope_complete": False,
                "baseline_passed": baseline.get("passed"), "duration_seconds": 120,
                "injected_timeouts": args.timeouts, "expect_retry": args.expect_retry}
    overlays = [ROOT / "hummingbot/cli" / name for name in ("process_supervision.py", "service_processes.py")] if args.supervision_source_overlay else []
    evidence["supervision_source_overlay_sha256"] = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                                                     for path in overlays}
    evidence["unmodified_runtime_image"] = not bool(overlays)
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
            if args.report == 'overview':
                health_source += '''
import time
from pathlib import Path
_original_response_problem = response_problem
def response_problem(path, status, body):
    if path.split('?', 1)[0] == '/status' and isinstance(body, dict):
        fees = ((body.get('overview') or {}).get('session') or {}).get('fees') or {}
        target = Path('/tmp/overview-fee-observations.jsonl')
        if not target.exists() or target.stat().st_size < 262144:
            with target.open('a') as stream:
                stream.write(json.dumps(dict(at=time.time(), stale=fees.get('stale'),
                    reason=fees.get('unavailable_reason'), has_value=fees.get('value') is not None)) + '\\n')
    return _original_response_problem(path, status, body)
'''
            health.write_text(health_source)
            service = assets / "load_service.py"
            source = service.read_text()
            injection = (ROOT / "test/e2e/offline/slow_reporting_injection.py").read_text()
            injection = injection.replace('SLOW_REPORT_MODE = "wait"', f'SLOW_REPORT_MODE = "{args.fault_mode}"')
            injection = injection.replace('SLOW_REPORT_TIMEOUTS = 1', f'SLOW_REPORT_TIMEOUTS = {args.timeouts}')
            injection = injection.replace('SLOW_REPORT_TARGET = "daily"', f'SLOW_REPORT_TARGET = "{args.report}"')
            assert source.count("service.main()") == 1
            service.write_text(source.replace("service.main()", injection + "\nservice.main()"))
            evidence["injection_sha256"] = hashlib.sha256(injection.encode()).hexdigest()
            harness = Harness(image, assets, report, "demo")
            if overlays:
                original_run_container = harness.run_container

                def run_container(name, options, **kwargs):
                    if name.endswith('-slow-report'):
                        for path in overlays:
                            options = [*options, '--mount', f'type=bind,source={path},target=/home/hummingbot/{path.relative_to(ROOT)},readonly']
                    return original_run_container(name, options, **kwargs)

                harness.run_container = run_container
            harness.env.update(ADMIN_API_ENABLED="true", ADMIN_BASIC_AUTH_ENABLED="true", ADMIN_API_USERNAME="operator", ADMIN_API_PASSWORD="offline-admin",
                               ADMIN_STATISTICS_CACHE_TTL_SECONDS="15", FUNDING_SYNC_INTERVAL_SECONDS="5")
            if args.report == 'overview':
                import base64
                harness.env.update(EXCHANGE_CREDENTIALS_SOURCE='postgres_kms', KMS_PROVIDER='local',
                                   KMS_LOCAL_KEY_ID='offline-reporting', KMS_LOCAL_SECRET_KEY=base64.b64encode(b'K' * 32).decode())
                for name in tuple(harness.env):
                    if name.startswith('HBOT_OKX'):
                        harness.env.pop(name)
            bot = None
            try:
                harness.setup()
                restore_market_fixture(harness, baseline)
                evidence['external_book_levels'] = baseline.get('external_book_levels', 1)
                evidence['external_book_base_quantity'] = baseline.get('external_book_base_quantity')
                harness.control(include_own_orders=True)
                docker("update", "--cpus", "1", "--memory", "512m", "--memory-swap", "512m", harness.simulator)
                docker("update", "--cpus", "2", "--memory", "512m", "--memory-swap", "512m", harness.pg)
                bot, port = harness.start("slow-report", instrumented=True, cpus=2, memory_mib=args.memory_mib)
                docker("update", "--memory", str(args.memory_mib) + 'm', "--memory-swap", str(args.memory_mib) + 'm', bot)
                if args.report == 'overview':
                    from test.e2e.offline.security_fixture import configure_connections
                    configure_connections(harness, port)
                    evidence['credentials_source'] = 'managed_local_kms_offline_keys'
                command(harness, port, "start", market_mode="spot", selection="fixed", profile_id="sideways", duration_seconds=600)
                poll(lambda: bool(harness.state()["recovery_state"].get("spot_outflow", {}).get("binding")),
                     description="empty-DB outflow origin")
                command(harness, port, "stop")
                evidence['history_preparation'] = {}
                prepare_history(harness, bot, baseline['history_orders'], active_outflow=True,
                                seed=seed_history, evidence=evidence['history_preparation'])
                command(harness, port, "start", market_mode="both", selection="fixed", profile_id="follow_market", duration_seconds=600)
                poll(lambda: len([row for row in harness.ledger()["orders"] if row["state"] == "live"]) == 12,
                     description="both markets have working quotes")
                metrics = wait_running_metrics(harness, bot)
                before = harness.state()
                assert metrics["runtime_running"] and not before["recovery_state"].get("halt")
                status, daily = harness.admin(port, "/statistics/daily")
                assert status == 200 and daily["freshness"] == "fresh"
                if args.report == 'daily':
                    docker("exec", bot, "touch", "/tmp/slow-daily-arm")
                client = harness.prefix + "-load-client"
                harness.run_container(client, ["--platform", "linux/amd64", "--cpus", "1", "--memory", "512m",
                                               "--memory-swap", "512m", "--read-only", "--tmpfs", "/tmp:rw,nosuid,size=128m",
                                               "--mount", f"type=bind,source={assets},target=/test-assets,readonly"],
                                      command=["python", "-c", "import time;time.sleep(20000)"])
                from scripts.test_mm_load import run_window
                witness = {}
                witness_reader = DurableFillReader(harness.pg)
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
                        # Log emission follows the injected start marker. This
                        # conservative cutoff avoids a late Docker file read;
                        # final proof still uses actual fault start/end clocks.
                        start = datetime.fromisoformat(event["timestamp"].replace("Z", "+00:00")).timestamp()
                        started_ms = int(start * 1000) + 1
                        deadline = time.monotonic() + 2.5
                        while time.monotonic() < deadline and not observer_stop.is_set():
                            row = witness_reader.read(
                                "SELECT json_build_object('observed_at',extract(epoch from clock_timestamp()),"
                                "'fills',count(*)) FROM trade_fills WHERE config_file_path <> 'load-history.yaml' "
                                f"AND timestamp >= {started_ms}")
                            if row["fills"]:
                                witness.update(row)
                                return
                            observer_stop.wait(.15)
                        return

                observer = threading.Thread(target=observe_durable_fill, daemon=True)
                observer.start()
                arm_timer = None
                if args.report == 'overview':
                    # Unlike daily(), the fee worker progresses without a UI
                    # request. Start the workload/witness before arming its SQL.
                    arm_timer = threading.Timer(5, lambda: docker('exec', bot, 'touch', '/tmp/slow-daily-arm'))
                    arm_timer.daemon = True
                    arm_timer.start()
                print("Slow-report isolation evidence:", report, flush=True)
                try:
                    data = run_window(harness, bot, client, "slow-report", 120, report, options=("--operations",))
                finally:
                    if arm_timer is not None:
                        arm_timer.cancel()
                        arm_timer.join(timeout=10)
                    observer_stop.set()
                    log_stream.terminate()
                    try:
                        log_stream.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        log_stream.kill()
                        log_stream.wait(timeout=5)
                    observer.join(timeout=10)
                    witness_reader.close()
                assert not observer.is_alive(), "Durable-fill observer did not finish"
                fault = json.loads(docker("exec", bot, "cat", "/tmp/slow-daily-result.json").stdout)
                attempts = json.loads(docker('exec', bot, 'cat', '/tmp/slow-daily-attempts.json').stdout)
                stale = [json.loads(line) for line in docker("exec", client, "cat", "/tmp/slow-report-stale.jsonl", check=False).stdout.splitlines()]
                evidence['fault_attempts'] = attempts
                if args.report == 'overview':
                    observations = [json.loads(line) for line in docker('exec', client, 'cat', '/tmp/overview-fee-observations.jsonl').stdout.splitlines()]
                    evidence['overview_fee_observations'] = observations
                    assert any(item['stale'] and fault['started_at'] <= item['at'] <= attempts[-1]['finished_at'] + 8
                               for item in observations), 'No degraded Overview fee observation during the SQL fault'
                evidence["observation"] = dict(fault=fault, witness=witness, stale=stale,
                                               market_updates_per_second=data["market_updates_per_second"],
                                               fills_per_second=data["fills_per_second"],
                                               unexpected_errors=data["unexpected_errors"], errors=data["errors"])
                (report / "result.json").write_text(json.dumps(evidence, indent=2) + "\n")
                assert fault["sqlstate"] == "57014" and fault["finished_at"] > fault["started_at"]
                assert fault['report_target'] == args.report
                assert len(attempts) == args.timeouts
                assert all(item.get('sqlstate') == '57014' and item['finished_at'] > item['started_at'] for item in attempts)
                assert witness.get("fills", 0) > 0 and fault["started_at"] <= float(witness["observed_at"]) <= fault["finished_at"], \
                    "No independently observed durable fill while the report query was blocked"
                assert data["unexpected_errors"] == len(data["errors"])
                assert len(stale) == data["unexpected_errors"]
                if args.report == 'overview':
                    assert not stale and data['unexpected_errors'] == 0
                elif args.expect_retry:
                    assert not stale
                else:
                    assert stale
                assert all(item["freshness"] == "stale" and 0 <= item["observation_age_seconds"] < 60
                           and 1 <= item["retry_after_seconds"] <= 5 and item["server_time"] for item in stale)
                assert len({item["server_time"] for item in stale}) == (0 if args.expect_retry or args.report == 'overview' else 1)
                assert all(item.get("reason") == "daily_statistics_degraded" and item.get("status") == 200
                           and fault["started_at"] <= item["at"] <= attempts[-1]["finished_at"] + 8 for item in data["errors"])
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
                if args.report == 'overview':
                    def overview_fresh():
                        code, body = harness.admin(port, '/status')
                        fees = ((body.get('overview') or {}).get('session') or {}).get('fees') or {}
                        return code == 200 and fees.get('value') is not None and fees.get('stale') is False
                    poll(overview_fresh, timeout=15, description='Overview fees recovered after SQL fault')
                command(harness, port, "stop")
                assert all(row["state"] in ("filled", "canceled") for row in harness.ledger()["orders"])
                unexpected_logs = []
                retry_events = []
                for line in docker("logs", bot).stdout.splitlines():
                    try:
                        row = json.loads(line)
                        timestamp = datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00")).timestamp()
                        if row.get('event') in ('admin_daily_retry', 'admin_daily_retry_recovered'):
                            retry_events.append({key: row.get(key) for key in ('timestamp', 'event', 'severity')})
                        if row.get("severity") in ("ERROR", "CRITICAL") and fault["started_at"] <= timestamp <= attempts[-1]["finished_at"] + 8:
                            unexpected_logs.append({key: row.get(key) for key in ("timestamp", "severity", "event", "message")})
                    except (ValueError, KeyError, TypeError):
                        continue
                evidence["unexpected_error_logs_during_fault"] = unexpected_logs
                evidence['retry_events'] = retry_events
                assert not unexpected_logs
                if args.expect_retry:
                    assert any(row['event'] == 'admin_daily_retry_recovered' for row in retry_events)
                assert data["fills_per_second"] >= 1 - 1 / data["duration_seconds"]
                assert data["market_updates_per_second"] >= 100 - 4 / data["duration_seconds"]
                evidence.update(passed=True, fault=fault, stale_read_count=data["unexpected_errors"],
                                stale_metadata_bounded=True if stale else None,
                                stale_original_server_time_preserved=True if stale else None,
                                transient_timeout_recovered_fresh=args.expect_retry,
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
