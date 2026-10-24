"""Run PostgreSQL tests against a disposable, loopback-only Docker PostgreSQL 14.

Never reads local .env or a production DSN. No compose dependency or persistent volume.
"""
import os
import secrets
import subprocess
import sys
import time
import uuid
from pathlib import Path


def main() -> int:
    name = "hbot-test-" + uuid.uuid4().hex[:12]
    password = secrets.token_hex(16)
    database = "hbot_test_port"
    root = Path(__file__).resolve().parents[1]
    try:
        subprocess.run(["docker", "run", "-d", "--name", name, "-e", f"POSTGRES_PASSWORD={password}",
                        "-e", f"POSTGRES_DB={database}", "-p", "127.0.0.1::5432", "postgres:14-alpine"],
                       check=True, capture_output=True, text=True, timeout=60)
        port = subprocess.check_output(["docker", "port", name, "5432/tcp"], text=True).strip().rsplit(":", 1)[1]
        deadline = time.monotonic() + 30
        while True:
            ready = subprocess.run(["docker", "exec", name, "pg_isready", "-U", "postgres", "-d", database],
                                   capture_output=True, timeout=5)
            if ready.returncode == 0:
                break
            if time.monotonic() > deadline:
                raise RuntimeError("Disposable PostgreSQL failed readiness.")
            time.sleep(0.1)
        env = {**os.environ, "HBOT_TEST_POSTGRES_URL": f"postgresql+psycopg2://postgres:{password}@127.0.0.1:{port}/{database}"}
        # Production/connector secrets have no role in this isolated job.
        for key in list(env):
            if key in ("DATABASE_URL", "K_SERVICE", "HBOT_ENV_FILE") or key.startswith("HBOT_OKX"):
                env.pop(key)
        return subprocess.run([sys.executable, "-m", "pytest", "-q", "test/integration/postgres", *sys.argv[1:]],
                              cwd=root, env=env).returncode
    except (OSError, subprocess.SubprocessError, RuntimeError):
        print("PostgreSQL test infrastructure failed; tests were not skipped.", file=sys.stderr)
        return 1
    finally:
        subprocess.run(["docker", "rm", "-f", "-v", name], capture_output=True, timeout=30)


if __name__ == "__main__":
    sys.exit(main())
