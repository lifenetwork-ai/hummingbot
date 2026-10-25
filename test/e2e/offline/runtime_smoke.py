"""Credential-free ABI/runtime checks for the slim deployment image."""
import importlib
import importlib.util
import json
import shutil
import ssl
import subprocess
import sys
from pathlib import Path

import numpy as np
import talib
from numba import njit


def main():
    sys.path.insert(0, str(Path.cwd()))
    from hummingbot.client.settings import AllConnectorSettings
    from hummingbot.model.db_migration.postgres.runner import SCRIPTS

    for executable in ("gcc", "g++", "rustc", "cargo", "conda"):
        assert shutil.which(executable) is None, f"Build tool shipped in runtime: {executable}"
    for module in ("Cython", "pytest", "conda_build"):
        spec = importlib.util.find_spec(module)
        assert spec is None or spec.origin is None, f"Build/test module shipped in runtime: {module}"
    for module in ("hummingbot.core.clock", "hummingbot.core.data_type.order_book",
                   "hummingbot.strategy.pure_market_making.pure_market_making",
                   "hummingbot.connector.exchange.okx.okx_exchange",
                   "hummingbot.connector.derivative.okx_perpetual.okx_perpetual_derivative",
                   "hummingbot.strategy_v2.executors.executor_orchestrator", "pandas_ta", "psycopg2"):
        importlib.import_module(module)
    connectors = AllConnectorSettings.get_connector_settings()
    assert {"okx", "okx_perpetual"} <= connectors.keys()
    # Numba needs LLVM at runtime even though Rust/GCC are build-only.
    assert njit(lambda number: number + 1)(2) == 3
    assert talib.SMA(np.array([1., 2., 3.]), timeperiod=2)[-1] == 2.5
    ssl.create_default_context()
    trust = ssl.get_default_verify_paths()
    assert trust.cafile and Path(trust.cafile).is_file()
    assert len(list(SCRIPTS.glob("*.sql"))) == 13
    assert (SCRIPTS.parent / "manual_scripts/001_normalize_legacy_names.sql").is_file()
    cli_version = subprocess.check_output(["hbot", "--version"], text=True, timeout=20).strip()
    assert cli_version
    report = json.loads(Path("cloudrun-runtime-packages.json").read_text())
    assert report["environment_bytes_after"] < report["environment_bytes_before"]
    print(json.dumps({"passed": True, "numba_jit": True, "ta_lib": True, "tls_trust": True,
                      "connector_count": len(connectors), "sql_scripts": len(list(SCRIPTS.glob("*.sql"))),
                      "build_tools_absent": True, "cli_version": cli_version,
                      "environment_bytes": report["environment_bytes_after"]}))


if __name__ == "__main__":
    main()
