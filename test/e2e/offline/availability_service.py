"""Fault injection around the normal service, only on the offline dummy network."""
import importlib.util
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, "/home/hummingbot")

import hummingbot.cli.admin_observations as observations  # noqa: E402
import hummingbot.model.admin_reporting as reporting  # noqa: E402
from controllers.market_making.life_mm import LifeMMController  # noqa: E402
from hummingbot.cli.service import main  # noqa: E402
from hummingbot.connector.exchange.okx.okx_exchange import OkxExchange  # noqa: E402
from hummingbot.model.admin_store import AdminStore  # noqa: E402

assert os.environ.get("HBOT_OFFLINE_HTTP_ORIGIN") == "http://exchange:8080"
assert "K_SERVICE" not in os.environ

FAULTS = Path("/tmp/availability-faults.json")


def faults():
    return json.loads(FAULTS.read_text()) if FAULTS.exists() else {}


capture = observations.capture_reporting
save = reporting.save_daily_balances
claim = AdminStore.claim_check
rules = OkxExchange._update_trading_rules
control = LifeMMController.control_task


def capture_reporting(*args):
    if faults().get("reporting_error"):
        raise ValueError("offline injected report failure")
    return capture(*args)


def save_daily_balances(*args):
    if faults().get("daily_error"):
        raise ValueError("offline injected optional snapshot failure")
    return save(*args)


def claim_check(*args):
    if faults().get("claim_error"):
        raise ValueError("offline injected check claim failure")
    return claim(*args)


async def update_rules(self):
    values = faults()
    if not self.is_trading_required and values.get("metadata_failures", 0):
        values["metadata_failures"] -= 1
        updated = FAULTS.with_suffix(".rules")
        updated.write_text(json.dumps(values))
        updated.replace(FAULTS)
        raise OSError("HTTP status is 503. Offline read-only initialization failure")
    return await rules(self)


async def control_task(self):
    if faults().get("controller_error"):
        raise RuntimeError("offline injected controller failure")
    return await control(self)


observations.capture_reporting = capture_reporting
reporting.save_daily_balances = save_daily_balances
AdminStore.claim_check = claim_check
OkxExchange._update_trading_rules = update_rules
LifeMMController.control_task = control_task
if importlib.util.find_spec('hummingbot.cli.service_processes'):
    from hummingbot.cli import service_processes
    service_processes.child_command = lambda role: [sys.executable, __file__, '--role', role]
main()
