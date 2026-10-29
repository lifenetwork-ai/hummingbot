import asyncio
import os
import select
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from hummingbot.cli.service import ServiceHost
from hummingbot.client.config.runtime_config import RuntimeSettings
from hummingbot.core.web_assistant.connections.offline_endpoint import endpoint


@pytest.mark.asyncio
async def test_nonowner_never_constructs_strategy_or_publishes_owner_status():
    settings = RuntimeSettings.from_environment({"DATABASE_URL": "postgresql://unused/test"})
    host = ServiceHost(settings, asyncio.Event(), MagicMock())
    host.owner = MagicMock()
    host.owner.try_acquire.return_value = False
    host.start_strategy = AsyncMock()
    await host.observe_once()
    assert host.phase == "standby" and not host.running
    host.start_strategy.assert_not_awaited()
    host.owner.store.publish.assert_not_called()


@pytest.mark.asyncio
async def test_health_separates_valid_standby_startup_from_trading_readiness():
    settings = RuntimeSettings.from_environment({"DATABASE_URL": "postgresql://unused/test"})
    host = ServiceHost(settings, asyncio.Event(), MagicMock())
    assert (await host.health(SimpleNamespace(path="/startup"))).status == 503
    assert (await host.health(SimpleNamespace(path="/live"))).status == 200
    host.bootstrapped, host.phase = True, "standby"
    assert (await host.health(SimpleNamespace(path="/startup"))).status == 200
    assert (await host.health(SimpleNamespace(path="/ready"))).status == 503
    host.stop.set()
    assert (await host.health(SimpleNamespace(path="/startup"))).status == 503


def test_process_deadline_exits_even_when_main_thread_is_in_blocking_cleanup(tmp_path):
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[3]), "PYTHONDONTWRITEBYTECODE": "1"}
    program = """
import asyncio, time
from hummingbot.cli.service import ShutdownDeadline
ShutdownDeadline(asyncio.Event(), seconds=0.1).request()
time.sleep(30)
"""
    result = subprocess.run([sys.executable, "-c", program], env=env, cwd=tmp_path, capture_output=True, timeout=5)
    assert result.returncode == 1
    assert not list(tmp_path.iterdir())


def test_sigterm_wakes_an_idle_async_service_without_waiting_for_health_traffic(tmp_path):
    program = """
import asyncio
from hummingbot.cli.service import ShutdownDeadline
async def run():
    stop = asyncio.Event()
    deadline = ShutdownDeadline(stop, seconds=3)
    deadline.install()
    try:
        print('ready', flush=True)
        await stop.wait()
    finally:
        deadline.close()
asyncio.run(run())
"""
    env = {**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[3]), 'PYTHONDONTWRITEBYTECODE': '1'}
    process = subprocess.Popen([sys.executable, '-c', program], cwd=tmp_path, env=env,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert select.select([process.stdout], [], [], 10)[0], 'Idle child did not start'
        assert process.stdout.readline().strip() == 'ready'
        time.sleep(.1)  # Let the real selector become idle, with no timers/IO.
        process.terminate()
        assert process.wait(timeout=1) == 0
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=3)


def test_offline_transport_preserves_paths_and_refuses_real_keys_and_cloud(monkeypatch):
    monkeypatch.delenv("HBOT_OFFLINE_HTTP_ORIGIN", raising=False)
    assert endpoint("https://www.okx.com/api/v5/public/time") == "https://www.okx.com/api/v5/public/time"
    monkeypatch.setenv("HBOT_OFFLINE_HTTP_ORIGIN", "http://exchange:8080")
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused/test")
    monkeypatch.delenv("K_SERVICE", raising=False)
    for prefix in ("HBOT_OKX_", "HBOT_OKX_PERPETUAL_"):
        for field, value in (("API_KEY", "offline-key"), ("SECRET_KEY", "offline-secret"), ("PASSPHRASE", "offline-pass")):
            monkeypatch.setenv(prefix + field, value)
    assert endpoint("https://www.okx.com/api/v5/trade/order?a=1") == "http://exchange:8080/api/v5/trade/order?a=1"
    assert endpoint("wss://ws.okx.com:8443/ws/v5/private") == "ws://exchange:8080/ws/v5/private"
    monkeypatch.setenv("HBOT_OKX_API_KEY", "real-looking-key")
    with pytest.raises(ValueError, match="fixture credentials"):
        endpoint("https://www.okx.com")
    monkeypatch.setenv("K_SERVICE", "actual-cloud-service")
    with pytest.raises(ValueError, match="forbidden"):
        endpoint("https://www.okx.com")


@pytest.mark.parametrize("identity", ["", "offline-spot-controller"])
def test_postgres_controller_identity_cannot_be_empty_or_duplicated(tmp_path, monkeypatch, identity):
    from hummingbot.client import settings
    from hummingbot.strategy.strategy_v2_base import StrategyV2ConfigBase
    fixture = Path(__file__).resolve().parents[3] / "test/e2e/offline/controller-spot.yml"
    original = fixture.read_text()
    (tmp_path / "first.yml").write_text(original)
    (tmp_path / "second.yml").write_text(original.replace("id: offline-spot-controller", f'id: "{identity}"'))
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused/test")
    monkeypatch.setattr(settings, "CONTROLLERS_CONF_DIR_PATH", tmp_path)
    config = StrategyV2ConfigBase(script_file_name="v2_with_controllers.py", controllers_config=["first.yml", "second.yml"])
    with pytest.raises(ValueError, match="stable IDs"):
        config.load_controller_configs()
