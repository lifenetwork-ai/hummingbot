"""Opt-in deployment-image load/soak on an internal network, with dummy keys only."""
import argparse
import hashlib
import json
import math
import platform
import sys
import tempfile
import threading
import time
import uuid
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from test.e2e.offline.load_incidents import capture_limit  # noqa: E402

from scripts.mm_load_budgets import memory_retention, soak_violations, violations  # noqa: E402
from scripts.mm_load_faults import fault_windows  # noqa: E402
from scripts.mm_load_ledger import funding_ledger_violations, ledger_violations  # noqa: E402
from scripts.mm_load_processes import merge_metrics  # noqa: E402
from scripts.test_e2e_offline import Harness, docker, pin_manifest, poll  # noqa: E402


def save_fault_progress(report, evidence, name, data):
    """Keep completed fault evidence even if preparation of the next case fails."""
    evidence.setdefault("faults", {})[name] = data
    (report / ("fault-" + name + ".json")).write_text(json.dumps(data, indent=2))
    (report / "result.json").write_text(json.dumps(evidence, indent=2))


def seed_history(harness, count, *, active_outflow=False):
    """Reuse the PG performance fixture's generate_series bulk insert pattern.

    Add recorder fills in natural decimal units, dated yesterday and closed so
    the bounded current reconciliation scope does not include synthetic history.
    E6 places their identical timestamp 120 seconds before a one-day window expires,
    retaining the same row count and historical reconciliation exclusion.
    """
    assert 0 <= count <= 1000000
    stamp = "(extract(epoch from transaction_timestamp())*1000)::bigint-86400000+120000" if active_outflow else "(extract(epoch from clock_timestamp())*1000)::bigint-86400000"
    harness.sql(f"""
        INSERT INTO orders (id, config_file_path, strategy, market, symbol, base_asset, quote_asset,
            creation_timestamp, order_type, amount, leverage, price, last_status, last_update_timestamp, creation_pending)
        SELECT 'load-history-' || n, 'load-history.yaml', 'v2_with_controllers',
            CASE WHEN n%2=0 THEN 'okx' ELSE 'okx_perpetual' END, 'LIFE-USDT', 'LIFE', 'USDT',
            {stamp}, 'LIMIT_MAKER',0.001,1,0.1,
            'BuyOrderCompleted',{stamp},false
        FROM generate_series(1,{count}) n;
        INSERT INTO trade_fills (config_file_path,strategy,market,symbol,base_asset,quote_asset,timestamp,
            order_id,trade_type,order_type,price,amount,leverage,trade_fee,trade_fee_in_quote,exchange_trade_id,position)
        SELECT config_file_path,strategy,market,symbol,base_asset,quote_asset,creation_timestamp,id,'BUY',order_type,
            price,amount,leverage,'{{"percent":"0","flat_fees":[{{"token":"USDT","amount":"0.000001"}}]}}'::json,0.000001,id,'OPEN'
        FROM orders WHERE id LIKE 'load-history-%'; ANALYZE orders; ANALYZE trade_fills;
    """)


def assets_for(path):
    from test.e2e.offline.build_fixtures import write_fixtures

    import yaml
    path.chmod(0o755)
    for name in ("exchange.py", "load_service.py", "load_entrypoint.py", "load_process_metrics.py", "load_trace.py", "load_incidents.py", "load_health.py", "load_client.py", "runtime_smoke.py", "pmm.yml", "controller-spot.yml", "controller-perpetual.yml", "script-perpetual.yml"):
        (path / name).write_bytes((ROOT / "test/e2e/offline" / name).read_bytes())
    (path / "pmm-revised.yml").write_bytes((path / "pmm.yml").read_bytes())
    write_fixtures(path / "fixtures.json")
    profiles = yaml.safe_load((ROOT / "deploy/cloudrun/conf/life_mm_profiles.yaml").read_text())
    (path / "life_mm_profiles.yaml").write_text(yaml.safe_dump(profiles))


def command(harness, port, action, **values):
    started = time.perf_counter()
    receipt = harness.mm_command(port, action, **values)
    return time.perf_counter() - started, receipt


