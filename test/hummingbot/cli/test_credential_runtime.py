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
    host.hb = SimpleNamespace(trading_core=SimpleNamespace(before_position_checkpoint=object(), after_order_cancellation=object()))
    host.owner = SimpleNamespace(listing_bootstrap=object(), spot_outflow=object(), volatility_checkpoints=object(), mm_quote_check=object())
    old_reader = asyncio.create_task(asyncio.sleep(100))
    host.reconciliation_task = old_reader
    await asyncio.sleep(0)
    await host.detach_credential_readers()
    host.funding.credentials_changed.assert_awaited_once()
    assert old_reader.cancelled() and host.reconciliation_task is None
    assert host.hb.trading_core.before_position_checkpoint is None and host.hb.trading_core.after_order_cancellation is None
    assert host.owner.listing_bootstrap is None and host.owner.volatility_checkpoints is None


@pytest.mark.asyncio
async def test_disconnect_can_use_loaded_bundle_during_kms_outage_and_detaches_selected_slot():
    source = SimpleNamespace(store=SimpleNamespace(metadata=Mock(return_value=[dict(connector="okx", version=1, state="active")])),
                             load_configs=Mock(return_value=({"okx": "loaded-model"}, {"okx": 1})),
                             load_cleanup_configs=Mock(side_effect=RuntimeError("KMS unavailable")),
                             verify_configs=AsyncMock(), loaded_bindings={"okx": "account"}, close=Mock())
    runtime = ManagedCredentialRuntime(SimpleNamespace(), factory=lambda state: source)
    runtime.install = Mock()
    await runtime.reload()
    assert await runtime.reload_cleanup(dict(id="disconnect", affected_versions={"okx": 1})) == {"okx": 1}
    source.load_cleanup_configs.assert_not_called()
    runtime.detach_selected("okx")
    assert runtime.versions == {} and runtime.configs == {}
    assert runtime.install.call_args.args == ({},)
    await runtime.close()


@pytest.mark.asyncio
async def test_cold_cleanup_reports_missing_kek_as_configuration_error():
    from hummingbot.core.credential_encryption import CredentialCryptoError
    runtime = ManagedCredentialRuntime(SimpleNamespace(), factory=Mock(side_effect=RuntimeConfigurationError('Missing KEK')))
    runtime.install = Mock()
    try:
        with pytest.raises(CredentialCryptoError) as error:
            await runtime.reload_cleanup(dict(id='disconnect', affected_versions={'okx': 1}))
        assert error.value.code == 'kms_configuration_invalid' and not runtime.configs
    finally:
        await runtime.close()
