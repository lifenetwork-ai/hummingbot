"""Deployment-image E2E on an internal Docker network; never loads local .env."""
import argparse
import hashlib
import json
import re
import secrets
import subprocess
import sys
import tempfile
import time
import traceback
import uuid
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def pin_manifest(reference):
    """Publish only to a temporary loopback registry, then cache immutable ref.

    A Docker image ID is a config digest, not the registry manifest digest. Close
    this registry before test execution; application containers never need it.
    """
    if re.fullmatch(r"[^\s@]+@sha256:[0-9a-f]{64}", reference):
        metadata = json.loads(docker("image", "inspect", reference).stdout)[0]
        assert reference in metadata["RepoDigests"], "Explicit digest must already be cached before offline execution."
        return reference
    docker("image", "inspect", "registry:2")
    name = "hbot-e2e-registry-" + uuid.uuid4().hex[:10]
    try:
        # The daemon lives inside Docker Desktop's Linux VM. Its loopback is
        # distinct from the macOS published-port loopback. Host networking plus
        # an explicit 127.0.0.1 bind works on that VM and native Linux without
        # exposing an unauthenticated registry on an external interface.
        docker("run", "-d", "--name", name, "--network", "host", "-e", "REGISTRY_HTTP_ADDR=127.0.0.1:0", "registry:2")

        def listening():
            logs = docker("logs", name, check=False)
            return re.search(r"listening on 127\.0\.0\.1:(\d+)", logs.stdout + logs.stderr)
        port = poll(listening, timeout=10, description="temporary loopback registry listener").group(1)
        repository = f"127.0.0.1:{port}/hummingbot"
        tag = repository + ":offline"
        docker("tag", reference, tag)
        # Large conda/Cython layers can take several minutes to compress on
        # Docker Desktop. This preparation budget does not relax runtime gates.
        print(f"Preparing local immutable image reference for {reference}", flush=True)
        docker("push", tag, timeout=600)
        metadata = json.loads(docker("image", "inspect", tag).stdout)[0]
        pinned = next(digest for digest in metadata["RepoDigests"] if digest.startswith(repository + "@"))
        docker("pull", pinned, timeout=60)
        return pinned
    finally:
        docker("rm", "-f", "-v", name, check=False)


def docker(*args, timeout=60, check=True):
    result = subprocess.run(["docker", *map(str, args)], capture_output=True, text=True, timeout=timeout)
    if check and result.returncode:
        raise RuntimeError(f"Docker operation failed: {args[0]}\n{result.stderr[-2000:]}")
    return result


def poll(check, *, timeout=90, description):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(0.1)
    raise AssertionError(f"Timed out: {description}")


def http(container, path, data=None, headers=None):
    # Docker internal networks intentionally have no published host ports. Keep
    # the HTTP probe/control client inside the target's network namespace too.
    program = """
import json, sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
data = None if sys.argv[2] == 'null' else sys.argv[2].encode()
request = Request('http://127.0.0.1:8080' + sys.argv[1], data=data, headers={'Content-Type':'application/json', **json.loads(sys.argv[3])})
try:
    with urlopen(request, timeout=2) as response:
        body = json.load(response) if response.headers.get_content_type() == 'application/json' else response.read().decode()
        print(json.dumps([response.status, body]))
except HTTPError as error:
    print(json.dumps([error.code, json.load(error)]))
except (URLError, TimeoutError):
    print('[null,null]')
"""
    result = docker("exec", container, "python", "-c", program, path, json.dumps(data), json.dumps(headers or {}), timeout=10, check=False)
    return json.loads(result.stdout) if result.returncode == 0 else (None, None)


