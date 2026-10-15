"""Only externally signed public-key bootstrap enters the configured service."""
import json
from test.hummingbot.strategy_v2.life_liquidity.storage.test_quote_service import quote_service

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.control_plane import OperatorKeys
from hummingbot.strategy_v2.life_liquidity.storage.service_config import ControlServiceConfig


def keys():
    return OperatorKeys({'operator': b'x' * 32}, {'operator': frozenset(('ACTIVATE', 'DRAIN', 'RECOVER', 'REVOKE', 'IMPORT', 'APPROVE_CASHFLOW', 'INVENTORY'))})


def control_policy(signer):
    verifier = signer.verifier()
    return ControlServiceConfig(public_keys={k: v.hex() for k, v in verifier.keys.items()},
                                roles={k: tuple(sorted(v)) for k, v in verifier.roles.items()})


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['none', 'signature', 'owner'])
async def test_configured_control_seed_keeps_signing_key_outside_runtime(tmp_path, fault):
    from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError, encode
    signer = keys()

    def seed(binding, owner):
        ownership = {'mode': 'ACTIVE', 'token': owner.json()}
        if fault == 'owner':
            ownership['mode'] = 'UNARMED'
        value = signer.seed(binding, ownership)
        if fault == 'signature':
            value['signature'] = '0' * 128
        return encode(value)
    if fault != 'none':
        with pytest.raises((StorageError, ValueError)):
            await quote_service(tmp_path, control_config=control_policy(signer), control_seed=seed)
    else:
        service, _ = await quote_service(tmp_path, control_config=control_policy(signer), control_seed=seed)
        try:
            assert service.store._operator_seed_signer is None
            assert service.sources.control_seed is None
            assert service.store._operator_keys == signer.verifier()
            assert (await service.store.read(service.store.binding)).resources['control_seed']['operator_id'] == 'operator'
        finally:
            await service.close()


def attach_empty_observer(service, *, rate_limits=None):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from hummingbot.connector.exchange.okx import okx_constants
    from hummingbot.core.api_throttler.async_throttler import AsyncThrottler
    from hummingbot.core.web_assistant.connections.rest_connection import RESTConnection
    from hummingbot.core.web_assistant.rest_assistant import RESTAssistant
    from hummingbot.strategy_v2.life_liquidity.storage.spot_observation import OkxSpotRepositoryObserver
    connector = service.connectors['okx']

    class Response:
        status, content_type = 200, 'application/json'

        def __init__(self, body):
            self.body = body

        async def json(self):
            return self.body

    class Session:
        async def request(self, **request):
            from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_application import empty_transport
            return Response(empty_transport(request, [], {'now': service.sources.clock()}))
    assistant = RESTAssistant(connection=RESTConnection(Session()), throttler=AsyncThrottler(
        okx_constants.RATE_LIMITS if rate_limits is None else rate_limits))
    connector._web_assistants_factory = SimpleNamespace(get_rest_assistant=AsyncMock(return_value=assistant))
    connector._api_request_url = AsyncMock(side_effect=lambda path_url, **_: 'https://www.okx.com' + path_url)
    service.spot.install_observer(OkxSpotRepositoryObserver(service.spot, independent_fill_value=lambda *_: None,
                                                            independent_cashflow_value=lambda *_: None, max_pages=2))


@pytest.mark.asyncio
async def test_configured_signed_activation_uses_collected_cut_and_replay_never_rearms(tmp_path):
    from dataclasses import replace
    from datetime import timedelta
    from uuid import UUID

    from hummingbot.strategy_v2.life_liquidity.storage.contracts import OwnerMode
    from hummingbot.strategy_v2.life_liquidity.storage.control_plane import OwnerTransition
    signer = keys()
    service, _ = await quote_service(tmp_path, control_config=control_policy(signer),
                                     control_seed=lambda b, o: json.dumps(signer.seed(b, {'mode': 'ACTIVE', 'token': o.json()})).encode())
    attach_empty_observer(service)
    try:
        snapshot = await service.control.snapshot()
        assert service.admission_paused and not service.runtime._local_ready()
        token = replace(service.spot.owner, generation=UUID(int=921))
        command = OwnerTransition('configured-activation', OwnerMode.ACTIVE, token)
        signature = signer.sign('operator', service.store.binding, 'ACTIVATE', command.json(), snapshot.cut, snapshot.facts,
                                issued_at=service.sources.clock(), expires_at=service.sources.clock() + timedelta(seconds=1))
        receipt = await service.control.apply(command, approval=signature, snapshot=snapshot)
        assert not receipt.replayed and service.runtime._local_ready()
        assert service.spot.owner == service.runtime.owner == token
        assert (await service.control.apply(command, approval=signature, snapshot=snapshot)).replayed
        assert service.runtime._local_ready()
        service.runtime.revoke()
        assert (await service.control.apply(command, approval=signature, snapshot=snapshot)).replayed
        assert not service.runtime._local_ready()
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_configured_joint_signed_activation_rearms_only_fresh_empty_account_and_replay_does_not(tmp_path):
    from dataclasses import replace
    from datetime import timedelta
    from decimal import Decimal as D
    from test.hummingbot.strategy_v2.life_liquidity.storage.test_service_startup import joint_service
    from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_financial_contracts import NOW
    from uuid import UUID

    from hummingbot.strategy_v2.life_liquidity.storage.contracts import OwnerMode, encode
    from hummingbot.strategy_v2.life_liquidity.storage.control_plane import OwnerTransition
    from hummingbot.strategy_v2.life_liquidity.storage.recovery import SpotFinancialRecoveryFacts
    signer = keys()

    def callbacks(cfg, src):
        facts = SpotFinancialRecoveryFacts(cfg.controller.storage_binding, NOW, D('5'), D('10'), D('0'), D('0'),
                                           (), (), (), (), True, True, True)
        return dict(control_seed=lambda b, o: encode(signer.seed(b, {'mode': 'ACTIVE', 'token': o.json()})),
                    joint_spot_recovery=lambda: ((), facts))
    service, _, _, _, _ = await joint_service(tmp_path, hedge=False, config_extra={'control': control_policy(signer)}, source_extra=callbacks)
    service.controller.trading_permissions_ready = lambda: True  # Explicit synthetic release.
    try:
        snapshot = await service.control.snapshot()
        token = replace(service.spot.owner, generation=UUID(int=1822))
        command = OwnerTransition('joint-activate', OwnerMode.ACTIVE, token)
        approval = signer.sign('operator', service.store.binding, 'ACTIVATE', command.json(), snapshot.cut, snapshot.facts,
                               issued_at=NOW, expires_at=NOW + timedelta(seconds=1))
        result = await service.control.apply(command, approval=approval, snapshot=snapshot)
        assert not result.replayed
        assert service.runtime.owner == service.spot.owner == service.swap.owner == service.joint.owner == token
        assert service.runtime._local_ready() and service.swap.local_ready()
        service.runtime.revoke()
        assert (await service.control.apply(command, approval=approval, snapshot=snapshot)).replayed
        assert not service.runtime._local_ready() and not service.swap.local_ready()
    finally:
        await service.close()
