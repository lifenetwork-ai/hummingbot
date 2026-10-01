"""Load the pure config module while the host lacks full Hummingbot dependencies."""

import importlib.util
import sys
from pathlib import Path


def load_config_module():
    name = "life_liquidity_config_under_test"
    if name not in sys.modules:
        path = (Path(__file__).resolve().parents[4] / "hummingbot/strategy_v2/life_liquidity/config.py")
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


def simulation_config_data():
    return {
        "execution_mode": "simulation",
        "spot": {"enabled": True, "connector": "okx", "pair": "LIFE-USDT"},
        "perpetual": {"enabled": False},
        "session": {"duration": "4h", "start_policy": "when_ready", "on_expiry": "pause_quotes"},
        "reference": {"mode": "market", "lookback": "15m"},
        "quotes": {"spreads_bps": ["30"], "sizes_base": ["10"]},
        "risk": {},
        "economics": {"objective": "profit_mm"},
    }
