"""Run a disposable local PostgreSQL spike and retain every outcome.

Usage: PYTHONPATH=<directory containing asyncpg>:. conda run -n hummingbot
       python -m test.hummingbot.strategy_v2.life_liquidity.postgres_spike.run --output /tmp/life-pg-results
"""
import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import time
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[4]
IMAGE = "postgres@sha256:e17e86066e5ef83e0952a9347f5c792b7ece00972e2aa787a6986f471b3dd3d5"  # noqa: documentation
BUDGETS = ROOT / "test/hummingbot/strategy_v2/life_liquidity/fixtures/offline_inputs/budgets.json"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def source_snapshot():
    files = list(HERE.glob("*.py")) + list(HERE.glob("*.sql")) + [HERE / "requirements.txt"]
    files.extend(ROOT / name for name in (
        "hummingbot/strategy/strategy_v2_base.py",
        "hummingbot/strategy_v2/executors/order_executor/order_executor.py",
        "hummingbot/core/web_assistant/connections/rest_connection.py",
        "hummingbot/core/web_assistant/rest_assistant.py",
        "hummingbot/connector/exchange_py_base.py",
        "hummingbot/connector/exchange/okx/okx_exchange.py",
        "hummingbot/connector/markets_recorder.py",
        "hummingbot/strategy_v2/executors/executor_orchestrator.py",
        "test/hummingbot/core/web_assistant/test_async_send_authorization.py",
        "test/hummingbot/strategy_v2/life_liquidity/test_o8_performance.py"))
    return {str(path.relative_to(ROOT)): sha(path) for path in sorted(files)}


def xml_result(path):
    root = ET.parse(path).getroot()
    cases = []
    scales = []
    for node in root.iter("testcase"):
        outcome = next((name for name in ("failure", "error", "skipped") if node.find(name) is not None), "passed")
        cases.append({"name": node.attrib["name"], "outcome": outcome})
    for node in root.iter("property"):
        if node.attrib["name"] == "life_pg_spike_scale":
            scales.append(json.loads(node.attrib["value"]))
    return {"xml": path.name, "sha256": sha(path), "cases": cases, "scale": scales,
            "counts": {name: sum(c["outcome"] == name for c in cases)
                       for name in ("passed", "failure", "error", "skipped")}}


def docker(*args):
    return subprocess.check_output(["docker", *args], text=True, stderr=subprocess.STDOUT).strip()


def main():
    import asyncpg

    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--assembly", action="store_true", help="Exercise the actual runner, OKX and recorder paths")
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)  # Never overwrite a previous outcome.
    sources, budget_sha = source_snapshot(), sha(BUDGETS)
    report = {"scope": "DISPOSABLE_SPOT_SPIKE_NOT_FULL_O8_ASSEMBLY", "production_permission": False,
              "offline_complete": False, "source_sha256": sources, "budget_sha256": budget_sha,
              "python": sys.version, "platform": platform.platform(), "asyncpg": asyncpg.__version__,
              "host_load": os.getloadavg(), "host_cpus": os.cpu_count(), "image": IMAGE,
              "container_cpus": 2, "container_memory_bytes": 1024 ** 3, "runs": []}
    if args.assembly:
        report["scope"] = "SPOT_PG_V2_OKX_SQLITE_PROJECTION_ASSEMBLY_SYNTHETIC_HTTP"
    name = "life-pg-spike-" + uuid.uuid4().hex[:12]
    created = False
    try:
        docker("run", "-d", "--name", name, "--label", "life.spike=disposable",
               "--cpus", "2", "--memory", "1g", "-e", "POSTGRES_HOST_AUTH_METHOD=trust",
               "-e", "POSTGRES_DB=life_spike", "-p", "127.0.0.1::5432", IMAGE)
        created = True
        for _ in range(100):
            ready = subprocess.run(["docker", "exec", name, "pg_isready", "-h", "127.0.0.1",
                                    "-U", "postgres", "-d", "life_spike"],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if ready.returncode == 0:
                break
            time.sleep(.1)
        else:
            raise RuntimeError("disposable PostgreSQL did not become ready")
        docker("exec", name, "psql", "-U", "postgres", "-d", "life_spike", "-c",
               "CREATE ROLE life_spike_runtime LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE;")
        report["postgres_version"] = docker("exec", name, "psql", "-U", "postgres", "-d", "life_spike", "-Atc",
                                            "SELECT version()")
        port = docker("port", name, "5432/tcp").split(":")[-1]
        env = {**os.environ, "LIFE_PG_SPIKE_DSN": f"postgresql://postgres@127.0.0.1:{port}/life_spike"}
        env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
        for kind, selection in (("functional", "not scale"), ("scale", "scale")):
            path = out / (kind + ".xml")
            cases = "pg_assembly_cases.py" if args.assembly else "pg_spike_cases.py"
            cmd = [sys.executable, "-m", "pytest", "-q", str(HERE / cases), "-k", selection,
                   "--junitxml=" + str(path)]
            with (out / (kind + ".log")).open("w") as log:
                result = subprocess.run(cmd, env=env, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
            report["runs"].append({"command": cmd, "returncode": result.returncode, **xml_result(path)})
        report["source_unchanged"] = sources == source_snapshot()
        assert report["source_unchanged"] and budget_sha == sha(BUDGETS)
        red = Path("/tmp/life-pg-spike-red.xml")
        if red.exists():
            shutil.copyfile(red, out / "initial_red.xml")
            report["initial_red"] = xml_result(out / "initial_red.xml")
    except Exception as exc:
        report["runner_error"] = {"type": type(exc).__name__, "message": str(exc)}
        raise
    finally:
        if created:
            (out / "postgres.log").write_text(docker("logs", name))
            docker("rm", "-f", name)
        report["disposable_container_removed"] = created
        (out / "results.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"output": str(out), "runs": [(r["xml"], r["counts"]) for r in report["runs"]]}))
    return int(any(r["returncode"] for r in report["runs"]))


if __name__ == "__main__":
    sys.exit(main())
