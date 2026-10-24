import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from hummingbot.cli import bot
from hummingbot.cli.data import get_trades
from hummingbot.model.bot_state_store import BotStateStore, ControlConflict
from hummingbot.model.db_migration.postgres.runner import bootstrap_postgres
from hummingbot.model.order import Order
from hummingbot.model.trade_fill import TradeFill


@pytest.fixture
def state(pg_engine):
    bootstrap_postgres(pg_engine, auto_migrate=True)
    store = BotStateStore(pg_engine, "hummingbot")
    store.ensure()
    return store


def test_concurrent_control_rejects_stale_intent(state):
    def change(desired):
        try:
            return state.control(expected_version=0, desired_state=desired,
                                 config_file_path="test.yml", config_type="v1-strategy")
        except ControlConflict:
            return "conflict"
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(change, ("running", "stopped")))
    assert sorted(map(str, results)) == ["1", "conflict"]
    assert state.read()["control_version"] == 1
    with pytest.raises(ControlConflict):
        state.control(expected_version=0, desired_state="stopped")


def test_dedicated_db_rejects_another_bot_identity(state):
    with pytest.raises(ControlConflict, match="BOT_ID"):
        BotStateStore(state.engine, "another-bot").ensure()


def test_old_owner_and_old_ack_cannot_overwrite_newer_intent(state):
    version = state.control(expected_version=0, desired_state="running", config_file_path="test.yml", config_type="controller")
    with state.engine.begin() as connection:
        connection.execute(text("UPDATE public.bot_state SET owner_uuid='owner', owner_generation=2"))
    assert not state.publish(owner_uuid="stale", owner_generation=1, version=version, status={"running": True})
    assert state.publish(owner_uuid="owner", owner_generation=2, version=version, status={"running": True}, revision="test-revision")
    new_version = state.control(expected_version=version, desired_state="stopped")
    assert not state.publish(owner_uuid="owner", owner_generation=2, version=version, status={"running": True})
    assert state.publish(owner_uuid="owner", owner_generation=2, version=new_version, status={"running": False})
    row = state.read()
    assert row["acknowledged_version"] == new_version and row["desired_state"] == "stopped"


def cli(args, tmp_path):
    env = {**os.environ, "DATABASE_URL": os.environ["HBOT_TEST_POSTGRES_URL"], "K_SERVICE": "offline-test",
           "BOT_ID": "hummingbot", "PYTHONDONTWRITEBYTECODE": "1",
           "PYTHONPATH": str(Path(__file__).resolve().parents[3])}
    env.pop("HBOT_ENV_FILE", None)
    # Constrain Python HTTP/WS sockets. libpq connects only to the supplied disposable
    # DB; history must not fetch live marks, prices or fee conversion rates.
    program = """
import runpy, socket, sys
original = socket.socket.connect
def offline(self, address):
    if isinstance(address, tuple) and address[0] not in ('127.0.0.1', '::1'):
        raise RuntimeError('External network is disabled in this offline test')
    return original(self, address)
socket.socket.connect = offline
sys.argv = ['hbot', *sys.argv[1:]]
runpy.run_module('hummingbot.cli.main', run_name='__main__')
"""
    return subprocess.run([sys.executable, "-c", program, *args], cwd=tmp_path, env=env,
                          capture_output=True, text=True, timeout=30)


def test_separate_cli_processes_control_and_read_without_runtime_files(state, tmp_path):
    with state.engine.begin() as connection:
        connection.execute(text("UPDATE public.bot_state SET owner_uuid='owner', owner_generation=1"))
    state.publish(owner_uuid="owner", owner_generation=1, version=0,
                  status={"running": False, "available_configs": {"v1-strategy": ["test.yml"]}}, revision="r1")
    started = cli(["start", "test.yml", "--timeout", "0", "--json", "--expected-version", "0"], tmp_path)
    assert started.returncode == 0, started.stderr
    assert json.loads(started.stdout)["status"] == "requested"
    assert state.read()["desired_state"] == "running"
    stopped = cli(["stop", "--timeout", "0", "--json", "--expected-version", "1"], tmp_path)
    assert stopped.returncode == 0, stopped.stderr
    assert json.loads(stopped.stdout)["control_version"] == 2
    assert state.read()["desired_state"] == "stopped"
    stale = cli(["stop", "--timeout", "0", "--expected-version", "1"], tmp_path)
    assert stale.returncode != 0
    status = cli(["status", "--json"], tmp_path)
    assert status.returncode == 0, status.stderr
    assert json.loads(status.stdout)["desired_state"] == "stopped"
    assert not list(tmp_path.iterdir())


