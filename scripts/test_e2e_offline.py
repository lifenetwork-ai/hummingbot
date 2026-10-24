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
        docker("push", tag, timeout=240)
        metadata = json.loads(docker("image", "inspect", tag).stdout)[0]
        pinned = next(digest for digest in metadata["RepoDigests"] if digest.startswith(repository + "@"))
        docker("pull", pinned, timeout=60)
        return pinned
    finally:
        docker("rm", "-f", name, check=False)


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


def http(container, path, data=None):
    # Docker internal networks intentionally have no published host ports. Keep
    # the HTTP probe/control client inside the target's network namespace too.
    program = """
import json, sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
data = None if sys.argv[2] == 'null' else sys.argv[2].encode()
request = Request('http://127.0.0.1:8080' + sys.argv[1], data=data, headers={'Content-Type':'application/json'})
try:
    with urlopen(request, timeout=2) as response:
        print(json.dumps([response.status, json.load(response)]))
except HTTPError as error:
    print(json.dumps([error.code, json.load(error)]))
except (URLError, TimeoutError):
    print('[null,null]')
"""
    result = docker("exec", container, "python", "-c", program, path, json.dumps(data), timeout=10, check=False)
    return json.loads(result.stdout) if result.returncode == 0 else (None, None)


class Harness:
    def __init__(self, image, assets, report):
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
                    "HBOT_OKX_API_KEY": "offline-key", "HBOT_OKX_SECRET_KEY": "offline-secret",
                    "HBOT_OKX_PASSPHRASE": "offline-pass", "HBOT_OFFLINE_HTTP_ORIGIN": "http://exchange:8080"}
        self.metrics = {}

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

    def start(self, revision, *, auto_migrate=True, runtime_role=False):
        name = self.prefix + "-" + revision
        env = {**self.env, "K_REVISION": revision, "ENABLE_AUTO_MIGRATION": str(auto_migrate).lower()}
        if runtime_role:
            env["DATABASE_URL"] = f"postgresql+psycopg2://hbot_runtime:{self.password}@db:5432/hbot_test_offline"
        self.run_container(name, ["--platform", "linux/amd64", "--cpus", "1", "--memory", "2g", "--read-only",
                                  "--tmpfs", "/tmp:rw,nosuid,size=128m", "--mount",
                                  f"type=bind,source={self.assets / 'pmm.yml'},target=/home/hummingbot/conf/strategies/pmm.yml,readonly"],
                           env=env, publish=True)
        port = self.port(name)
        started = time.monotonic()
        poll(lambda: http(port, "/startup")[0] == 200, timeout=200, description=f"{revision} validated startup")
        self.metrics[revision + "_startup_s"] = round(time.monotonic() - started, 3)
        assert http(port, "/live")[0] == 200
        assert docker("exec", name, "python", "-c", "from pathlib import Path; print(Path('/proc/1/cmdline').read_bytes())").stdout.startswith("b'python\\x00-m\\x00hummingbot.cli.service")
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
        for number, amount, fee in ((1, "0.04", "0.004"), (2, "0.06", "0.006")):
            self.control(fill={"clOrdId": buy["clOrdId"], "amount": amount, "fee": fee}, duplicate=True)
            poll(lambda: self.sql("SELECT count(*) FROM public.trade_fills") == str(number), description="deduplicated partial fill")
        totals = json.loads(self.sql("SELECT json_build_object('amount',sum(amount)::text,'fees',sum(trade_fee_in_quote)::text,'price',min(price)::text) FROM public.trade_fills"))
        # Existing SqliteDecimal represents scale-six values in BIGINT on PG too.
        assert Decimal(totals["amount"]) / 1000000 == Decimal("0.1")
        assert Decimal(totals["fees"]) / 1000000 == Decimal("0.01")
        assert Decimal(totals["price"]) / 1000000 == Decimal("99")
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
        self.metrics["accepted_orders"] = 12
        self.metrics["persisted_fills"] = 2

    def cleanup(self):
        if hasattr(self, "exchange_port"):
            try:
                self.report.joinpath("exchange-ledger.json").write_text(json.dumps(self.ledger(), indent=2))
            except Exception:
                pass
        for name in reversed(self.containers):
            result = docker("logs", name, check=False)
            self.report.joinpath(name + ".log").write_text(result.stdout + result.stderr)
            docker("rm", "-f", name, check=False)
        docker("network", "rm", self.network, check=False)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default="hummingbot-cloudrun:local")
    args = parser.parse_args()
    metadata = json.loads(docker("image", "inspect", args.image).stdout)[0]
    assert metadata["Architecture"] == "amd64", "Build the Linux amd64 deployment target first."
    docker("image", "inspect", "postgres:14-alpine")
    image = pin_manifest(args.image)
    assert json.loads(docker("image", "inspect", image).stdout)[0]["Id"] == metadata["Id"]
    report = ROOT / "build" / "e2e-offline" / (time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6])
    report.mkdir(parents=True)
    from test.e2e.offline.build_fixtures import write_fixtures
    with tempfile.TemporaryDirectory(prefix="hbot-e2e-assets-") as directory:
        assets = Path(directory)
        assets.chmod(0o755)
        for name in ("exchange.py", "pmm.yml"):
            assets.joinpath(name).write_bytes((ROOT / "test/e2e/offline" / name).read_bytes())
        write_fixtures(assets / "fixtures.json")
        harness = Harness(image, assets, report)
        result = {"image_id": metadata["Id"], "registry_manifest_digest": image.split("@", 1)[1],
                  "architecture": "amd64", "config_sha256": hashlib.sha256((assets / "pmm.yml").read_bytes()).hexdigest()}
        try:
            harness.setup()
            harness.scenario()
            result.update(passed=True, metrics=harness.metrics)
            print(json.dumps(result, indent=2))
            return 0
        except Exception as error:
            result.update(passed=False, error=str(error), metrics=harness.metrics)
            print(f"Offline E2E failed: {error}\nEvidence: {report}", file=sys.stderr)
            return 1
        finally:
            harness.cleanup()
            report.joinpath("result.json").write_text(json.dumps(result, indent=2))


if __name__ == "__main__":
    sys.exit(main())