class Harness:
    def __init__(self, image, assets, report, trading_environment="live"):
        self.prefix = "hbot-e2e-" + uuid.uuid4().hex[:10]
        self.network = self.prefix + "-network"
        self.pg = self.prefix + "-pg"
        self.simulator = self.prefix + "-exchange"
        self.containers = []
        self.image = image
        self.assets = assets
        self.report = report
        self.password = secrets.token_hex(16)
        self.env = {"DATABASE_URL": f"postgresql+psycopg2://postgres:{self.password}@db:5432/hbot_test_offline",
                    "ENABLE_AUTO_MIGRATION": "true", "BOT_ID": "hummingbot", "PORT": "8080",
                    "ADMIN_API_ENABLED": "false",
                    "HBOT_OKX_API_KEY": "offline-key", "HBOT_OKX_SECRET_KEY": "offline-secret",
                    "HBOT_OKX_PASSPHRASE": "offline-pass", "HBOT_OFFLINE_HTTP_ORIGIN": "http://exchange:8080"}
        self.metrics = {}
        self.env["OKX_TRADING_ENV"] = trading_environment
        if trading_environment == "demo":
            for prefix in ("HBOT_OKX_DEMO_", "HBOT_OKX_PERPETUAL_DEMO_"):
                for field, value in (("API_KEY", "offline-key"), ("SECRET_KEY", "offline-secret"), ("PASSPHRASE", "offline-pass")):
                    self.env[prefix + field] = value

    def run_container(self, name, args, command=(), env=None, publish=False):
        options = ["run", "-d", "--name", name, "--network", self.network]
        for key, value in (env or {}).items():
            options += ["-e", f"{key}={value}"]
        docker(*options, *args, self.image, *command)
        self.containers.append(name)
        return name

    def port(self, name):
        return name

    def sql(self, statement):
        return docker("exec", self.pg, "psql", "-U", "postgres", "-d", "hbot_test_offline", "-At", "-v", "ON_ERROR_STOP=1",
                      "-c", statement).stdout.strip()

    def state(self):
        return json.loads(self.sql("SELECT row_to_json(b) FROM public.bot_state b"))

    def ledger(self):
        status, body = http(self.exchange_port, "/ledger")
        assert status == 200
        return body

    def control(self, **data):
        assert http(self.exchange_port, "/control", data)[0] == 200

    def setup(self):
        docker("network", "create", "--internal", self.network)
        docker("run", "-d", "--name", self.pg, "--network", self.network, "--network-alias", "db", "--memory", "512m",
               "-e", "POSTGRES_DB=hbot_test_offline", "-e", "POSTGRES_PASSWORD=" + self.password, "postgres:14-alpine")
        self.containers.append(self.pg)
        poll(lambda: docker("exec", self.pg, "pg_isready", "-U", "postgres", check=False).returncode == 0,
             timeout=30, description="PostgreSQL readiness")
        self.run_container(self.simulator, ["--platform", "linux/amd64", "--network-alias", "exchange", "--read-only",
                                            "--tmpfs", "/tmp:rw,nosuid,size=128m", "--mount",
                                            f"type=bind,source={self.assets},target=/test-assets,readonly"],
                           command=["python", "/test-assets/exchange.py"], publish=True)
        self.exchange_port = self.port(self.simulator)
        poll(lambda: http(self.exchange_port, "/ledger")[0] == 200, timeout=30, description="simulator readiness")
        assert json.loads(docker("network", "inspect", self.network).stdout)[0]["Internal"]

    def start(self, revision, *, auto_migrate=True, runtime_role=False, expect_failure=False, additive_fixture=False,
              storage_backend="postgres", instrumented=False, trace_directory=None, availability=False):
        name = self.prefix + "-" + revision
        env = {**self.env, "K_REVISION": revision, "ENABLE_AUTO_MIGRATION": str(auto_migrate).lower(),
               "STORAGE_BACKEND": storage_backend}
        if runtime_role:
            env["DATABASE_URL"] = f"postgresql+psycopg2://hbot_runtime:{self.password}@db:5432/hbot_test_offline"
        mounts = []
        if additive_fixture:
            mounts += ["--mount", f"type=bind,source={self.assets / '999_offline_upgrade.sql'},target=/home/hummingbot/"
                       "hummingbot/model/db_migration/postgres/scripts/999_offline_upgrade.sql,readonly"]
        for directory, filenames in {"strategies": ["pmm.yml", "pmm-revised.yml"], "controllers": ["controller-spot.yml", "controller-perpetual.yml"],
                                     "scripts": ["script-perpetual.yml"]}.items():
            for filename in filenames:
                mounts += ["--mount", f"type=bind,source={self.assets / filename},target=/home/hummingbot/conf/{directory}/{filename},readonly"]
        if (self.assets / "life_mm_profiles.yaml").is_file():
            mounts += ["--mount", f"type=bind,source={self.assets / 'life_mm_profiles.yaml'},target=/home/hummingbot/conf/life_mm_profiles.yaml,readonly"]
        if instrumented or availability:
            mounts += ["--mount", f"type=bind,source={self.assets},target=/test-assets,readonly"]
        if trace_directory is not None:
            assert instrumented
            env["HBOT_LOAD_TRACE_DIRECTORY"] = "/trace-output"
            mounts += ["--mount", f"type=bind,source={trace_directory},target=/trace-output"]
        self.run_container(name, ["--platform", "linux/amd64", "--cpus", "1", "--memory", "2g", "--read-only",
                                  "--tmpfs", "/tmp:rw,nosuid,size=128m", *(["--network-alias", "bot"] if instrumented else []), *mounts],
                           env=env, publish=True, command=(["python", "/test-assets/availability_service.py"] if availability else
                                                           ["python", "/test-assets/load_service.py"] if instrumented else ()))
        port = self.port(name)
        started = time.monotonic()
        if expect_failure:
            poll(lambda: json.loads(docker("inspect", name).stdout)[0]["State"]["Status"] == "exited",
                 timeout=200, description=f"{revision} rejected before trading")
            assert json.loads(docker("inspect", name).stdout)[0]["State"]["ExitCode"] != 0
            assert not self.ledger()["accepted"]
            return name, port

        def startup_ready():
            if http(port, "/startup")[0] == 200:
                return True
            state = json.loads(docker("inspect", name).stdout)[0]["State"]
            assert state["Running"], f"{revision} exited before startup; inspect retained container logs"
            return False

        poll(startup_ready, timeout=200, description=f"{revision} validated startup")
        self.metrics[revision + "_startup_s"] = round(time.monotonic() - started, 3)
        assert http(port, "/live")[0] == 200
        expected = ("b'python\\x00/test-assets/availability_service.py" if availability else
                    "b'python\\x00/test-assets/load_service.py" if instrumented else "b'python\\x00-m\\x00hummingbot.cli.service")
        assert docker("exec", name, "python", "-c", "from pathlib import Path; print(Path('/proc/1/cmdline').read_bytes())").stdout.startswith(expected)
        return name, port

    def cli(self, *args, success=True):
        options = ["run", "--rm", "--platform", "linux/amd64", "--network", self.network, "--read-only",
                   "--tmpfs", "/tmp:rw,nosuid,size=128m"]
        for key, value in self.env.items():
            options += ["-e", f"{key}={value}"]
        result = docker(*options, self.image, "hbot", *args, timeout=60, check=False)
        if success:
            assert result.returncode == 0, result.stderr[-2000:] + result.stdout[-2000:]
        else:
            assert result.returncode != 0
        return result.stdout

    def terminate(self, name):
        started = time.monotonic()
        docker("kill", "--signal", "TERM", name)
        poll(lambda: json.loads(docker("inspect", name).stdout)[0]["State"]["Status"] == "exited",
             timeout=9.5, description="PID 1 shutdown")
        state = json.loads(docker("inspect", name).stdout)[0]["State"]
        assert state["ExitCode"] == 0 and not state["OOMKilled"], docker("logs", name, check=False).stdout[-2000:]
        self.metrics[name.rsplit("-", 1)[-1] + "_shutdown_s"] = round(time.monotonic() - started, 3)

    def wait_orders(self, count):
        ledger = poll(lambda: (value if len((value := self.ledger())["accepted"]) >= count else None),
                      description=f"independent exchange ledger accepted {count} orders")
        assert len(ledger["accepted"]) == count and not ledger["unknown"], "Unexpected order count or unspecified simulator endpoint; inspect retained ledger."
        assert len({order["clOrdId"] for order in ledger["accepted"]}) == count
        return ledger["accepted"]

    def wait_running(self, port):
        poll(lambda: http(port, "/ready")[0] == 200, description="reconciliation then trading readiness")

    def no_runtime_files(self, name):
        program = """from pathlib import Path
paths = [Path('/home/hummingbot') / value for value in ('data', 'logs', 'conf/connectors')]
assert all(not list(path.rglob('*')) for path in paths)
assert not Path('/home/hummingbot/conf/.password_verification').exists()
assert not list(Path('/home/hummingbot').rglob('*.sqlite'))
"""
        docker("exec", name, "python", "-c", program)

    def reviewed_test_recovery(self):
        """Test-only operator maintenance, after independent exposure review.

        The application has no automatic HALT reset. This deliberately demonstrates
        that repeated process restarts alone cannot reopen the risk gate.
        """
        row = self.state()
        assert row["status"]["phase"] == "halted" and not row["status"]["engine"]["strategy_running"]
        assert not row["recovery_state"].get("runtime_active")
        assert self.sql("SELECT count(*) FROM public.orders WHERE creation_pending") == "0"
        assert all(order["state"] in ("filled", "canceled") for order in self.ledger()["orders"])
        self.sql("UPDATE public.bot_state SET recovery_state=recovery_state - 'halt' - 'reason'")

    def scenario(self):
        print("Offline E2E: bootstrap, standby and deterministic orders", flush=True)
        first, port = self.start("first")
        poll(lambda: self.state()["owner_uuid"], description="first owner")
        assert http(port, "/ready")[0] == 503 and self.state()["desired_state"] == "stopped"
        generation = self.state()["owner_generation"]
        second, second_port = self.start("second", auto_migrate=False)
        assert http(second_port, "/live")[1]["state"] == "standby"
        self.cli("start", "pmm.yml", "--timeout", "0", "--expected-version", "0", "--json")
        self.wait_running(port)
        orders = self.wait_orders(2)
        self.metrics["local_steady_resources"] = docker(
            "stats", "--no-stream", "--format", "cpu={{.CPUPerc}} memory={{.MemUsage}}", first).stdout.strip()
        latency = """import json,time
from sqlalchemy import text
from hummingbot.client.config.runtime_config import RuntimeSettings
from hummingbot.model.db_migration.postgres.connection import create_postgres_engine
engine=create_postgres_engine(RuntimeSettings.from_environment())
samples=[]
with engine.connect() as connection:
    for _ in range(25):
        started=time.perf_counter()
        assert connection.execute(text('SELECT 1')).scalar()==1
        samples.append((time.perf_counter()-started)*1000)
engine.dispose()
print(json.dumps({'select_one_p95_ms':sorted(samples)[23]}))
"""
        self.metrics["local_database"] = json.loads(docker("exec", first, "python", "-c", latency).stdout)
        assert {(order["side"], Decimal(order["sz"]), Decimal(order["px"])) for order in orders} == {
            ("buy", Decimal("0.1"), Decimal("99")), ("sell", Decimal("0.1"), Decimal("101"))}
        poll(lambda: self.sql("SELECT count(*) FROM public.orders WHERE NOT creation_pending") == "2",
             description="durable creation acknowledgments")
        buy = next(order for order in orders if order["side"] == "buy")
        print("Offline E2E: partial/duplicate fills and separate CLI history", flush=True)
        for number, amount, fee in ((1, "0.04000001", "0.004000000000000001"),
                                    (2, "0.05999999", "0.005999999999999999")):
            if number == 2:
                subscriptions = len(self.ledger()["subscriptions"])
                self.control(drop_ws=True)
                poll(lambda: {"books", "orders", "account"} <= {
                    item["channel"] for item in self.ledger()["subscriptions"][subscriptions:]},
                    description="public/private WebSocket reconnect and subscriptions")
            self.control(fill={"clOrdId": buy["clOrdId"], "amount": amount, "fee": fee}, duplicate=True)
            poll(lambda: self.sql("SELECT count(*) FROM public.trade_fills") == str(number), description="deduplicated partial fill")
        self.control(replay_trade_id="1")
        totals = json.loads(self.sql("SELECT json_build_object('amount',sum(amount)::text,'fees',sum(trade_fee_in_quote)::text,'price',min(price)::text) FROM public.trade_fills"))
        # PostgreSQL recorder decimals use natural units.
        assert Decimal(totals["amount"]) == Decimal("0.1")
        assert Decimal(totals["fees"]) == Decimal("0.01")
        assert Decimal(totals["price"]) == Decimal("99")
        history = self.cli("history", "pmm.yml")
        assert "-0.01" in history and "last recorded fill" in history and "error:" not in history
        self.report.joinpath("history.txt").write_text(history)
        snapshot = json.loads(self.cli("status", "--json"))
        assert snapshot["running"] and snapshot["acknowledged_version"] == 1
        self.cli("stop", "--expected-version", "0", "--timeout", "0", success=False)
        self.cli("stop", "--expected-version", "1", "--timeout", "9", "--json")
        assert self.state()["desired_state"] == "stopped" and http(port, "/ready")[0] == 503
        self.no_runtime_files(first)
        self.terminate(first)
        print("Offline E2E: clean handover and fresh DML-only restart", flush=True)
        poll(lambda: self.state()["owner_generation"] > generation, description="standby ownership handover")
        assert len(self.ledger()["accepted"]) == 2
        self.cli("start", "pmm.yml", "--expected-version", "2", "--timeout", "0", "--json")
        self.wait_running(second_port)
        self.wait_orders(4)
        self.terminate(second)
        assert self.state()["desired_state"] == "running" and self.state()["control_version"] == 3
        # A fresh process with DML-only privileges uses the validated existing schema.
        self.sql(f"CREATE ROLE hbot_runtime LOGIN PASSWORD '{self.password}'; GRANT USAGE ON SCHEMA public TO hbot_runtime; "
                 "GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA public TO hbot_runtime; "
                 "GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA public TO hbot_runtime")
        third, third_port = self.start("third", auto_migrate=False, runtime_role=True)
        self.wait_running(third_port)
        self.wait_orders(6)
        assert self.sql("SELECT count(*) FROM public.trade_fills") == "2"
        self.no_runtime_files(third)
        docker("kill", "--signal", "KILL", third)
        print("Offline E2E: SIGKILL recovery must retain HALT", flush=True)
        fourth, fourth_port = self.start("fourth", auto_migrate=False)
        poll(lambda: self.state()["status"].get("phase") == "halted", description="durable HALT after unclean exit")
        assert self.state()["recovery_state"]["halt"] and http(fourth_port, "/ready")[0] == 503
        poll(lambda: all(order["state"] not in ("live", "partially_filled") for order in self.ledger()["orders"]),
             description="restored working orders reconciled and canceled")
        assert len(self.ledger()["accepted"]) == 6
        self.no_runtime_files(fourth)
        print("Offline E2E: accepted request loses ACK across paused owner/session loss", flush=True)
        self.reviewed_test_recovery()
        self.cli("start", "pmm.yml", "--replace", "--expected-version", "3", "--timeout", "0", "--json")
        self.wait_running(fourth_port)
        self.wait_orders(8)
        self.cli("stop", "--expected-version", "4", "--timeout", "9", "--json")
        self.control(hold_ack=True)
        self.cli("start", "pmm.yml", "--expected-version", "5", "--timeout", "0", "--json")
        self.wait_orders(10)
        assert self.sql("SELECT count(*) FROM public.orders WHERE creation_pending") == "2"
        docker("pause", fourth)
        self.sql("SELECT pg_terminate_backend(pid) FROM pg_locks WHERE locktype='advisory' AND granted "
                 "AND classid=1212305236 AND objid=1 AND objsubid=2")
        fifth, fifth_port = self.start("fifth", auto_migrate=False)
        poll(lambda: self.state()["status"].get("phase") == "halted", description="lost-ACK successor HALT")
        assert http(fifth_port, "/ready")[0] == 503
        assert self.state()["recovery_state"]["halt"]
        poll(lambda: all(order["state"] not in ("live", "partially_filled") for order in self.ledger()["orders"]),
             description="accepted unknown-ACK orders reconciled without resubmission")
        self.control(hold_ack=False)
        docker("unpause", fourth)
        poll(lambda: json.loads(docker("inspect", fourth).stdout)[0]["State"]["Status"] == "exited",
             timeout=10, description="stale owner exits with send gate closed")
        assert len(self.ledger()["accepted"]) == 10 and not self.ledger()["unknown"]
        assert self.sql("SELECT count(*) FROM public.trade_fills") == "2"
        assert self.sql("SELECT count(*) FROM public.orders WHERE creation_pending") == "0"
        self.no_runtime_files(fifth)
        print("Offline E2E: database outage closes the runtime and restart remains HALTed", flush=True)
        self.reviewed_test_recovery()
        self.cli("start", "pmm.yml", "--replace", "--expected-version", "6", "--timeout", "0", "--json")
        self.wait_running(fifth_port)
        self.wait_orders(12)
        docker("stop", "--time", "5", self.pg)
        poll(lambda: json.loads(docker("inspect", fifth).stdout)[0]["State"]["Status"] == "exited",
             timeout=10, description="DB outage closes the owned runtime")
        assert len(self.ledger()["accepted"]) == 12
        docker("start", self.pg)
        poll(lambda: docker("exec", self.pg, "pg_isready", "-U", "postgres", check=False).returncode == 0,
             timeout=30, description="PostgreSQL restored")
        sixth, sixth_port = self.start("sixth", auto_migrate=False)
        poll(lambda: self.state()["status"].get("phase") == "halted", description="DB outage restart recovery gate")
        assert http(sixth_port, "/live")[0] == 200 and http(sixth_port, "/ready")[0] == 503
        assert len(self.ledger()["accepted"]) == 12
        self.reviewed_test_recovery()
        self.cli("start", "pmm.yml", "--replace", "--expected-version", "7", "--timeout", "0", "--json")
        self.wait_running(sixth_port)
        orders = self.wait_orders(14)
        buy = next(order for order in orders[-2:] if order["side"] == "buy")
        self.interrupt_recorder(sixth, buy)
        seventh, seventh_port = self.start("seventh", auto_migrate=False)
        poll(lambda: self.state()["status"].get("phase") == "halted", description="interrupted recorder reconciliation HALT")
        assert http(seventh_port, "/ready")[0] == 503
        poll(lambda: self.sql("SELECT count(*) FROM public.trade_fills") == "3", description="exchange fill recovered after rolled back transaction")
        assert Decimal(self.sql("SELECT sum(amount) FROM public.trade_fills")) == Decimal("0.14")
        assert Decimal(self.sql("SELECT sum(trade_fee_in_quote) FROM public.trade_fills")) == Decimal("0.014")
        assert "-0.014" in self.cli("history", "pmm.yml")
        assert len(self.ledger()["accepted"]) == 14
        self.metrics["accepted_orders"] = 14
        self.metrics["persisted_fills"] = 3
        self.metrics["websocket_reconnect"] = True
        self.metrics["interrupted_recorder_recovered"] = True

    def interrupt_recorder(self, process, order):
        print("Offline E2E: interrupt a recorder transaction behind a real row-lock barrier", flush=True)
        command = ["docker", "exec", self.pg, "psql", "-U", "postgres", "-d", "hbot_test_offline", "-At", "-v", "ON_ERROR_STOP=1",
                   "-c", "SET application_name='e2e_recorder_barrier'; BEGIN; SELECT id FROM public.market_states FOR UPDATE; SELECT pg_sleep(30)"]
        blocker = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        try:
            poll(lambda: self.sql("SELECT count(*) FROM pg_stat_activity WHERE application_name='e2e_recorder_barrier' AND wait_event='PgSleep'") == "1",
                 timeout=10, description="recorder barrier holds tracking row lock")
            self.control(fill={"clOrdId": order["clOrdId"], "amount": "0.04", "fee": "0.004"}, duplicate=True)
            victim = poll(lambda: self.sql("SELECT a.pid FROM pg_stat_activity a WHERE EXISTS (SELECT 1 FROM pg_stat_activity b "
                                           "WHERE b.application_name='e2e_recorder_barrier' AND b.pid=ANY(pg_blocking_pids(a.pid))) LIMIT 1"),
                          timeout=5, description="actual recorder transaction waiting before commit")
            assert self.sql("SELECT count(*) FROM public.trade_fills") == "2", "Uncommitted fill must remain invisible."
            assert self.sql(f"SELECT pg_terminate_backend({int(victim)})") == "t"
        finally:
            self.sql("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE application_name='e2e_recorder_barrier'")
            blocker.communicate(timeout=10)
        poll(lambda: json.loads(docker("inspect", process).stdout)[0]["State"]["Status"] == "exited",
             timeout=15, description="persistence failure closes trading process")
        assert self.sql("SELECT count(*) FROM public.trade_fills") == "2", "Interrupted transaction must roll back atomically."

    def cleanup(self):
        if hasattr(self, "exchange_port"):
            try:
                self.report.joinpath("exchange-ledger.json").write_text(json.dumps(self.ledger(), indent=2))
            except Exception:
                pass
        for name in reversed(self.containers):
            result = docker("logs", name, check=False)
            self.report.joinpath(name + ".log").write_text(result.stdout + result.stderr)
            docker("rm", "-f", "-v", name, check=False)
        docker("network", "rm", self.network, check=False)

    def v2_scenario(self, *, perpetual):
        connector = "okx_perpetual" if perpetual else "okx"
        if perpetual:
            for field, dummy in (("API_KEY", "offline-key"), ("SECRET_KEY", "offline-secret"), ("PASSPHRASE", "offline-pass")):
                self.env["HBOT_OKX_PERPETUAL_" + field] = dummy
        config = "script-perpetual.yml" if perpetual else "controller-spot.yml"
        controller_id = "offline-perpetual-controller" if perpetual else "offline-spot-controller"
        print(f"Offline E2E: existing V2 {connector} controller, executor, fills and position holds", flush=True)
        first, port = self.start("v2-first")
        poll(lambda: self.state()["owner_uuid"], description="V2 service owns database")
        self.cli("start", config, "--timeout", "0", "--json")
        self.wait_running(port)
        orders = self.wait_orders(1)
        order = orders[0]
        # Decimal spread calculation preserves exactly 1% below the 100 anchor.
        assert order["side"] == "buy" and Decimal(order["px"]) == Decimal("99")
        assert Decimal(order["sz"]) == (Decimal("10") if perpetual else Decimal("0.1"))
        assert order["instId"] == ("BTC-USDT-SWAP" if perpetual else "BTC-USDT")
        if perpetual:
            poll(lambda: any(item["path"].endswith("set-leverage") for item in self.ledger()["mutations"]),
                 description="existing perpetual account setup mutation acknowledged")
            assert not self.state()["recovery_state"].get("pending_mutations")
        self.control(fill={"clOrdId": order["clOrdId"], "amount": order["sz"], "fee": "0.01"}, duplicate=True)
        poll(lambda: self.sql("SELECT count(*) FROM public.trade_fills") == "1", description="V2 unique fill persisted")
        fill = json.loads(self.sql("SELECT row_to_json(f) FROM public.trade_fills f"), parse_float=Decimal)
        assert Decimal(str(fill["amount"])) == Decimal("0.1")
        assert Decimal(str(fill["price"])) == Decimal("99")
        assert Decimal(str(fill["trade_fee_in_quote"])) == Decimal("0.01")
        assert fill["market"] == connector and fill["config_file_path"] == config
        assert 1700000000000 < fill["timestamp"] < 2000000000000, "Recorder timestamps must remain epoch milliseconds."
        history = self.cli("history", config)
        assert "-0.01" in history and "error:" not in history
        self.report.joinpath("history-v2.txt").write_text(history)
        self.cli("stop", "--expected-version", "1", "--timeout", "9", "--json")
        held = json.loads(self.sql("SELECT row_to_json(p) FROM public.positions p LIMIT 1"), parse_float=Decimal)
        assert held["controller_id"] == controller_id and held["connector_name"] == connector
        assert Decimal(str(held["amount"])) == Decimal("0.1")
        assert Decimal(str(held["breakeven_price"])) == Decimal("99")
        assert Decimal(str(held["cum_fees_quote"])) == Decimal("0.01")
        # Reviewable test-only checkpoint seed while stopped: verify that the
        # existing controller's startup hook cannot erase the persisted risk peak.
        checkpoint = {"_is_stop_triggered": False, "drawdown_exited_controllers": [controller_id],
                      "max_global_pnl": "12.345678", "max_pnl_by_controller": {controller_id: "5.25"}}
        payload = json.dumps({"strategy_checkpoints": {config: checkpoint}})
        self.sql(f"UPDATE public.bot_state SET recovery_state=recovery_state || '{payload}'::jsonb")
        self.terminate(first)
        second, second_port = self.start("v2-second", auto_migrate=False)
        self.cli("start", config, "--expected-version", "2", "--timeout", "0", "--json")
        self.wait_running(second_port)
        self.cli("stop", "--expected-version", "3", "--timeout", "9", "--json")
        restored = self.state()["recovery_state"]["strategy_checkpoints"][config]
        assert restored == checkpoint
        assert len(self.ledger()["accepted"]) == 1, "Drawdown-stopped controller must not restart."
        assert self.sql("SELECT count(*) FROM public.positions") == "1"
        self.no_runtime_files(second)
        self.terminate(second)
        assert not self.ledger()["unknown"]
        self.metrics.update(accepted_orders=1, persisted_fills=1, position_base="0.1", pnl_peak_restored="12.345678")

    def schema_scenario(self):
        print("Offline E2E: disabled migration rejects empty and incompatible schemas", flush=True)
        self.start("file-backend", storage_backend="file", expect_failure=True)
        assert self.sql("SELECT to_regclass('public.bot_state') IS NULL") == "t"
        self.start("empty", auto_migrate=False, expect_failure=True)
        valid, _ = self.start("valid")
        poll(lambda: self.state()["owner_uuid"], description="validated schema owner")
        self.terminate(valid)
        self.sql("ALTER TABLE public.orders RENAME COLUMN price TO incompatible_price")
        self.start("drift", auto_migrate=False, expect_failure=True)
        self.sql("ALTER TABLE public.orders RENAME COLUMN incompatible_price TO price")
        repaired, _ = self.start("repaired", auto_migrate=False)
        self.terminate(repaired)
        assert not self.ledger()["accepted"]
        self.metrics.update(file_backend_rejected=True, empty_rejected=True, drift_rejected=True,
                            repaired_schema_validated=True)

    def admin(self, port, path, data=None, key=None):
        import base64
        headers = {"Authorization": "Basic " + base64.b64encode(b"operator:offline-admin").decode()}
        if key:
            headers["Idempotency-Key"] = key
        return http(port, "/api/v1/admin" + path, data, headers)

    def mm_command(self, port, action, **fields):
        state = self.state()
        payload = dict(action=action, expected_version=state["control_version"], **fields)
        if action in ("stop", "switch"):
            payload["session_id"] = state["mm_session"]["id"]
        key = str(uuid.uuid4())
        status, receipt = self.admin(port, "/commands", payload, key)
        assert status == 202, receipt
        status, retried = self.admin(port, "/commands", payload, key)
        assert status == 202 and retried["id"] == receipt["id"]
        latest = {"id": receipt["id"]}

        def completed():
            code, observed = self.admin(port, "/commands/" + receipt["id"])
            assert code == 200, f"MM {action} receipt unavailable HTTP {code}"
            latest.update({field: observed.get(field) for field in ("id", "status", "error", "effective_version")})
            if observed["status"] in ("failed", "superseded"):
                raise AssertionError(f"MM {action} {observed['status']}")
            return observed["status"] == "succeeded"

        try:
            poll(completed, timeout=60, description="MM " + action + " actual command completion")
        except AssertionError as error:
            # Retain only operational codes, never dump request/config payloads.
            try:
                code, observed = self.admin(port, "/status")
                runtime = {field: observed.get(field) for field in ("phase", "error")}
                runtime.update(http_status=code, markets=[{field: item.get(field) for field in ("market", "reason")}
                                                          for item in observed.get("markets", [])],
                               incidents=[item.get("reason_code") for item in (observed.get("service") or {}).get("incidents", [])])
            except Exception as diagnostic_error:
                runtime = {"unavailable": type(diagnostic_error).__name__}
            raise AssertionError(f"{error}; command={json.dumps(latest)}; runtime={json.dumps(runtime)}") from error
        return receipt

    def mm_scenario(self, *, matrix=True):
        print("Offline E2E: LIFE admin three scenarios x spot/perpetual/both", flush=True)
        self.env.update(ADMIN_API_ENABLED="true", ADMIN_API_USERNAME="operator", ADMIN_API_PASSWORD="offline-admin", FUNDING_SYNC_INTERVAL_SECONDS="5")
        for prefix in ("HBOT_OKX_PERPETUAL_",):
            for field, value in (("API_KEY", "offline-key"), ("SECRET_KEY", "offline-secret"), ("PASSPHRASE", "offline-pass")):
                self.env[prefix + field] = value
        first, port = self.start("mm-first")
        poll(lambda: bool(self.state().get("status")), description="MM owner status")
        assert http(port, "/api/v1/admin/status")[0] == 401
        assert self.admin(port, "/docs")[0] == 200
        profiles = self.admin(port, "/profiles")[1]
        assert profiles["configuration_policy"]["profile_settings"] == "yaml_only"
        assert profiles["process_identity"]["trading_environment"] == self.env["OKX_TRADING_ENV"]
        invalid = dict(action="start", expected_version=self.state()["control_version"], market_mode="spot",
                       profile_id="sideways", references=None)
        code, rejected = self.admin(port, "/commands", invalid, str(uuid.uuid4()))
        assert code == 422 and rejected["code"] == "yaml_configuration_read_only"
        assert rejected["request_id"] and rejected["retry"] == "fix_request"
        cases = []
        for mode in (("spot", "perpetual", "both") if matrix else ()):
            for profile in ("follow_market", "sideways", "follow_token"):
                print(f"Offline LIFE: {mode}/{profile}", flush=True)
                before = len(self.ledger()["accepted"])
                fields = dict(market_mode=mode, selection="fixed", profile_id=profile, duration_seconds=600)
                receipt = self.mm_command(port, "start", expected_catalogue_hash=profiles["catalogue_hash"], **fields)
                commands = self.admin(port, "/commands?action=start&limit=1")[1]
                assert commands["items"][0]["id"] == receipt["id"]
                sessions = self.admin(port, "/sessions?limit=1")[1]
                assert sessions["items"][0]["id"] == receipt["session_id"]
                config = self.admin(port, "/sessions/" + receipt["session_id"] + "/configuration")[1]
                assert config["catalogue_hash"] == profiles["catalogue_hash"] and not config["legacy_overrides"]
                count = 8 if mode == "both" else 4
                poll(lambda: len(self.ledger()["accepted"]) >= before + count, description="MM post-only levels")
                orders = self.ledger()["accepted"][before:]
                assert {order["instId"] for order in orders} == ({"LIFE-USDT", "LIFE-USDT-SWAP"} if mode == "both" else
                                                                 {"LIFE-USDT" if mode == "spot" else "LIFE-USDT-SWAP"})
                assert all(order["ordType"] == "post_only" and order["side"] in ("buy", "sell") for order in orders)
                for instrument in {order["instId"] for order in orders}:
                    for side, prices in (("buy", {Decimal("0.099"), Decimal("0.098")}),
                                         ("sell", {Decimal("0.101"), Decimal("0.102")})):
                        assert {Decimal(order["px"]) for order in orders if order["instId"] == instrument and order["side"] == side} == prices
                runtime = self.admin(port, "/status")[1]
                assert len(runtime["markets"]) == (2 if mode == "both" else 1)
                assert all(Decimal(item["reference_price"]) == Decimal("0.1") for item in runtime["markets"])
                cases.append(dict(mode=mode, profile=profile, count=count))
                self.mm_command(port, "stop")
                assert all(order["state"] in ("filled", "canceled") for order in self.ledger()["orders"])
        # Generic CLI control and the admin MM API must share one stop handshake.
        # A legacy YAML selection then replaces, rather than strands, MM state.
        self.mm_command(port, "start", market_mode="both", selection="fixed", profile_id="sideways", duration_seconds=600)
        poll(lambda: sum(order["state"] == "live" for order in self.ledger()["orders"]) == 8,
             description="working MM quotes before generic CLI stop")
        self.cli("stop", "--expected-version", str(self.state()["control_version"]), "--timeout", "9", "--json")
        assert self.state()["mm_session"]["status"] == "stopped"
        assert all(order["state"] in ("filled", "canceled") for order in self.ledger()["orders"])
        self.cli("start", "pmm.yml", "--expected-version", str(self.state()["control_version"]), "--timeout", "0", "--json")
        self.wait_running(port)
        assert self.state()["mm_session"] is None
        poll(lambda: sum(order["state"] == "live" for order in self.ledger()["orders"]) == 2,
             description="working legacy quotes after replacing MM selection")
        self.cli("stop", "--expected-version", str(self.state()["control_version"]), "--timeout", "9", "--json")
        # Shared basket reference response, fixed anchors and clean process restart.
        self.mm_command(port, "start", market_mode="both", selection="fixed", profile_id="follow_market", duration_seconds=600)
        session = self.state()["mm_session"]
        self.control(prices={"BTC-USDT": "110"})
        poll(lambda: all(Decimal(item["reference_price"]) == Decimal("0.106") for item in self.admin(port, "/status")[1]["markets"]),
             description="shared weighted basket follows +10 percent BTC with 0.6 weight")
        self.terminate(first)
        second, port = self.start("mm-restart", auto_migrate=False)
        self.wait_running(port)
        restored = self.state()["mm_session"]
        assert restored["id"] == session["id"] and restored["anchors"] == session["anchors"]
        assert restored["started_at"] == session["started_at"] and restored["expires_at"] == session["expires_at"]
        restored_config = self.admin(port, "/sessions/" + session["id"] + "/configuration")[1]
        assert restored_config["catalogue_hash"] == profiles["catalogue_hash"]
        # One stale reference blocks both markets without extending the session.
        self.control(stale_pairs=["BTC-USDT"])
        poll(lambda: not self.state()["status"]["running"] or self.state()["status"]["phase"] == "waiting_for_mm_readiness",
             timeout=25, description="joint stale-reference readiness closes quotes")
        self.control(stale_pairs=[])
        self.wait_running(port)
        assert self.state()["mm_session"]["expires_at"] == session["expires_at"]
        # Observe real recorder fees and shared wallet via FE, then both position holds.

        def live_buys():
            buys = [order for order in self.ledger()["orders"] if order["side"] == "buy" and order["state"] == "live"]
            return buys if {item["instId"] for item in buys} == {"LIFE-USDT", "LIFE-USDT-SWAP"} else None
        buys = poll(live_buys, description="both markets recreate quotes after readiness recovery")
        for order in (next(item for item in buys if item["instId"] == "LIFE-USDT"),
                      next(item for item in buys if item["instId"] == "LIFE-USDT-SWAP")):
            self.control(fill={"clOrdId": order["clOrdId"], "amount": order["sz"], "fee": "0.01"}, duplicate=True)
        poll(lambda: self.sql("SELECT count(*) FROM trade_fills") == "2", description="MM separate market deduplicated fills")
        for market in ("spot", "perpetual"):
            fills = self.admin(port, "/fills?market=" + market)[1]["items"]
            assert len(fills) == 1 and fills[0]["market"] == market and Decimal(fills[0]["fee_in_quote"]) == Decimal("0.01")
        daily = self.admin(port, "/statistics/daily")[1]
        assert all(row["days"][0]["fill_count"] == 1 and Decimal(row["days"][0]["fee_in_usdt"]) == Decimal("0.01") for row in daily["markets"])
        balances = self.admin(port, "/balances")[1]
        assert len(balances["accounts"]) == 1 and balances["accounts"][0]["account_id"] == "offline-account-1"
        actual_cash = {row["ccy"]: Decimal(row["cashBal"]) for row in self.ledger()["balances"]}
        poll(lambda: all(Decimal(row["cash_balance"]) == actual_cash[row["currency"]]
                         for row in self.admin(port, "/balances")[1]["accounts"][0]["balances"]), description="observed shared cash matches independent exchange")
        status, receipt = self.admin(port, "/reconciliation/check", {"market": "all"}, str(uuid.uuid4()))
        assert status == 202
        report = poll(lambda: (value if (value := self.admin(port, "/reconciliation")[1])["check"]["status"] in ("succeeded", "failed") else None),
                      description="MM observational check completion")
        assert report["report"]["state"] == "matched", report
        stable_check = self.admin(port, "/reconciliation/" + receipt["id"])[1]
        assert stable_check["id"] == receipt["id"] and stable_check["report"] == report["report"]
        # OKX may cancel a post-only order in the private stream before its
        # positive REST create response. A terminal exchange identity resolves
        # that durable intent without reopening the order or halting the bot.
        self.control(cancel_before_ack=True, hold_ack=True)
        before_cancel_race = len(self.ledger()["accepted"])
        self.mm_command(port, "switch", market_mode="both", selection="fixed", profile_id="sideways", duration_seconds=600)
        poll(lambda: len(self.ledger()["accepted"]) >= before_cancel_race + 8,
             description="both markets observe canceled orders before REST create ACK")
        poll(lambda: self.sql("SELECT count(*) FROM orders WHERE creation_pending") == "0",
             description="positive private cancellation durably resolves create intents")
        assert not self.state()["recovery_state"].get("halt") and http(port, "/live")[0] == 200
        canceled_ids = {row["clOrdId"] for row in self.ledger()["accepted"][before_cancel_race:]}
        canceled_views = [row for row in self.admin(port, "/orders?limit=500")[1]["items"] if row["id"] in canceled_ids]
        assert len(canceled_views) >= 8 and all(row["side"] in ("BUY", "SELL") and row["exchange_order_id"]
                                                for row in canceled_views)
        assert all(row["progress_basis"] == "recorded_fills_only" and Decimal(row["recorded_filled_amount"]) == 0
                   for row in canceled_views)
        self.metrics["admin_operations_contract"] = True
        self.control(cancel_before_ack=False, hold_ack=False)
        poll(lambda: sum(order["state"] == "live" for order in self.ledger()["orders"]) == 8,
             description="normal maker quotes recover after terminal-before-create race")
        self.metrics["terminal_cancel_ack_before_create"] = True
        self.mm_command(port, "switch", market_mode="both", selection="random", duration_seconds=60)
        first_draw = self.state()["mm_session"]
        first_order_count = len(self.ledger()["accepted"])
        poll(lambda: self.state()["mm_session"]["generation"] > first_draw["generation"], timeout=30, description="persisted random step")
        poll(lambda: self.state()["mm_session"]["status"] == "expired", timeout=70, description="finite random session expiry")
        poll(lambda: all(order["state"] in ("filled", "canceled") for order in self.ledger()["orders"]), description="expiry cancellation")
        deadline_ms = Decimal(str(first_draw["expires_at"])) * 1000
        random_orders = self.ledger()["accepted"][first_order_count:]
        assert all(Decimal(order["cTime"]) < deadline_ms for order in random_orders)
        assert self.admin(port, "/positions")[1]["markets"][1]["items"]
        expired_orders = len(self.ledger()["accepted"])
        bill_time = int(time.time() * 1000) - 1000
        self.control(funding_bills=[dict(billId="8801", type="8", subType="174", instId="LIFE-USDT-SWAP",
                                         ts=str(bill_time), pnl="0.0030000000000000001", ccy="USDT")])
        poll(lambda: self.sql("SELECT count(*) FROM funding_bills") == "1", timeout=40,
             description="expired session keeps funding observation alive")
        assert len(self.ledger()["accepted"]) == expired_orders and self.state()["desired_state"] == "stopped"
        self.mm_command(port, "start", market_mode="both", selection="fixed", profile_id="sideways", duration_seconds=600)
        # Interrupt an established runtime here, not the first create dispatch.
        # The separate lost-ACK cases retain their uncertain-intent/exit scope.
        poll(lambda: sum(order["state"] == "live" for order in self.ledger()["orders"]) == 8
             and self.sql("SELECT count(*) FROM orders WHERE creation_pending") == "0",
             description="all initial maker acknowledgments committed before DB outage")
        accepted = len(self.ledger()["accepted"])
        docker("stop", "--time", "5", self.pg)
        poll(lambda: json.loads(docker("inspect", second).stdout)[0]["State"]["Status"] == "exited",
             timeout=10, description="MM DB outage closes owned runtime")
        after_outage = len(self.ledger()["accepted"])
        docker("start", self.pg)
        poll(lambda: docker("exec", self.pg, "pg_isready", "-U", "postgres", check=False).returncode == 0,
             timeout=30, description="MM PostgreSQL restored")
        third, third_port = self.start("mm-db-restart", auto_migrate=False)
        poll(lambda: self.state()["status"].get("phase") == "halted", description="MM DB recovery retains HALT")
        assert self.admin(third_port, "/status")[1]["error"]
        assert len(self.ledger()["accepted"]) == after_outage
        assert after_outage >= accepted
        assert self.state()["recovery_state"]["halt"]
        assert not self.ledger()["unknown"]
        self.no_runtime_files(third)
        expected_header = "1" if self.env["OKX_TRADING_ENV"] == "demo" else None
        assert all(row["simulated"] == expected_header for row in self.ledger()["requests"]
                   if row["path"].startswith(("/api/v5/trade/", "/api/v5/account/")))
        self.metrics.update(matrix=cases, cli_mm_stop=True, legacy_replaces_mm=True, exact_quote_levels=matrix,
                            restart_anchors=True, weighted_reference=True, stale_both=True,
                            fills=2, random_steps=True, expiry=True, expiry_funding=True, reconciliation="matched", db_fault_halt=True)

    def quote_guard_scenario(self):
        print("Offline E2E: strict quote guards, own-only depth, range and delayed joint recovery", flush=True)
        import yaml
        path = self.assets / "life_mm_profiles.yaml"
        original_profiles = path.read_bytes()
        config = yaml.safe_load(path.read_text())
        for market in config["markets"].values():
            market["quote_guards"] = dict(enabled=True, max_deviation_bps="200", resume_deviation_bps="100",
                                          recovery_delay_seconds="3", max_book_levels=10, max_own_orders=200)
        config["profiles"]["sideways"]["sideways_range"] = dict(
            lower_price=".09", upper_price=".11", resume_lower_price=".095", resume_upper_price=".105")
        path.write_text(yaml.safe_dump(config))
        self.report.joinpath("effective-guard-profiles.yaml").write_bytes(path.read_bytes())
        self.env.update(ADMIN_API_ENABLED="true", ADMIN_API_USERNAME="operator", ADMIN_API_PASSWORD="offline-admin")
        for field, value in (("API_KEY", "offline-key"), ("SECRET_KEY", "offline-secret"), ("PASSPHRASE", "offline-pass")):
            self.env["HBOT_OKX_PERPETUAL_" + field] = value
        self.control(target_updates_per_second=20, include_own_orders=True)
        first, port = self.start("guard")
        self.mm_command(port, "start", market_mode="both", selection="fixed", profile_id="sideways", duration_seconds=600)
        poll(lambda: len(self.ledger()["accepted"]) >= 8, description="guard admits both markets with real independent depth")
        session = self.state()["mm_session"]
        started = json.loads(docker("inspect", first).stdout)[0]["State"]["StartedAt"]

        def waiting(reason):
            state = self.state()["status"]
            return state["phase"] == "waiting_for_mm_readiness" and any(
                (row.get("reason") or "").split(":", 1)[0] == reason for row in state.get("mm_markets", []))

        def cancelled():
            return all(order["state"] in ("filled", "canceled") for order in self.ledger()["orders"])

        # A book containing only our acknowledged orders is not a qualified market.
        self.control(book_overrides={"LIFE-USDT-SWAP": {"bids": [], "asks": []}})
        poll(lambda: waiting("market_price_unavailable"), description="own-only perpetual book closes joint admission")
        poll(cancelled, description="both markets cancel while the required market is unavailable")
        assert http(port, "/live")[0] == 200 and http(port, "/ready")[0] == 503
        code, value = self.admin(port, "/status")
        assert code == 200 and any(row["quote_guard"] and row["quote_guard"]["data_quality"] == "unavailable" for row in value["markets"])
        self.control(book_overrides={})
        poll(lambda: waiting("guard_recovering"), description="fresh book waits for recovery observations")
        self.wait_running(port)
        poll(lambda: any(order["state"] == "live" for order in self.ledger()["orders"]), description="quotes after delayed recovery")
        assert self.state()["mm_session"]["anchors"] == session["anchors"]
        assert self.state()["mm_session"]["expires_at"] == session["expires_at"]
        # Deviation is checked against the observed market, not the saved anchor.
        self.control(prices={"LIFE-USDT": ".108"})
        poll(lambda: waiting("reference_deviation"), description="market deviation suspends quotes")
        poll(cancelled, description="deviation cancels both quote sets")
        self.control(prices={"LIFE-USDT": ".12"})
        poll(lambda: waiting("sideways_breakout"), description="manual outer range breakout visible")
        self.control(prices={"LIFE-USDT": ".1"})
        self.wait_running(port)
        # Partial fill cannot turn the old full own book quantity into external depth.
        buy = poll(lambda: next((order for order in self.ledger()["orders"] if order["state"] == "live"
                                 and order["side"] == "buy" and order["instId"] == "LIFE-USDT"), None),
                   description="spot quote available for real partial fill")
        base_before_fill = next(Decimal(row["cashBal"]) for row in self.ledger()["balances"] if row["ccy"] == "LIFE")
        self.control(fill={"clOrdId": buy["clOrdId"], "amount": str(Decimal(buy["sz"]) / 2), "fee": ".001"}, duplicate=True)
        poll(lambda: self.sql("SELECT count(*) FROM trade_fills") == "1", description="partial fill is recorded once")
        self.control(book_overrides={"LIFE-USDT": {"bids": [], "asks": []}})
        poll(lambda: waiting("market_price_unavailable"), description="partial own-only spot depth is rejected")
        poll(cancelled, description="partial-fill remainder cancelled without liquidation")
        self.control(book_overrides={})
        self.wait_running(port)
        assert self.state()["mm_session"]["anchors"] == session["anchors"]
        assert self.state()["mm_session"]["expires_at"] == session["expires_at"]
        assert json.loads(docker("inspect", first).stdout)[0]["State"]["StartedAt"] == started
        assert not self.state()["recovery_state"].get("halt")
        self.terminate(first)
        accepted_before_restart = len(self.ledger()["accepted"])
        _, port = self.start("guard-restart", auto_migrate=False)
        poll(lambda: waiting("guard_recovering"), description="cold process resets guard recovery delay")
        assert len(self.ledger()["accepted"]) == accepted_before_restart
        self.wait_running(port)
        assert self.state()["mm_session"]["anchors"] == session["anchors"]
        assert self.state()["mm_session"]["expires_at"] == session["expires_at"]
        self.mm_command(port, "stop")
        assert self.ledger()["positions"] == []  # Only a spot inventory fill occurred.
        assert next(Decimal(row["cashBal"]) for row in self.ledger()["balances"] if row["ccy"] == "LIFE") == base_before_fill + Decimal(buy["sz"]) / 2
        assert self.sql("SELECT count(*) FROM trade_fills") == "1"
        assert not self.ledger()["unknown"]
        self.metrics["quote_guards"] = dict(own_only_rejected=True, joint_cancel=True, range_breakout=True,
                                            deviation=True, delayed_resume=True, partial_fill=True,
                                            preserved_anchor_deadline=True, unexpected_process_restarts=0, intentional_restarts=1,
                                            effective_config_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        path.write_bytes(original_profiles)

    def drawdown_scenario(self):
        print("Offline E2E: durable drawdown, retained-market marks and position-preserving HALT", flush=True)
        import yaml
        path = self.assets / "life_mm_profiles.yaml"
        original_profiles = path.read_bytes()
        config = yaml.safe_load(path.read_text())
        config.update(max_global_drawdown_quote="1", max_controller_drawdown_quote="1")
        path.write_text(yaml.safe_dump(config))
        self.report.joinpath("effective-drawdown-profiles.yaml").write_bytes(path.read_bytes())
        self.env.update(ADMIN_API_ENABLED="true", ADMIN_API_USERNAME="operator", ADMIN_API_PASSWORD="offline-admin")
        for field, value in (("API_KEY", "offline-key"), ("SECRET_KEY", "offline-secret"), ("PASSPHRASE", "offline-pass")):
            self.env["HBOT_OKX_PERPETUAL_" + field] = value
        first, port = self.start("drawdown")
        self.mm_command(port, "start", market_mode="both", selection="fixed", profile_id="sideways", duration_seconds=600)
        buy = poll(lambda: next((row for row in self.ledger()["orders"] if row["state"] == "live"
                                 and row["instId"] == "LIFE-USDT-SWAP" and row["side"] == "buy"), None),
                   description="perpetual quote for partial drawdown fill")
        self.control(fill={"clOrdId": buy["clOrdId"], "amount": str(Decimal(buy["sz"]) / 2), "fee": ".001"}, duplicate=True)
        poll(lambda: self.sql("SELECT count(*) FROM trade_fills") == "1", description="drawdown partial fill recorded once")
        held = self.ledger()["positions"]
        assert held
        self.mm_command(port, "switch", market_mode="spot", selection="fixed", profile_id="sideways", duration_seconds=600)
        self.wait_running(port)
        boundary = len(self.ledger()["accepted"])
        mutation_boundary = len(self.ledger()["mutations"])

        def risk():
            return self.admin(port, "/status")[1].get("drawdown") or {}

        poll(lambda: risk().get("ready") and Decimal(risk()["controllers"]["life_perpetual"]["pnl_quote"]) > 0,
             description="disabled perpetual exposure still has a fresh marked report")
        peak = Decimal(risk()["global_peak_quote"])
        assert peak > 0 and len(risk()["scope"]) == 2
        session = self.state()["mm_session"]
        self.control(stale_instruments=["LIFE-USDT-SWAP"])
        poll(lambda: risk().get("stale") and not risk().get("ready"), timeout=25,
             description="stale disabled-market valuation blocks new spot risk")
        poll(lambda: all(row["state"] in ("filled", "canceled") for row in self.ledger()["orders"]),
             description="missing drawdown coverage cancels new quotes without closing held exposure")
        assert risk()["global_pnl_quote"] is None and self.ledger()["positions"] == held
        assert http(port, "/live")[0] == 200 and not self.state()["recovery_state"].get("halt")
        self.control(stale_instruments=[])
        self.wait_running(port)
        poll(lambda: risk().get("ready"), description="drawdown coverage recovers in the same process")
        assert Decimal(risk()["global_peak_quote"]) >= peak
        assert self.state()["mm_session"]["anchors"] == session["anchors"]
        assert self.state()["mm_session"]["expires_at"] == session["expires_at"]
        self.terminate(first)
        second, port = self.start("drawdown-clean-restart", auto_migrate=False)
        self.wait_running(port)
        poll(lambda: risk().get("ready"), description="clean restart restores peak and read-only retained feed")
        assert Decimal(risk()["global_peak_quote"]) >= peak
        self.control(prices={"LIFE-USDT": ".08"})
        poll(lambda: self.state()["status"].get("phase") == "halted", description="covered drawdown breach HALTs service trading")
        poll(lambda: all(row["state"] in ("filled", "canceled") for row in self.ledger()["orders"]),
             description="risk HALT cancels remainders and preserves partial fill")
        assert self.ledger()["positions"] == held
        assert all(row["instId"] == "LIFE-USDT" and row["ordType"] == "post_only" for row in self.ledger()["accepted"][boundary:])
        assert risk()["reason_code"] == "drawdown_global_limit" and risk()["operator_action_required"]
        accepted = len(self.ledger()["accepted"])
        self.terminate(second)
        _, port = self.start("drawdown-halt-restart", auto_migrate=False)
        poll(lambda: self.state()["status"].get("phase") == "halted", description="cold restart preserves drawdown HALT")
        stopped = risk()
        assert stopped["stale"] and stopped["global_pnl_quote"] is None
        assert stopped["reason_code"] == "drawdown_global_limit" and Decimal(stopped["global_peak_quote"]) >= peak
        status, _ = self.admin(port, "/commands", dict(action="start", expected_version=self.state()["control_version"],
                                                       market_mode="both", selection="fixed", profile_id="sideways", duration_seconds=600), str(uuid.uuid4()))
        assert status == 409
        assert len(self.ledger()["accepted"]) == accepted and self.ledger()["positions"] == held
        assert len(self.ledger()["mutations"]) == mutation_boundary
        assert not self.ledger()["unknown"]
        self.metrics["drawdown"] = dict(retained_disabled_market=True, partial_fill_preserved=True,
                                        stale_coverage_waits=True, coverage_recovers=True, clean_restart_peak=True,
                                        global_breach_halt=True, restart_halt=True, post_halt_start_rejected=True,
                                        retained_feed_has_no_account_mutations=True,
                                        global_peak_quote=str(peak), fresh_stopped_pnl_not_invented=True,
                                        effective_config_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        path.write_bytes(original_profiles)

    def outflow_scenario(self):
        print("Offline E2E: spot outflow BUY-only admission, committed fills and durable binding", flush=True)
        import yaml
        path = self.assets / "life_mm_profiles.yaml"
        original = path.read_bytes()
        profiles = yaml.safe_load(path.read_text())
        profiles["spot_outflow"] = dict(window_seconds=300, max_net_outflow_quote="30", fee_allowance_rate=".01")
        path.write_text(yaml.safe_dump(profiles))
        self.report.joinpath("effective-outflow-profiles.yaml").write_bytes(path.read_bytes())
        self.env.update(ADMIN_API_ENABLED="true", ADMIN_API_USERNAME="operator", ADMIN_API_PASSWORD="offline-admin")
        for field, value in (("API_KEY", "offline-key"), ("SECRET_KEY", "offline-secret"), ("PASSPHRASE", "offline-pass")):
            self.env["HBOT_OKX_PERPETUAL_" + field] = value
        first, port = self.start("outflow")
        self.mm_command(port, "start", market_mode="both", selection="fixed", profile_id="sideways", duration_seconds=600)

        def live(instrument, side):
            return [row for row in self.ledger()["orders"] if row["instId"] == instrument and row["side"] == side
                    and row["state"] in ("live", "partially_filled")]

        def flow():
            return self.admin(port, "/status")[1].get("spot_outflow") or {}

        poll(lambda: len(live("LIFE-USDT", "buy")) == 1 and len(live("LIFE-USDT", "sell")) == 2,
             description="only one spot BUY fits the outflow cap; both funded sells remain")
        poll(lambda: len(live("LIFE-USDT-SWAP", "buy")) == 2, description="spot cap leaves perpetual admission intact")
        assert flow()["ready"] and Decimal(flow()["reserved_quote"]) <= 30
        buy = live("LIFE-USDT", "buy")[0]
        amount = Decimal(buy["sz"]) / 2
        self.control(fill={"clOrdId": buy["clOrdId"], "amount": str(amount), "fee": ".001"}, duplicate=True)
        expected = amount * Decimal(buy["px"]) + Decimal(".001")
        poll(lambda: flow().get("recorded_net_outflow_quote") is not None
             and Decimal(flow()["recorded_net_outflow_quote"]) == expected, description="committed partial BUY and original USDT fee")
        self.mm_command(port, "stop")
        assert Decimal(flow()["reserved_quote"]) == 0
        assert self.sql("SELECT count(*) FROM trade_fills") == "1"
        binding = self.state()["recovery_state"]["spot_outflow"]
        self.terminate(first)
        _, port = self.start("outflow-restart", auto_migrate=False)
        self.mm_command(port, "start", market_mode="perpetual", selection="fixed", profile_id="sideways", duration_seconds=600)
        poll(lambda: flow().get("ready") and Decimal(flow()["recorded_net_outflow_quote"]) == expected,
             description="restarted perpetual-only session still covers prior spot outflow")
        self.mm_command(port, "switch", market_mode="spot", selection="fixed", profile_id="sideways", duration_seconds=600)
        poll(lambda: len(live("LIFE-USDT", "sell")) == 2, description="funded spot sells continue while BUY levels exceed remaining cap")
        assert not live("LIFE-USDT", "buy")
        assert self.state()["status"]["phase"] == "running" and not self.state()["recovery_state"].get("halt")
        assert self.state()["recovery_state"]["spot_outflow"] == binding
        sell = live("LIFE-USDT", "sell")[0]
        sell_amount = Decimal("100")
        self.control(fill={"clOrdId": sell["clOrdId"], "amount": str(sell_amount), "fee": "-.001"}, duplicate=True)
        after_sell = expected - sell_amount * Decimal(sell["px"]) - Decimal(".001")
        poll(lambda: flow().get("recorded_net_outflow_quote") is not None
             and Decimal(flow()["recorded_net_outflow_quote"]) == after_sell, description="only committed SELL proceeds and actual rebate fund new capacity")
        poll(lambda: len(live("LIFE-USDT", "buy")) == 1, timeout=45, description="BUY level resumes after committed SELL cashflow")
        assert Decimal(flow()["recorded_net_outflow_quote"]) + Decimal(flow()["reserved_quote"]) <= 30
        self.mm_command(port, "stop")
        assert Decimal(flow()["reserved_quote"]) == 0
        assert self.sql("SELECT count(*) FROM trade_fills") == "2" and not self.ledger()["unknown"]
        self.metrics["spot_outflow"] = dict(buy_only_cap=True, perpetual_unaffected=True, funded_sells_continue=True,
                                            partial_commit=True, original_usdt_fee_and_rebate=True, duplicates=2,
                                            terminal_proof_releases=True, restart_and_mode_switch=True, bound_identity=True,
                                            used_quote=str(after_sell), effective_config_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        path.write_bytes(original)

    def bootstrap_scenario(self):
        print("Offline E2E: bounded empty/own-only listing, expiry, restart and market qualification", flush=True)
        import yaml
        path = self.assets / "life_mm_profiles.yaml"
        original = path.read_bytes()
        profiles = yaml.safe_load(path.read_text())
        profiles.update(max_global_drawdown_quote="20", spot_outflow=dict(
            window_seconds=300, max_net_outflow_quote="100", fee_allowance_rate=".01"))
        for market in profiles["markets"].values():
            market.update(initial_anchor=".1", quote_guards=dict(
                enabled=True, mode="bootstrap_then_market", max_deviation_bps="200", resume_deviation_bps="100",
                recovery_delay_seconds="1", bootstrap=dict(duration_seconds=30, max_anchor_move_bps="500",
                                                           max_quote="50", min_external_depth_quote="100",
                                                           max_market_spread_bps="250", qualification_seconds="2")))
        path.write_text(yaml.safe_dump(profiles))
        self.report.joinpath("effective-bootstrap-profiles.yaml").write_bytes(path.read_bytes())
        self.env.update(ADMIN_API_ENABLED="true", ADMIN_API_USERNAME="operator", ADMIN_API_PASSWORD="offline-admin")
        for field, value in (("API_KEY", "offline-key"), ("SECRET_KEY", "offline-secret"), ("PASSPHRASE", "offline-pass")):
            self.env["HBOT_OKX_PERPETUAL_" + field] = value
        empty = {instrument: dict(bids=[], asks=[]) for instrument in ("LIFE-USDT", "LIFE-USDT-SWAP")}
        self.control(book_overrides=empty, include_own_orders=True)
        first, port = self.start("bootstrap")
        self.mm_command(port, "start", market_mode="both", selection="fixed", profile_id="sideways", duration_seconds=600)

        def live():
            return [row for row in self.ledger()["orders"] if row["state"] in ("live", "partially_filled")]

        def guards():
            return [market.get("quote_guard") or {} for market in self.admin(port, "/status")[1].get("markets", [])]

        def episode():
            return self.state()["recovery_state"].get("listing_bootstrap")

        poll(lambda: len(live()) == 8, timeout=60, description="both empty markets quote bounded post-only levels")
        armed = episode()
        for instrument in empty:
            rows = [row for row in live() if row["instId"] == instrument]
            assert all(Decimal(".095") <= Decimal(row["px"]) <= Decimal(".105") for row in rows)
            # SWAP wire size is contracts; the existing LIFE fixture uses 0.01 base per contract.
            assert sum(Decimal(row["px"]) * Decimal(row["sz"])
                       * (Decimal(".01") if instrument.endswith("-SWAP") else 1) for row in rows) <= 50
        assert len(guards()) == 2 and all(g["bootstrap"]["state"] == "bootstrap" for g in guards())
        assert not any(value["market_qualified"] for value in armed["episodes"].values())
        self.mm_command(port, "stop")
        self.terminate(first)
        _, port = self.start("bootstrap-restart", auto_migrate=False)
        self.mm_command(port, "start", market_mode="both", selection="fixed", profile_id="follow_token", duration_seconds=600)
        poll(lambda: len(live()) == 8 or any(g.get("reason_code") == "bootstrap_expired" for g in guards()),
             description="new session/revision reuses original listing clocks")
        assert episode() == armed
        poll(lambda: not live() and len(guards()) == 2
             and all(g.get("reason_code") == "bootstrap_expired" for g in guards()),
             timeout=60, description="original listing deadline cancels both legs without restarting process")
        assert episode() == armed and not self.ledger()["fills"]
        # A single fresh leg cannot admit the other; no fills are injected to qualify.
        qualified = dict(bids=[[".099", "200000"]], asks=[[".101", "200000"]])
        self.control(book_overrides={"LIFE-USDT": qualified, "LIFE-USDT-SWAP": empty["LIFE-USDT-SWAP"]})
        poll(lambda: episode()["episodes"]["life_spot"]["market_qualified"], description="spot qualification checkpoint")
        assert not live()
        self.control(book_overrides={instrument: qualified for instrument in empty})
        poll(lambda: len(guards()) == 2 and all(g["bootstrap"]["state"] == "market_guarded" for g in guards())
             and len(live()) == 8, description="both independent books qualify and resume original model anchors")
        final = episode()
        assert all(value["market_qualified"] for value in final["episodes"].values())
        for key in armed["episodes"]:
            assert final["episodes"][key]["expires_at"] == armed["episodes"][key]["expires_at"]
        self.control(book_overrides=empty)
        poll(lambda: not live() and all(g.get("reason_code") == "market_liquidity_unqualified" for g in guards()),
             description="qualified liquidity loss waits rather than rearming bootstrap")
        assert not self.ledger()["fills"] and not self.state()["recovery_state"].get("halt")
        self.mm_command(port, "stop")
        assert http(port, "/startup")[0] == 200  # Missing outside depth cannot kill the service on stop.
        self.metrics["listing_bootstrap"] = dict(empty_and_own_only=True, both_market_budgets=True,
                                                 original_band_and_clock=True, clean_restart_and_new_session=True, expiry_cancels=True,
                                                 partial_market_cannot_admit=True, durable_qualification=True, lost_depth_never_rearms=True,
                                                 forced_fills=0, clean_stop_after_lost_depth=True,
                                                 effective_config_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        path.write_bytes(original)

    def spread_policy_scenario(self):
        print("Offline E2E: fee/NATR spread response, candle outage and recovery", flush=True)
        import yaml
        path = self.assets / "life_mm_profiles.yaml"
        original = path.read_bytes()
        profiles = yaml.safe_load(path.read_text())
        for name, market in profiles["markets"].items():
            market.update(executor_refresh_time=4, cooldown_time=0)
            volatility = dict(enabled=True, interval="1s", length=5, target_natr=".01",
                              min_multiplier=".5", max_multiplier="3", max_age_seconds=2,
                              on_unavailable="static" if name == "spot" else "wait")
            if name == "spot":
                volatility.update(fallback_buy_spreads=[".01", ".02"], fallback_sell_spreads=[".01", ".02"])
            market["spread_policy"] = dict(fee_floor=True, volatility=volatility)
        path.write_text(yaml.safe_dump(profiles))
        self.report.joinpath("effective-spread-profiles.yaml").write_bytes(path.read_bytes())
        self.env.update(ADMIN_API_ENABLED="true", ADMIN_API_USERNAME="operator", ADMIN_API_PASSWORD="offline-admin")
        for field, value in (("API_KEY", "offline-key"), ("SECRET_KEY", "offline-secret"), ("PASSPHRASE", "offline-pass")):
            self.env["HBOT_OKX_PERPETUAL_" + field] = value
        bot, port = self.start("spread-policy")
        receipt = self.mm_command(port, "start", market_mode="both", selection="fixed", profile_id="sideways", duration_seconds=600)

        def states():
            return {row["market"]: row.get("spread_policy") or {} for row in self.state()["status"].get("mm_markets", [])}

        def live():
            return [row for row in self.ledger()["orders"] if row["state"] in ("live", "partially_filled")]

        poll(lambda: len(states()) == 2 and all(value.get("state") == "dynamic" for value in states().values())
             and len(live()) == 8, description="both candle feeds warm up and configured maker levels appear")
        baseline = states()
        self.control(candle_width=".03")
        poll(lambda: all(Decimal(value.get("multiplier") or 0) > 2 for value in states().values()),
             description="completed candle shock widens capped NATR spreads")
        poll(lambda: len(live()) == 8 and all(Decimal(row["px"]) < Decimal(".098") if row["side"] == "buy"
                                              else Decimal(row["px"]) > Decimal(".102") for row in live()),
             description="normal executor refresh applies wider configured prices")
        shock = states()
        self.control(candle_stale=True)
        poll(lambda: states().get("spot", {}).get("state") == "static_fallback"
             and states().get("perpetual", {}).get("state") == "waiting" and not live(),
             description="declared fallback/wait and joint cancellation on candle outage")
        assert http(port, "/live")[0] == 200
        self.control(candle_stale=False, candle_width=".005")
        recovery_started = time.monotonic()
        poll(lambda: len(states()) == 2 and all(value.get("state") == "dynamic" for value in states().values())
             and len(live()) == 8, timeout=30, description="same process/controller resumes after required contiguous candle tail")
        candle_recovery_seconds = time.monotonic() - recovery_started
        assert json.loads(docker("inspect", bot).stdout)[0]["RestartCount"] == 0
        events = self.admin(port, "/sessions/" + receipt["session_id"] + "/events?limit=500")[1]
        assert events["history_complete"] and any(row["event_type"] == "guard_changed" for row in events["items"])
        assert all(row["ordType"] == "post_only" for row in self.ledger()["orders"])
        assert not self.state()["recovery_state"].get("halt") and not self.ledger()["unknown"]
        self.mm_command(port, "stop")
        assert http(port, "/live")[0] == 200
        self.metrics["spread_policy"] = dict(baseline=baseline, shock=shock, outage_recovers_in_place=True,
                                             candle_recovery_seconds=candle_recovery_seconds, service_restarts=0,
                                             fee_floor=True, configured_maker_prices=True, events=len(events["items"]),
                                             effective_config_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        path.write_bytes(original)

    def inventory_skew_scenario(self):
        print("Offline E2E: existing PMM inventory interpolation on spot/both with normal post-only executors", flush=True)
        import yaml
        path = self.assets / "life_mm_profiles.yaml"
        original = path.read_bytes()
        profiles = yaml.safe_load(path.read_text())
        profiles.update(max_global_drawdown_quote="20", spot_outflow=dict(
            window_seconds=300, max_net_outflow_quote="10000", fee_allowance_rate=".01"))
        profiles["markets"]["spot"]["inventory_skew"] = dict(enabled=True, target_base_pct=".015", inventory_range_multiplier="1")
        for market in profiles["markets"].values():
            market.update(executor_refresh_time=8, cooldown_time=0)
        path.write_text(yaml.safe_dump(profiles))
        self.report.joinpath("effective-inventory-skew-profiles.yaml").write_bytes(path.read_bytes())
        self.env.update(ADMIN_API_ENABLED="true", ADMIN_API_USERNAME="operator", ADMIN_API_PASSWORD="offline-admin")
        for field, value in (("API_KEY", "offline-key"), ("SECRET_KEY", "offline-secret"), ("PASSPHRASE", "offline-pass")):
            self.env["HBOT_OKX_PERPETUAL_" + field] = value
        first, port = self.start("inventory-skew")
        self.mm_command(port, "start", market_mode="spot", selection="fixed", profile_id="sideways", duration_seconds=600)

        def live(instrument="LIFE-USDT"):
            return [row for row in self.ledger()["orders"] if row["instId"] == instrument
                    and row["state"] in ("live", "partially_filled")]

        def status():
            return self.admin(port, "/status")[1].get("markets", [])

        def skew():
            return next((market.get("inventory_skew") or {} for market in status() if market["market"] == "spot"), {})

        def quotes(rows):
            return {side: sum((Decimal(row["px"]) * Decimal(row["sz"]) for row in rows if row["side"] == side), Decimal("0"))
                    for side in ("buy", "sell")}

        poll(lambda: len(live()) == 4 and skew().get("state") == "active", description="spot inventory-skew quotes")
        before = quotes(live())
        assert before["buy"] > before["sell"] and sum(before.values()) <= 100
        assert all(row["ordType"] == "post_only" for row in self.ledger()["accepted"])
        original_ids = {row["clOrdId"] for row in live()}
        for order in [row for row in live() if row["side"] == "buy"]:
            self.control(fill=dict(clOrdId=order["clOrdId"], amount=order["sz"], fee="0"), duplicate=True)
        poll(lambda: self.sql("SELECT count(*) FROM trade_fills") == "2", description="two normal BUY fills recorded once")
        poll(lambda: skew().get("state") == "active" and Decimal(skew()["buy_multiplier"]) < Decimal(skew()["sell_multiplier"]),
             description="observed spot cash inventory reverses the size bias")
        poll(lambda: len(live()) == 4 and not original_ids.intersection(row["clOrdId"] for row in live()),
             timeout=60, description="ordinary refresh replaces old levels with new inventory sizing")
        after = quotes(live())
        assert after["sell"] > after["buy"] and sum(after.values()) <= 100
        self.mm_command(port, "switch", market_mode="both", selection="fixed", profile_id="sideways", duration_seconds=600)
        poll(lambda: len(live()) == 4 and len(live("LIFE-USDT-SWAP")) == 4, description="spot skew and unchanged perpetual coexist")
        markets = status()
        assert next(market for market in markets if market["market"] == "perpetual").get("inventory_skew") is None
        current_ratio = Decimal(skew()["base_pct"])
        balances = {row["ccy"]: Decimal(row["cashBal"]) for row in self.ledger()["balances"]}
        assert abs(current_ratio - balances["LIFE"] * Decimal(".1") / (balances["LIFE"] * Decimal(".1") + balances["USDT"])) < Decimal("1e-20")
        assert sum(quotes(live()).values()) <= 100
        self.mm_command(port, "stop")
        self.terminate(first)
        _, port = self.start("inventory-skew-restart", auto_migrate=False)
        self.mm_command(port, "start", market_mode="both", selection="fixed", profile_id="sideways", duration_seconds=600)
        poll(lambda: skew().get("state") == "active" and len(live()) == 4, description="restart derives skew from fresh existing cash observations")
        assert abs(Decimal(skew()["base_pct"]) - current_ratio) < Decimal("1e-20")
        self.mm_command(port, "stop")
        assert len(self.ledger()["fills"]) == 2 and not self.ledger()["unknown"]
        assert not self.state()["recovery_state"].get("halt") and http(port, "/startup")[0] == 200
        self.metrics["inventory_skew"] = dict(spot_and_both=True, post_only=True, existing_refresh=True,
                                              underweight_quote={key: str(value) for key, value in before.items()},
                                              overweight_quote={key: str(value) for key, value in after.items()},
                                              cash_ratio_not_shared_equity=True, perpetual_unchanged=True, restart_fresh_observation=True,
                                              unique_fills=2, no_extra_market_orders=True,
                                              effective_config_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        path.write_bytes(original)

    def availability_scenario(self):
        print("Offline E2E: service availability and automatic read/feed recovery", flush=True)
        self.env.update(ADMIN_API_ENABLED="true", ADMIN_API_USERNAME="operator", ADMIN_API_PASSWORD="offline-admin")
        for field, value in (("API_KEY", "offline-key"), ("SECRET_KEY", "offline-secret"), ("PASSPHRASE", "offline-pass")):
            self.env["HBOT_OKX_PERPETUAL_" + field] = value
        first, port = self.start("availability", availability=True)
        self.control(target_updates_per_second=20)

        def inject(**values):
            docker("exec", first, "python", "-c", "from pathlib import Path; import sys; "
                   "p=Path('/tmp/availability-faults.next'); p.write_text(sys.argv[1]); "
                   "p.replace('/tmp/availability-faults.json')", json.dumps(values))

        def incident(reason):
            code, value = self.admin(port, "/status")
            return code == 200 and any(row["reason_code"] == reason for row in (value.get("service") or {}).get("incidents", []))

        poll(lambda: bool(self.state().get("status")), description="availability owner status")
        inject(metadata_failures=3)
        payload = dict(action="start", expected_version=self.state()["control_version"], market_mode="perpetual",
                       selection="fixed", profile_id="follow_market", duration_seconds=600)
        code, receipt = self.admin(port, "/commands", payload, str(uuid.uuid4()))
        assert code == 202, receipt
        poll(lambda: incident("controller_startup_read_retry"), description="observable startup read retry")
        assert not self.ledger()["accepted"]
        assert http(port, "/live")[0] == 200
        poll(lambda: self.admin(port, "/commands/" + receipt["id"])[1]["status"] == "succeeded",
             description="same controller recovers after metadata reads")
        poll(lambda: len(self.ledger()["accepted"]) >= 4, description="quotes after safe startup recovery")
        session = self.state()["mm_session"]
        deadline = session["expires_at"]
        process_started = json.loads(docker("inspect", first).stdout)[0]["State"]["StartedAt"]
        for flag, reason in (("reporting_error", "reporting_unavailable"),
                             ("daily_error", "daily_snapshot_unavailable"),
                             ("claim_error", "reconciliation_unavailable")):
            inject(**{flag: True})
            poll(lambda: incident(reason), description=reason)
            assert http(port, "/live")[0] == 200
            assert self.state()["status"]["running"]
            assert not self.state()["recovery_state"].get("halt")
            inject()
            poll(lambda: not incident(reason), description=reason + " recovery")
        self.control(stale_pairs=["BTC-USDT"])
        poll(lambda: self.state()["status"]["phase"] == "waiting_for_mm_readiness",
             timeout=25, description="stale basket suspends quoting without process exit")
        poll(lambda: all(order["state"] in ("filled", "canceled") for order in self.ledger()["orders"]),
             description="stale quotes cancelled")
        assert http(port, "/live")[0] == 200 and http(port, "/ready")[0] == 503
        self.control(stale_pairs=[])
        poll(lambda: self.state()["status"]["phase"] == "running", description="fresh basket automatically resumes")
        assert self.state()["mm_session"]["expires_at"] == deadline
        assert self.state()["mm_session"]["anchors"] == session["anchors"]
        assert json.loads(docker("inspect", first).stdout)[0]["State"]["StartedAt"] == process_started
        self.mm_command(port, "stop")
        assert http(port, "/live")[0] == 200
        # A real failed trading task is different from an optional observation failure.
        self.mm_command(port, "start", market_mode="spot", selection="fixed", profile_id="sideways", duration_seconds=600)
        inject(controller_error=True)
        poll(lambda: bool(self.state()["recovery_state"].get("halt")), description="dead controller creates durable HALT")
        poll(lambda: incident("controller_task_failed"), description="failed task reason exposed")
        assert http(port, "/live")[0] == 200
        self.metrics["availability"] = dict(optional_failures_recovered=3, startup_read_recovered=True,
                                            stale_feed_recovered=True, preserved_anchor_deadline=True,
                                            unexpected_process_restarts=0, failed_controller_halted=True)
        inject()
        self.mm_command(port, "stop")
        assert self.state()["recovery_state"].get("halt")

    def funding_scenario(self):
        print("Offline E2E: stopped funding, exact bills, restart and closed-position catch-up", flush=True)
        self.env.update(ADMIN_API_ENABLED="true", ADMIN_API_USERNAME="operator", ADMIN_API_PASSWORD="offline-admin",
                        FUNDING_SYNC_INTERVAL_SECONDS="1")
        for field, value in (("API_KEY", "offline-key"), ("SECRET_KEY", "offline-secret"), ("PASSPHRASE", "offline-pass")):
            self.env["HBOT_OKX_PERPETUAL_" + field] = value
        first, port = self.start("funding-first")
        poll(lambda: bool(self.state().get("owner_uuid")), description="funding service owner")
        self.mm_command(port, "start", market_mode="perpetual", selection="fixed", profile_id="sideways", duration_seconds=600)
        buy = poll(lambda: next((row for row in self.ledger()["orders"] if row["side"] == "buy" and row["state"] == "live"), None),
                   description="perpetual test quote")
        self.control(fill={"clOrdId": buy["clOrdId"], "amount": buy["sz"], "fee": "0.01"}, duplicate=True)
        poll(lambda: self.sql("SELECT count(*) FROM trade_fills") == "1", description="position fill persisted")
        self.mm_command(port, "stop")
        assert self.ledger()["positions"]
        accepted = len(self.ledger()["accepted"])
        mutations = len(self.ledger()["mutations"])
        timestamp = int(time.time() * 1000) - 5000

        def bill(identity, amount, ts=timestamp, pair="LIFE-USDT-SWAP"):
            return dict(billId=str(identity), type="8", subType="173" if Decimal(amount) < 0 else "174",
                        instId=pair, ts=str(ts), pnl=amount, ccy="USDT")
        bills = [bill(1, "-0.1234567890123456789"), bill(2, "0.0234567890123456789")]
        # Account pages also contain unrelated instruments. Force two pages
        # while retaining only this configured market's funding.
        unrelated = [bill(n, "99", pair="ETH-USDT-SWAP") for n in range(100, 201)]
        self.control(funding_bills=bills + unrelated)
        poll(lambda: self.sql("SELECT count(*) FROM funding_bills") == "2", timeout=60, description="idle funding pages persisted once")
        self.sql(f"INSERT INTO funding_payments(timestamp,config_file_path,market,symbol,rate,amount) VALUES ({timestamp},'legacy.yml','okx_perpetual','LIFE-USDT',0.001,-0.12345678901234568)")

        def totals(expected_paid, expected_received):
            daily = self.admin(port, "/statistics/daily?market=perpetual&pair=LIFE-USDT")[1]
            days = daily["markets"][0]["days"]
            return (sum((Decimal(row["funding_paid"]) for row in days), Decimal(0)) == Decimal(expected_paid)
                    and sum((Decimal(row["funding_received"]) for row in days), Decimal(0)) == Decimal(expected_received))
        poll(lambda: totals("0.1234567890123456789", "0.0234567890123456789"), description="exact funding API without legacy double count")
        self.terminate(first)
        self.control(clear_positions=True)
        bills += [bill(3, "-0.0030000000000000001", timestamp - 1000), bill(4, "0.0020000000000000001")]
        self.control(funding_bills=bills + unrelated)
        second, port = self.start("funding-restart", auto_migrate=False)
        poll(lambda: self.sql("SELECT count(*) FROM funding_bills") == "4", timeout=60, description="closed-position restart catches delayed bills")
        poll(lambda: totals("0.1264567890123456790", "0.0254567890123456790"), description="daily backfilled totals")
        self.control(funding_bills=bills + unrelated)
        time.sleep(2)
        assert self.sql("SELECT count(*) FROM funding_bills") == "4"
        self.control(funding_error=True)
        poll(lambda: (self.admin(port, "/status")[1].get("funding_sync") or {}).get("state") == "unavailable",
             description="funding outage is visible")
        assert self.state()["desired_state"] == "stopped" and not self.state()["status"]["running"]
        self.control(funding_error=False)
        poll(lambda: (self.admin(port, "/status")[1].get("funding_sync") or {}).get("state") == "observed",
             timeout=30, description="funding read recovery")
        config_reads = sum(row["path"] == "/api/v5/account/config" for row in self.ledger()["requests"])
        third, third_port = self.start("funding-standby", auto_migrate=False)
        assert http(third_port, "/live")[1]["state"] == "standby"
        time.sleep(2)
        assert sum(row["path"] == "/api/v5/account/config" for row in self.ledger()["requests"]) == config_reads
        self.terminate(second)
        bills.append(bill(5, "0.001", int(time.time() * 1000) - 500))
        self.control(funding_bills=bills + unrelated)
        poll(lambda: self.sql("SELECT count(*) FROM funding_bills") == "5", timeout=60, description="new owner continues bill cursor")
        # DB loss while idle must not cause any trading mutation; the next
        # owner resumes the last committed bill window after DB restoration.
        docker("stop", "--time", "5", self.pg)
        poll(lambda: json.loads(docker("inspect", third).stdout)[0]["State"]["Status"] == "exited",
             timeout=15, description="idle owner exits after DB loss")
        bills.append(bill(6, "-0.004", int(time.time() * 1000) - 500))
        self.control(funding_bills=bills + unrelated)
        docker("start", self.pg)
        poll(lambda: docker("exec", self.pg, "pg_isready", "-U", "postgres", check=False).returncode == 0,
             timeout=30, description="funding PostgreSQL restored")
        fourth, port = self.start("funding-db-recovery", auto_migrate=False)
        poll(lambda: self.sql("SELECT count(*) FROM funding_bills") == "6", timeout=60, description="funding catches up after DB failure")
        assert len(self.ledger()["accepted"]) == accepted and len(self.ledger()["mutations"]) == mutations
        assert not self.ledger()["positions"] and not self.ledger()["unknown"]
        assert self.state()["desired_state"] == "stopped"
        self.no_runtime_files(fourth)
        self.metrics.update(identified_bills=6, original_amounts_exact=True, same_timestamp_bills=True,
                            stopped_observation=True, closed_position_backfill=True, repeated_pages_idempotent=True,
                            legacy_not_double_counted=True, owner_handover=True, unavailable_visible=True,
                            db_failure_backfill=True, no_trading_mutations_after_stop=True)

    def upgrade_scenario(self, previous_image):
        print("Offline E2E: distinct compatible images, additive fixture, takeover and rollback", flush=True)
        current_image = self.image
        assert json.loads(docker("image", "inspect", current_image).stdout)[0]["Id"] != json.loads(
            docker("image", "inspect", previous_image).stdout)[0]["Id"], "Upgrade requires distinct image contents."
        self.image = previous_image
        old, old_port = self.start("old")
        self.cli("start", "pmm.yml", "--timeout", "0", "--json")
        self.wait_running(old_port)
        buy = next(order for order in self.wait_orders(2) if order["side"] == "buy")
        self.control(fill={"clOrdId": buy["clOrdId"], "amount": "0.04", "fee": "0.004"}, duplicate=True)
        poll(lambda: self.sql("SELECT count(*) FROM public.trade_fills") == "1", description="old image durable economics")
        self.cli("stop", "--expected-version", "1", "--timeout", "9", "--json")
        self.image = current_image
        new, new_port = self.start("new", additive_fixture=True)
        assert http(new_port, "/live")[1]["state"] == "standby"
        assert self.sql("SELECT count(*) FROM information_schema.columns WHERE table_name='orders' AND column_name='offline_upgrade_note'") == "1"
        self.sql("UPDATE public.orders SET offline_upgrade_note='retained' WHERE id='" + buy["clOrdId"] + "'")
        self.terminate(old)
        poll(lambda: self.state().get("revision") == "new", description="new image owns after old exit")
        self.cli("start", "pmm-revised.yml", "--expected-version", "2", "--timeout", "0", "--json")
        self.wait_running(new_port)
        revised_orders = self.wait_orders(4)[-2:]
        assert {Decimal(order["px"]) for order in revised_orders} == {Decimal("98"), Decimal("102")}
        assert self.state()["config_file_path"] == "pmm-revised.yml"
        self.cli("stop", "--expected-version", "3", "--timeout", "9", "--json")
        self.terminate(new)
        self.image = previous_image
        rollback, rollback_port = self.start("rollback", auto_migrate=False)
        self.cli("start", "pmm.yml", "--expected-version", "4", "--timeout", "0", "--json")
        self.wait_running(rollback_port)
        self.wait_orders(6)
        self.cli("stop", "--expected-version", "5", "--timeout", "9", "--json")
        assert self.sql("SELECT count(*) FROM public.trade_fills") == "1"
        assert self.sql("SELECT count(*) FROM public.orders WHERE offline_upgrade_note='retained'") == "1"
        assert "-0.004" in self.cli("history", "pmm.yml")
        self.no_runtime_files(rollback)
        self.terminate(rollback)
        self.image = current_image
        self.metrics.update(previous_manifest_digest=previous_image.split("@", 1)[1],
                            additive_fixture="nullable test-only orders.offline_upgrade_note", rollback_preserved_economics=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default="hummingbot-cloudrun:local")
    parser.add_argument("--scenario", choices=("all", "spot", "v2-spot", "v2-perpetual", "schema", "upgrade", "life-mm", "life-mm-recovery", "funding", "availability", "quote-guards", "drawdown", "spot-outflow", "listing-bootstrap", "inventory-skew", "spread-policy"), default="all")
    parser.add_argument("--previous-image", help="Cached compatible prior image; required for the release upgrade gate.")
    parser.add_argument("--trading-env", choices=("live", "demo"), default="live", help="Local simulator routing mode; never contacts OKX.")
    args = parser.parse_args()
    metadata = json.loads(docker("image", "inspect", args.image).stdout)[0]
    assert metadata["Architecture"] == "amd64", "Build the Linux amd64 deployment target first."
    assert metadata["Size"] <= 1536 * 1024 * 1024, "Cloud Run runtime image exceeds the 1.5 GiB size budget."
    docker("image", "inspect", "postgres:14-alpine")
    image = pin_manifest(args.image)
    previous_image = pin_manifest(args.previous_image) if args.previous_image else None
    if args.scenario == "upgrade" and not previous_image:
        parser.error("--previous-image is required for upgrade")
    assert json.loads(docker("image", "inspect", image).stdout)[0]["Id"] == metadata["Id"]
    report = ROOT / "build" / "e2e-offline" / (time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6])
    report.mkdir(parents=True)
    from test.e2e.offline.build_fixtures import write_fixtures
    with tempfile.TemporaryDirectory(prefix="hbot-e2e-assets-") as directory:
        assets = Path(directory)
        assets.chmod(0o755)
        for name in ("exchange.py", "runtime_smoke.py", "availability_service.py", "pmm.yml", "controller-spot.yml", "controller-perpetual.yml", "script-perpetual.yml"):
            assets.joinpath(name).write_bytes((ROOT / "test/e2e/offline" / name).read_bytes())
        write_fixtures(assets / "fixtures.json")
        import yaml
        profiles = yaml.safe_load((ROOT / "deploy/cloudrun/conf/life_mm_profiles.yaml").read_text())
        # Test policy belongs to the immutable fixture, just like production YAML.
        profiles["profiles"]["follow_token"]["references"] = [{"pair": "AVAX-USDT", "weight": "1"}]
        profiles["random"] = dict(profile_ids=["sideways"], step_duration_seconds=15)
        for config in profiles["markets"].values():
            config.update(executor_refresh_time=120, cooldown_time=1)
        assets.joinpath("life_mm_profiles.yaml").write_text(yaml.safe_dump(profiles))
        assets.joinpath("999_offline_upgrade.sql").write_text("ALTER TABLE public.orders ADD COLUMN IF NOT EXISTS offline_upgrade_note TEXT;\n")
        assets.joinpath("pmm-revised.yml").write_text((assets / "pmm.yml").read_text().replace("bid_spread: 1", "bid_spread: 2").replace("ask_spread: 1", "ask_spread: 2"))
        result = {"image_id": metadata["Id"], "registry_manifest_digest": image.split("@", 1)[1],
                  "image_size_bytes": metadata["Size"], "image_size_budget_bytes": 1536 * 1024 * 1024,
                  "architecture": "amd64", "config_sha256": hashlib.sha256((assets / "pmm.yml").read_bytes()).hexdigest(),
                  "fixture_sha256": {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(assets.iterdir())}}
        try:
            print("Offline E2E: runtime ABI, JIT, TLS, SDK discovery and size budget", flush=True)
            smoke = docker("run", "--rm", "--platform", "linux/amd64", "--network", "none", "--read-only",
                           "--tmpfs", "/tmp:rw,nosuid,size=128m", "--mount",
                           f"type=bind,source={assets},target=/test-assets,readonly",
                           image, "python", "/test-assets/runtime_smoke.py", timeout=90)
            result["runtime_smoke"] = json.loads(smoke.stdout)
            scenarios = ("schema", "spot", "v2-spot", "v2-perpetual", "life-mm", "funding", "availability", "quote-guards", "drawdown", "spot-outflow", "listing-bootstrap", "inventory-skew", "spread-policy") if args.scenario == "all" else (args.scenario,)
            if args.scenario == "all" and previous_image:
                scenarios += ("upgrade",)
            result["scenarios"] = {}
            for scenario in scenarios:
                scenario_report = report / scenario
                scenario_report.mkdir()
                harness = Harness(image, assets, scenario_report, args.trading_env)
                try:
                    harness.setup()
                    if scenario == "spot":
                        harness.scenario()
                    elif scenario == "schema":
                        harness.schema_scenario()
                    elif scenario == "funding":
                        harness.funding_scenario()
                    elif scenario == "availability":
                        harness.availability_scenario()
                    elif scenario == "quote-guards":
                        harness.quote_guard_scenario()
                    elif scenario == "drawdown":
                        harness.drawdown_scenario()
                    elif scenario == "spread-policy":
                        harness.spread_policy_scenario()
                    elif scenario == "inventory-skew":
                        harness.inventory_skew_scenario()
                    elif scenario == "listing-bootstrap":
                        harness.bootstrap_scenario()
                    elif scenario == "spot-outflow":
                        harness.outflow_scenario()
                    elif scenario == "upgrade":
                        harness.upgrade_scenario(previous_image)
                    elif scenario in ("life-mm", "life-mm-recovery"):
                        harness.mm_scenario(matrix=scenario == "life-mm")
                    else:
                        harness.v2_scenario(perpetual=scenario == "v2-perpetual")
                    result["scenarios"][scenario] = {"passed": True, "metrics": harness.metrics}
                finally:
                    harness.cleanup()
            result.update(passed=True, release_scope_complete=args.scenario == "all" and previous_image is not None)
            print(json.dumps(result, indent=2))
            return 0
        except Exception as error:
            result.update(passed=False, error=str(error))
            traceback.print_exc()
            print(f"Offline E2E failed: {error}\nEvidence: {report}", file=sys.stderr)
            return 1
        finally:
            report.joinpath("result.json").write_text(json.dumps(result, indent=2))


if __name__ == "__main__":
    sys.exit(main())