def test_stale_heartbeat_is_unknown_not_live(state):
    with state.engine.begin() as connection:
        connection.execute(text("""UPDATE public.bot_state SET owner_uuid='owner', owner_generation=1,
            status='{"running": true}', heartbeat=clock_timestamp() - INTERVAL '30 seconds'"""))
    with patch.dict(os.environ, {"DATABASE_URL": os.environ["HBOT_TEST_POSTGRES_URL"]}), \
            patch.object(bot, "_postgres_store", state), \
            patch.object(bot, "bot_dir", side_effect=AssertionError("No runtime directory")):
        snapshot = bot.read_status()
    assert snapshot["stale"] is True and snapshot["running"] is False
    assert snapshot["snapshot_age_s"] >= 30


def test_history_exact_filter_and_existing_performance_without_network(state, tmp_path):
    for number, config in enumerate(("test.yml", "test-other.yml")):
        with Session(state.engine) as session, session.begin():
            order = Order(id=f"order-{number}", config_file_path=config, strategy="pure_market_making", market="okx",
                          symbol="BTC-USDT", base_asset="BTC", quote_asset="USDT", creation_timestamp=1700000000000,
                          order_type="LIMIT", amount=Decimal("1"), leverage=1, price=Decimal("100"),
                          last_status="OrderFilled", last_update_timestamp=1700000001000)
            session.add(TradeFill(order=order, config_file_path=config, strategy="pure_market_making", market="okx",
                                  symbol="BTC-USDT", base_asset="BTC", quote_asset="USDT", timestamp=1700000001000,
                                  trade_type="BUY", order_type="LIMIT", price=Decimal("100"), amount=Decimal("1"),
                                  leverage=1, trade_fee={"percent": "0", "flat_fees": [{"token": "USDT", "amount": "0.2"}]},
                                  trade_fee_in_quote=Decimal("0.2"), exchange_trade_id=f"fill-{number}"))
    with patch.dict(os.environ, {"DATABASE_URL": os.environ["HBOT_TEST_POSTGRES_URL"]}), patch.object(bot, "_postgres_store", state):
        assert len(get_trades("postgresql", config_file_path="test.yml")) == 1
        assert get_trades("postgresql", config_file_path="test") == []
    history = cli(["history", "test.yml"], tmp_path)
    assert history.returncode == 0, history.stderr
    assert "last recorded fill" in history.stdout
    assert "0.2" in history.stdout and "-0.2" in history.stdout
    assert "error:" not in history.stdout
    assert not list(tmp_path.iterdir())


def test_force_and_foreground_do_not_signal_or_spawn_remote_pids(state, tmp_path):
    forced = cli(["stop", "--force", "--timeout", "0"], tmp_path)
    assert forced.returncode != 0
    assert state.read()["control_version"] == 0
    log = cli(["logs", "--json"], tmp_path)
    assert log.returncode == 0 and json.loads(log.stdout)["source"] == "Cloud Logging"


def test_imported_but_undeployed_config_cannot_start_and_cloud_yaml_is_readonly(state, tmp_path):
    with state.engine.begin() as connection:
        connection.execute(text("UPDATE public.bot_state SET owner_uuid='owner', owner_generation=1"))
    state.publish(owner_uuid="owner", owner_generation=1, version=0,
                  status={"running": False, "available_configs": {"v1-strategy": ["deployed.yml"]}})
    state.control(expected_version=0, config_file_path="local-only.yml", config_type="v1-strategy")
    result = cli(["start", "--timeout", "0"], tmp_path)
    assert result.returncode != 0
    assert state.read()["control_version"] == 1
    result = cli(["config", "log_level", "DEBUG"], tmp_path)
    assert result.returncode != 0 and "read-only" in result.stderr
    assert not list(tmp_path.iterdir())
