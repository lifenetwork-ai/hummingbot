import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from hummingbot.cli.credential_runtime import ManagedCredentialRuntime
from hummingbot.client.config.runtime_config import RuntimeConfigurationError


@pytest.mark.asyncio
async def test_loaded_version_does_not_call_kms_again_on_start_or_provider_outage():
    source = SimpleNamespace(store=SimpleNamespace(metadata=Mock(return_value=[dict(connector="okx", version=1, state="active")])),
                             load_configs=Mock(return_value=({"okx": "existing-model"}, {"okx": 1})),
                             verify_configs=AsyncMock(), close=Mock())
    runtime = ManagedCredentialRuntime(SimpleNamespace(), factory=lambda state: source)
    runtime.install = Mock()
    assert await runtime.reload() == {"okx": 1}
    source.load_configs.side_effect = RuntimeError("KMS outage")
    assert await runtime.reload() == {"okx": 1}
    assert source.load_configs.call_count == 1 and source.verify_configs.await_count == 1
    await runtime.close()


@pytest.mark.asyncio
async def test_failed_replacement_detaches_old_configs_and_missing_key_keeps_retry_possible():
    attempts = 0
    source = SimpleNamespace(store=SimpleNamespace(metadata=Mock(return_value=[])), load_configs=Mock(return_value=({}, {})),
                             verify_configs=AsyncMock(), close=Mock())

    def factory(state):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeConfigurationError("Missing KEK")
        return source

    runtime = ManagedCredentialRuntime(SimpleNamespace(), factory=factory)
    runtime.install = Mock()
    with pytest.raises(RuntimeConfigurationError):
        await runtime.reload()
    assert runtime.versions == {}
    assert await runtime.reload() == {}
    assert attempts == 2
    await runtime.close()


@pytest.mark.asyncio
async def test_slow_crypto_runs_off_loop_and_runtime_close_clears_configs():
    import threading
    entered, release = threading.Event(), threading.Event()

    def load(names):
        entered.set()
        assert release.wait(2)
        return {}, {}

    source = SimpleNamespace(store=SimpleNamespace(metadata=Mock(return_value=[])), load_configs=load,
                             verify_configs=AsyncMock(), close=Mock())
    runtime = ManagedCredentialRuntime(SimpleNamespace(), factory=lambda state: source)
    runtime.install = Mock()
    pending = asyncio.create_task(runtime.reload())
    while not entered.is_set():
        await asyncio.sleep(.01)
    assert not pending.done()  # The loop remains available while crypto is blocked.
    release.set()
    await pending
    await runtime.close()
    assert runtime.install.call_args.args == ({},)


@pytest.mark.asyncio
async def test_service_detaches_funding_and_reconciliation_before_replacing_credentials():
    from hummingbot.cli.service import ServiceHost
    from hummingbot.client.config.runtime_config import RuntimeSettings
    host = ServiceHost(RuntimeSettings.from_environment({"DATABASE_URL": "postgresql://unused/test"}), asyncio.Event(), Mock())
    host.funding.credentials_changed = AsyncMock()
    old_reader = asyncio.create_task(asyncio.sleep(100))
    host.reconciliation_task = old_reader
    await asyncio.sleep(0)
    await host.detach_credential_readers()
    host.funding.credentials_changed.assert_awaited_once()
    assert old_reader.cancelled() and host.reconciliation_task is None