def read_metrics(harness, bot):
    raw = docker("exec", bot, "cat", "/tmp/load-metrics.json", check=False)
    if raw.returncode:
        return None
    engine = json.loads(raw.stdout)
    if engine.get('process_role') != 'engine':
        return engine
    raw = docker('exec', bot, 'cat', '/tmp/load-metrics-api.json', check=False)
    return merge_metrics(engine, json.loads(raw.stdout) if raw.returncode == 0 else None, now=time.time())


def verify_ledger(harness, port, *, require_running=True):
    ledger = harness.ledger()
    rows = json.loads(harness.sql("SELECT COALESCE(json_agg(t),'[]') FROM (SELECT market,order_id,exchange_trade_id,amount::text AS amount,price::text AS price,trade_fee,trade_fee_in_quote::text AS trade_fee_in_quote FROM trade_fills WHERE config_file_path <> 'load-history.yaml') t"))
    (harness.report / "recorder-load-fills.json").write_text(json.dumps(rows, indent=2))
    assert not ledger_violations(ledger, rows), "Independent fill/fee/unit comparison failed"
    assert not ledger["unknown"]
    funding_rows = json.loads(harness.sql("SELECT COALESCE(json_agg(t),'[]') FROM (SELECT account_id,environment,bill_id,symbol,timestamp,amount::text AS amount,currency FROM funding_bills) t"))
    assert not funding_ledger_violations(ledger.get("funding_bills", []), funding_rows,
                                         account_id="offline-account-1", environment=harness.env["OKX_TRADING_ENV"]), "Funding bill ledger mismatch"
    expected_positions = {(row["instId"].removesuffix("-SWAP"), row["posSide"].upper()): Decimal(row["pos"]) * Decimal("0.01") for row in ledger["positions"]}

    def positions_match():
        markets = harness.admin(port, "/positions")[1]["markets"]
        items = next(row for row in markets if row["market"] == "perpetual")["items"]
        actual = {(row["pair"], row["side"]): abs(Decimal(row["amount"])) for row in items}
        return actual == expected_positions
    poll(positions_match, description="load account positions match independent exchange")
    if require_running:
        def holds_match():
            metrics = read_metrics(harness, port)
            if not metrics or not metrics.get("runtime_running") or metrics.get("positions_unsettled"):
                return False
            actual = {(row["pair"], row["side"]): Decimal(row["amount"]) for row in metrics.get("expected_positions", [])}
            return actual == expected_positions
        poll(holds_match, timeout=20, description="existing V2 held plus active exposure matches independent exchange")
    daily = harness.admin(port, "/statistics/daily")[1]
    # A cached day series can lag the final fill only within the declared TTL.

    def totals_match():
        nonlocal daily
        daily = harness.admin(port, "/statistics/daily")[1]
        return sum(day["fill_count"] for market in daily["markets"] for day in market["days"]) == len(rows) + int(harness.sql("SELECT count(*) FROM trade_fills WHERE config_file_path='load-history.yaml'"))
    # Post-window ledger convergence respects the declared reporting TTL. This
    # does not alter request/command deadlines or measured load budgets.
    cache_ttl = float(harness.env.get('ADMIN_STATISTICS_CACHE_TTL_SECONDS', '5'))
    poll(totals_match, timeout=max(15, cache_ttl + 5), description="large-history statistics retain all unique load fills")
    assert harness.sql("SELECT count(*) FROM daily_balance_snapshots") != "0"
    return dict(unique_exchange_fills=len(ledger["fills"]), unique_recorder_fills=len(rows), positions_matched=True,
                identified_funding_bills=len(funding_rows),
                daily_snapshots=int(harness.sql("SELECT count(*) FROM daily_balance_snapshots")))


def run_window(harness, bot, client, name, duration, report, *, options=(), burst=False):
    destination = report / (name + ".json")
    per_minute = []
    finished = threading.Event()

    def monitor():
        while not finished.wait(1 if burst else 60):
            row = read_metrics(harness, bot)
            if row:
                if not burst or len(per_minute) % 60 == 0:
                    row["docker"] = docker("stats", "--no-stream", "--format", "{{json .}}", bot).stdout.strip()
                    row["database"] = json.loads(harness.sql("SELECT json_build_object('connections',count(*) FILTER(WHERE backend_type='client backend'),'active',count(*) FILTER(WHERE state='active' AND backend_type='client backend'),'parallel_workers',count(*) FILTER(WHERE backend_type='parallel worker'),'waiting_locks',count(*) FILTER(WHERE wait_event_type='Lock')) FROM pg_stat_activity WHERE datname=current_database()"))
                per_minute.append(row)
                (report / (name + "-runtime-minutes.json")).write_text(json.dumps(per_minute, indent=2))
                if not burst or len(per_minute) % 60 == 0:
                    print(f"{name}: sample {len(per_minute)} RSS={row['memory'].get('VmRSS')} bytes", flush=True)
    monitor_thread = threading.Thread(target=monitor, daemon=True)
    monitor_thread.start()
    try:
        result = docker("exec", client, "python", "/test-assets/load_client.py", "--duration", duration,
                        "--output", "/tmp/load-result.json", *options, *(["--burst"] if burst else []), timeout=duration + 60, check=False)
        (report / (name + "-client.log")).write_text(result.stdout + result.stderr)
        if result.returncode:
            metrics = read_metrics(harness, bot)
            (report / (name + "-failed-runtime.json")).write_text(json.dumps(metrics, indent=2))
            raise AssertionError("Load driver failed; inspect retained client log")
        data = json.loads(docker("exec", client, "cat", "/tmp/load-result.json").stdout)
        metrics = read_metrics(harness, bot)
        destination.write_text(json.dumps(data, indent=2))
        assert metrics is not None, "Runtime role metrics missing/stale or process exited; inspect retained evidence"
        data["runtime"] = metrics
        trace_summary = report / "trace" / "client-summary.json"
        if trace_summary.exists():
            data["client_trace"] = json.loads(trace_summary.read_text())
        data["peak_rss_bytes"] = metrics["memory"]["VmHWM"]
        if burst and data["control_results"].get("burst_ended_at"):
            end = data["control_results"]["burst_ended_at"]
            drained = next((row["at"] - end for row in per_minute if row["at"] >= end and
                            row["latest"].get("book_backlog", 1e9) <= 4 and row["latest"].get("event_age_ms", 1e9) <= 1000), None)
            data["burst_drain_seconds"] = drained
            assert drained is not None and drained <= 10, "Burst backlog did not drain within 10s"
        destination.write_text(json.dumps(data, indent=2))
        return data
    finally:
        finished.set()
        monitor_thread.join(timeout=10)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default="hummingbot-cloudrun:local")
    parser.add_argument("--profile", choices=("probe", "sequence", "mixed", "bursts", "soak", "faults", "all"), default="probe")
    parser.add_argument("--probe-seconds", type=int, default=60)
    parser.add_argument("--history-rows", type=int, default=100000)
    parser.add_argument('--cpus', type=int, choices=(1, 2), default=2, help='Total application container CPUs')
    parser.add_argument('--daily-cache-ttl-seconds', type=float, default=15, help='Explicit existing daily reporting policy')
    parser.add_argument("--diagnostic", action="store_true", help="Use local image ID for iteration; cannot serve as final artifact acceptance")
    parser.add_argument("--refresh-seconds", type=int, help="Diagnostic-only accelerated quote refresh")
    parser.add_argument("--trace", action="store_true", help="Bounded request/SQL/commit/loop-stack/PG-wait traces; no parameters or credentials")
    parser.add_argument('--incident-diagnostics', action='store_true', help='Bounded stack/SQL/CPU prehistory automatically retained on failed probes or loop stalls')
    parser.add_argument('--incident-max-captures', type=capture_limit, default=32, help='Test-only incident count within 1..128; keep independent RAM/disk caps')
    parser.add_argument('--gc-diagnostics', action='store_true', help='Minimal bounded full-GC timing in the test wrapper; no GC policy changes')
    parser.add_argument('--market-diagnostics', action='store_true', help='Opt-in bounded simulator burst scheduling/send metrics')
    parser.add_argument("--strict-guards", action="store_true", help="Explicit strict-guard fixture; production defaults remain disabled")
    parser.add_argument("--drawdown", action="store_true", help="Explicit drawdown limits; production defaults remain disabled")
    parser.add_argument("--spot-outflow", action="store_true", help="Explicit outflow policy, bound on an empty DB before historical seeding")
    parser.add_argument("--listing-bootstrap", action="store_true", help="Bounded bootstrap policy with healthy outside-market qualification")
    parser.add_argument("--inventory-skew", action="store_true", help="Spot cash skew using the existing PMM calculation")
    parser.add_argument("--enhancements", action="store_true", help="Explicit E6 all-feature healthy fixture, including completed candles and an active same-time outflow expiry burst")
    parser.add_argument('--volatility-defense', action='store_true', help='Explicit three-level defense fixture; preserve original load rates, durations and budgets')
    parser.add_argument("--keep-going", action="store_true", help="Continue healthy windows after budget violations; final result still fails")
    parser.add_argument("--operations", action="store_true", help="Add three operator-history/configuration reads/s to the existing read mix")
    args = parser.parse_args()
    if args.trace and args.incident_diagnostics:
        parser.error('Use either full tracing or incident diagnostics to keep their overhead distinct')
    if args.incident_max_captures != 32 and not args.incident_diagnostics:
        parser.error('incident-max-captures requires incident-diagnostics')
    if not math.isfinite(args.daily_cache_ttl_seconds) or not 0 <= args.daily_cache_ttl_seconds <= 60:
        parser.error('daily-cache-ttl-seconds must be finite and within 0..60')
    if args.volatility_defense:
        args.strict_guards = True
    if args.enhancements:
        args.inventory_skew = args.listing_bootstrap = args.drawdown = args.spot_outflow = args.strict_guards = True
    if args.listing_bootstrap:
        args.strict_guards = args.drawdown = args.spot_outflow = True
    metadata = json.loads(docker("image", "inspect", args.image).stdout)[0]
    assert metadata["Architecture"] == "amd64"
    # Cached immutable digest from P4 needs no registry/network preparation again.
    diagnostic = args.diagnostic or args.profile in ('probe', 'sequence')
    image = (metadata["Id"] if diagnostic else
             next(iter(metadata["RepoDigests"]), None) or pin_manifest(args.image))
    report = ROOT / "build/mm-load" / (time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6])
    report.mkdir(parents=True)
    evidence = dict(profile=args.profile, diagnostic=diagnostic, image_id=metadata["Id"], manifest=image.split("@", 1)[1] if "@" in image else None,
                    host_architecture=platform.machine(), image_architecture="amd64", emulation=platform.machine() != "x86_64",
                    bot_limits=dict(cpus=args.cpus, memory_bytes=2 * 1024**3), postgres_limits=dict(cpus=2, memory_bytes=512 * 1024**2),
                    simulator_limits=dict(cpus=1, memory_bytes=512 * 1024**2), client_limits=dict(cpus=1, memory_bytes=512 * 1024**2),
                    history_orders=args.history_rows, history_fills=args.history_rows, funding_sync_interval_seconds=5,
                    memory_retention_policy=dict(basis='cgroup_current_minus_file_plus_shmem',
                                                 growth_budget_bytes=64 * 1024**2,
                                                 absolute_peak_budget_bytes=int(1.5 * 1024**3)),
                    enhancements=args.enhancements, active_outflow_history=args.enhancements, history_read_mix="original eleven routes/rates/latency population unchanged; add 1 event + 1 liquidity read/s with the same separate latency/error gates",
                    volatility_defense=args.volatility_defense,
                    operations_reads_per_second=3 if args.operations else 0,
                    tracing=args.trace, strict_quote_guards=args.strict_guards, drawdown=args.drawdown, spot_outflow=args.spot_outflow, listing_bootstrap=args.listing_bootstrap, inventory_skew=args.inventory_skew,
                    gc_diagnostics=args.gc_diagnostics,
                    market_diagnostics=args.market_diagnostics,
                    incident_diagnostics=args.incident_diagnostics,
                    incident_max_captures=args.incident_max_captures,
                    release_scope_complete=False,
                    daily_cache_ttl_seconds=args.daily_cache_ttl_seconds,
                    continue_after_budget_failure=args.keep_going, windows={})
    evidence["source_sha256"] = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in (
        ROOT / "scripts/test_mm_load.py", ROOT / "scripts/test_e2e_offline.py", ROOT / "scripts/analyze_mm_load_trace.py",
        ROOT / "scripts/mm_load_budgets.py", ROOT / "scripts/mm_load_faults.py", ROOT / "scripts/mm_load_ledger.py",
        ROOT / "scripts/mm_load_processes.py", ROOT / "test/e2e/offline/load_entrypoint.py",
        ROOT / "test/e2e/offline/load_service.py", ROOT / "test/e2e/offline/load_process_metrics.py")}
    try:
        with tempfile.TemporaryDirectory(prefix="hbot-mm-load-") as directory:
            assets = Path(directory)
            assets_for(assets)
            if args.spot_outflow:
                import yaml
                config_path = assets / "life_mm_profiles.yaml"
                profiles = yaml.safe_load(config_path.read_text())
                profiles["spot_outflow"] = dict(window_seconds=86400 if args.enhancements else 3600, max_net_outflow_quote="100000", fee_allowance_rate=".01")
                config_path.write_text(yaml.safe_dump(profiles))
            if args.drawdown:
                import yaml
                config_path = assets / "life_mm_profiles.yaml"
                profiles = yaml.safe_load(config_path.read_text())
                profiles.update(max_global_drawdown_quote="1000", max_controller_drawdown_quote="500")
                config_path.write_text(yaml.safe_dump(profiles))
            if args.strict_guards:
                import yaml
                config_path = assets / "life_mm_profiles.yaml"
                profiles = yaml.safe_load(config_path.read_text())
                for market in profiles["markets"].values():
                    market["quote_guards"] = dict(enabled=True, max_deviation_bps="200", resume_deviation_bps="100", recovery_delay_seconds="1")
                config_path.write_text(yaml.safe_dump(profiles))
            if args.listing_bootstrap:
                import yaml
                config_path = assets / "life_mm_profiles.yaml"
                profiles = yaml.safe_load(config_path.read_text())
                for market in profiles["markets"].values():
                    market["initial_anchor"] = ".1"
                    market["quote_guards"].update(mode="bootstrap_then_market", bootstrap=dict(
                        duration_seconds=300, max_anchor_move_bps="1000", max_quote=market["total_amount_quote"],
                        min_external_depth_quote="50", max_market_spread_bps="250", qualification_seconds="1"))
                config_path.write_text(yaml.safe_dump(profiles))
            if args.inventory_skew:
                import yaml
                config_path = assets / "life_mm_profiles.yaml"
                profiles = yaml.safe_load(config_path.read_text())
                # Explicit healthy-workload settings retain both funded sides.
                # Strong asymmetric sizing is checked in the dedicated E2E.
                profiles["markets"]["spot"]["inventory_skew"] = dict(
                    enabled=True, target_base_pct=".01", inventory_range_multiplier="10")
                config_path.write_text(yaml.safe_dump(profiles))
            if args.enhancements:
                import yaml
                config_path = assets / "life_mm_profiles.yaml"
                profiles = yaml.safe_load(config_path.read_text())
                for market in profiles["markets"].values():
                    market["spread_policy"] = dict(fee_floor=True, volatility=dict(
                        enabled=True, interval="1s", length=5, target_natr=".01", min_multiplier=".5", max_multiplier="3",
                        max_age_seconds=5, on_unavailable="wait"))
                config_path.write_text(yaml.safe_dump(profiles))
            if args.volatility_defense:
                from test.e2e.offline.build_fixtures import enable_volatility_defense

                import yaml
                config_path = assets / 'life_mm_profiles.yaml'
                profiles = yaml.safe_load(config_path.read_text())
                enable_volatility_defense(profiles)
                config_path.write_text(yaml.safe_dump(profiles))
            if args.refresh_seconds is not None:
                assert diagnostic and args.refresh_seconds > 0
                import yaml
                config_path = assets / "life_mm_profiles.yaml"
                profiles = yaml.safe_load(config_path.read_text())
                for market in profiles["markets"].values():
                    market["executor_refresh_time"] = args.refresh_seconds
                config_path.write_text(yaml.safe_dump(profiles))
            (report / "effective-life-mm-profiles.yaml").write_bytes((assets / "life_mm_profiles.yaml").read_bytes())
            evidence["fixture_sha256"] = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in assets.iterdir()}
            harness = Harness(image, assets, report, "demo")
            harness.env.update(ADMIN_API_ENABLED="true", ADMIN_API_USERNAME="operator", ADMIN_API_PASSWORD="offline-admin",
                               ADMIN_STATISTICS_CACHE_TTL_SECONDS=str(args.daily_cache_ttl_seconds))
            try:
                harness.setup()
                if args.strict_guards:
                    harness.control(include_own_orders=True)
                pg_image = json.loads(docker("inspect", harness.pg).stdout)[0]["Image"]
                evidence["postgres_image_id"] = pg_image
                evidence["postgres_architecture"] = json.loads(docker("image", "inspect", pg_image).stdout)[0]["Architecture"]
                evidence["postgres_version"] = harness.sql("SHOW server_version")
                evidence["docker_host_resources"] = docker("info", "--format", "cpus={{.NCPU}} memory_bytes={{.MemTotal}}").stdout.strip()
                docker("update", "--cpus", "1", "--memory", "512m", "--memory-swap", "512m", harness.simulator)
                docker("update", "--cpus", "2", "--memory", "512m", "--memory-swap", "512m", harness.pg)
                harness.env["FUNDING_SYNC_INTERVAL_SECONDS"] = "5"
                if args.gc_diagnostics:
                    harness.env['HBOT_LOAD_GC_DIAGNOSTICS'] = '1'
                incident_directory = report / 'incidents' if args.incident_diagnostics else None
                if incident_directory:
                    harness.env['HBOT_LOAD_INCIDENT_MAX_CAPTURES'] = str(args.incident_max_captures)
                    incident_directory.mkdir()
                    incident_directory.chmod(0o777)
                    evidence['incident_policy'] = dict(history_seconds=10, tail_seconds=10, max_records=256,
                                                       max_buffer_bytes_per_role=512 * 1024, max_incidents_per_role=args.incident_max_captures,
                                                       max_disk_bytes_per_role=32 * 1024**2,
                                                       sampling_ms=250, watchdog_poll_ms=50, max_active_spans=16,
                                                       parameters_and_frame_locals=False)
                trace_directory = report / "trace" if args.trace else None
                if trace_directory:
                    trace_directory.mkdir()
                    trace_directory.chmod(0o777)  # Test-only output mount for runtime UID 10001.
                    evidence["trace_policy"] = dict(path=str(trace_directory.relative_to(ROOT)),
                                                    slow_trace_ms=100, loop_stall_ms=200, database_sample_ms=250,
                                                    max_bytes_per_stream=128 * 1024**2, diagnostic_database_connections=1,
                                                    span_cpu_scope="inclusive thread CPU; async spans include interleaved work")
                bot, port = harness.start("load-bot", instrumented=True, trace_directory=trace_directory,
                                          incident_directory=incident_directory, cpus=args.cpus)
                docker("update", "--cpus", args.cpus, "--memory", "2g", "--memory-swap", "2g", bot)
                initial_metrics = poll(lambda: read_metrics(harness, bot), timeout=30, description='complete fresh runtime role metrics')
                assert memory_retention([initial_metrics])['basis'] == 'cgroup_current_minus_file_plus_shmem', \
                    'New load measurements require the complete observed memory breakdown'
                if args.incident_diagnostics:
                    for role_metrics in initial_metrics.get('role_metrics', {'combined': initial_metrics}).values():
                        assert role_metrics.get('incident_capture', {}).get('max_incidents') == args.incident_max_captures, 'Incident capacity not applied to runtime role'
                poll(lambda: bool(harness.state().get("status")), description="load owner status")
                print(f"Local load evidence: {report}", flush=True)
                if args.spot_outflow:
                    # Establish the verified empty-DB origin, not a relabelled
                    # legacy history or a test-only bypass of warmup.
                    command(harness, port, "start", market_mode="spot", selection="fixed", profile_id="sideways", duration_seconds=600)
                    poll(lambda: bool((harness.state()["recovery_state"].get("spot_outflow") or {}).get("binding")),
                         description="verified empty-DB outflow binding before historical fixture insertion")
                    command(harness, port, "stop")
                seed_history(harness, args.history_rows, active_outflow=args.enhancements)
                bill_time = int(time.time() * 1000) - 1000
                harness.control(funding_bills=[dict(billId=str(i), type="8", subType="174" if value[0] != "-" else "173",
                                                    instId="LIFE-USDT-SWAP", ts=str(bill_time), pnl=value, ccy="USDT")
                                               for i, value in ((1, "-0.0000001234567890123456789"), (2, "0.0000000234567890123456789"))])
                controls = {}
                controls["start"], _ = command(harness, port, "start", market_mode="both", selection="fixed", profile_id="follow_market", duration_seconds=86400)
                controls["switch"], _ = command(harness, port, "switch", market_mode="both", selection="fixed", profile_id="follow_market", duration_seconds=86400)
                controls["stop"], _ = command(harness, port, "stop")
                _, _ = command(harness, port, "start", market_mode="both", selection="fixed", profile_id="follow_market", duration_seconds=86400)
                expected_quotes = 12 if args.volatility_defense else 8
                poll(lambda: len([order for order in harness.ledger()["orders"] if order["state"] == "live"]) == expected_quotes,
                     description=f'{expected_quotes} working load quotes')
                if args.enhancements:
                    evidence["active_outflow_initial"] = (harness.state()["status"].get("spot_outflow") or {})
                    assert evidence["active_outflow_initial"].get("fill_count", 0) >= args.history_rows // 2
                client = harness.prefix + "-load-client"
                client_mounts = ["--mount", f"type=bind,source={trace_directory},target=/trace-output"] if args.trace else []
                client_env = {"HBOT_LOAD_TRACE_DIRECTORY": "/trace-output", "HBOT_LOAD_TRACE_DATABASE_URL": harness.env["DATABASE_URL"]} if args.trace else None
                harness.run_container(client, ["--platform", "linux/amd64", "--cpus", "1", "--memory", "512m", "--memory-swap", "512m", "--read-only",
                                               "--tmpfs", "/tmp:rw,nosuid,size=128m", "--mount", f"type=bind,source={assets},target=/test-assets,readonly", *client_mounts],
                                      env=client_env, command=["python", "-c", "import time; time.sleep(20000)"])
                if args.profile == "probe":
                    windows = [("probe", args.probe_seconds)]
                else:
                    windows = ([("warmup", 300), ("mixed", 900)] if args.profile in ("sequence", "mixed", "all") else [])
                    if args.profile in ("bursts", "sequence", "all"):
                        windows.append(("bursts", 90))
                    if args.profile in ("soak", "all"):
                        windows.append(("soak", 3600))
                for name, duration in windows:
                    if name == "soak":
                        _, _ = command(harness, port, "switch", market_mode="both", selection="random", duration_seconds=7200)
                    options = (("--random-soak",) if name == "soak" else ()) + (("--operations",) if args.operations else ())
                    if args.market_diagnostics:
                        options += ('--market-diagnostics',)
                    data = run_window(harness, bot, client, name, duration, report, burst=name == "bursts", options=options)
                    data["command_seconds"] = data["control_results"].get("command_seconds", controls)
                    data["ledger"] = verify_ledger(harness, port)
                    data["violations"] = violations(data, require_rates=name != "soak")
                    if name == "soak":
                        data["rate_scope"] = "observed; random-step admission gaps are intentional; fixed mixed window enforces rates"
                        minute_samples = json.loads((report / (name + "-runtime-minutes.json")).read_text())
                        data["violations"] += soak_violations(minute_samples)
                        try:
                            data['memory_retention'] = memory_retention(minute_samples)
                        except (KeyError, TypeError, ValueError):
                            data['memory_retention'] = dict(error='Incomplete or inconsistent memory breakdown')
                    route_queries = data["runtime"]["route_queries"]
                    assert all(row["max"] <= (3 if "/orders?" in path else 1) for path, row in route_queries.items()
                               if "/orders?" in path or "/fills?" in path or path.endswith("/performance")), "History/performance query count grew with page size"
                    assert data["exchange_account_reads"] <= 2 * duration / 5 + 10, "FE polls multiplied exchange account calls"
                    if args.enhancements:
                        data["enhancement_state"] = {key: harness.state()["status"].get(key) for key in ("spot_outflow", "mm_markets")}
                        routes = data["runtime"]["route_queries"]
                        assert routes["/api/v1/admin/sessions/{id}/events"]["max"] == 2
                        assert routes["/api/v1/admin/statistics/liquidity"]["max"] == 1
                        if name == "warmup":
                            assert data["enhancement_state"]["spot_outflow"]["fill_count"] < 1000
                        if name == "soak":
                            # Read existing history after timing ends; do not add
                            # traffic to or dilute the measured read population.
                            steps = json.loads(harness.sql("""SELECT COALESCE(json_agg(t),'[]') FROM (
                                SELECT e.new_profile AS profile, extract(epoch FROM e.created_at) AS at,
                                    (e.payload->>'step_index')::int AS step,
                                    (e.payload->>'step_expires_at')::numeric AS expires_at
                                FROM mm_events e JOIN bot_state b ON e.bot_id=b.bot_id
                                WHERE e.session_id=b.mm_session->>'id' AND e.event_type='step_started'
                                ORDER BY e.id) t"""))
                            low, high = data["window_start_utc"], data["window_start_utc"] + duration
                            data["profile_running_coverage"] = [dict(profile=row["profile"], step=row["step"],
                                                                     running_overlap_seconds=min(high, row["expires_at"]) - max(low, row["at"]))
                                                                for row in steps if row["at"] < high and row["expires_at"] > low]
                    evidence["windows"][name] = data
                    (report / "result.json").write_text(json.dumps(evidence, indent=2))
                    print(name, {key: data[key] for key in ("read_p95_ms", "read_p99_ms", "completed_reads_per_second", "market_updates_per_second", "fills_per_second", "violations")}, flush=True)
                    if data["violations"] and not args.keep_going:
                        raise AssertionError("Declared load budgets failed: " + ", ".join(data["violations"]))
                if args.profile in ("faults", "all"):
                    evidence["faults"] = fault_windows(
                        harness, bot, port, client, report, run_window,
                        lambda h, p: verify_ledger(h, p, require_running=False),
                        volatility_defense=args.volatility_defense,
                        on_result=lambda name, data: save_fault_progress(report, evidence, name, data))
                failed = {name: data["violations"] for name, data in evidence["windows"].items() if data.get("violations")}
                if failed:
                    raise AssertionError("Declared load budgets failed: " + json.dumps(failed))
                evidence["passed"] = True
                evidence['release_scope_complete'] = args.profile == 'all' and not diagnostic
            finally:
                evidence['command_receipt_transport_retries'] = harness.metrics.get('command_receipt_transport_retries', 0)
                try:
                    (report / "final-positions.json").write_text(harness.sql("SELECT COALESCE(json_agg(t),'[]') FROM positions t"))
                    (report / "final-executors.json").write_text(harness.sql("SELECT COALESCE(json_agg(t),'[]') FROM (SELECT * FROM executors ORDER BY timestamp DESC LIMIT 200) t"))
                except Exception:
                    pass
                harness.cleanup()
    except Exception as error:
        evidence.update(passed=False, error=str(error))
        raise
    finally:
        (report / "result.json").write_text(json.dumps(evidence, indent=2))
        print("Load evidence:", report, flush=True)


if __name__ == "__main__":
    main()
