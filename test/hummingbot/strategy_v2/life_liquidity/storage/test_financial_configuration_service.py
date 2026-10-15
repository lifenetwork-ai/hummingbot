"""Configured signed revision capture/drain/discard without active policy edits."""
from dataclasses import replace
from datetime import timedelta
from test.hummingbot.strategy_v2.life_liquidity.storage.test_quote_service import quote_service
from test.hummingbot.strategy_v2.life_liquidity.storage.test_service_control import (
    attach_empty_observer,
    control_policy,
    keys,
)
from uuid import UUID

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import OwnerMode, StorageError, encode
from hummingbot.strategy_v2.life_liquidity.storage.control_plane import OperatorKeys, OwnerTransition
from hummingbot.strategy_v2.life_liquidity.storage.financial_configuration import DiscardFinancialConfiguration


async def configured(tmp_path, **kwargs):
    existing = keys()
    signer = OperatorKeys(existing.keys, {key: roles | frozenset(('CONFIGURE',)) for key, roles in existing.roles.items()})
    service, state = await quote_service(tmp_path, control_config=control_policy(signer),
                                         control_seed=lambda b, o: encode(signer.seed(b, {'mode': 'ACTIVE', 'token': o.json()})), **kwargs)
    attach_empty_observer(service)
    return service, state, signer


def sign(service, signer, command, snapshot, role='CONFIGURE'):
    return signer.sign('operator', service.store.binding, role, command.json(), snapshot.cut, snapshot.facts,
                       issued_at=service.sources.clock(), expires_at=service.sources.clock() + timedelta(seconds=1))


@pytest.mark.asyncio
async def test_service_stages_signed_candidate_and_discard_requires_separate_activation(tmp_path):
    service, _, signer = await configured(tmp_path)
    try:
        active = service.controller.config.model_dump_json()
        before = await service.store.snapshot(service.store.binding)
        command, snapshot = await service.control.propose_financial_configuration(
            {'session': {'duration': '4h'}, 'quotes': {'spreads_bps': ['40'], 'sizes_base': ['2']}}, operation_id='candidate')
        assert not service.runtime._local_ready() and service.admission_paused
        assert service.controller.config.model_dump_json() == active
        signature = sign(service, signer, command, snapshot)
        receipt = await service.control.apply(command, approval=signature, snapshot=snapshot)
        assert receipt.state == 'STAGED' and not service.runtime._local_ready()
        assert any('policy handoff required' in line for line in service.status_lines())
        assert (await service.store.current_owner(service.store.binding)).mode is OwnerMode.DRAINING
        staged = await service.store.snapshot(service.store.binding)
        assert staged.session == before.session and staged.balance == before.balance and staged.budget == before.budget
        assert service.controller.config.model_dump_json() == active
        assert await service.plan() == []
        current = await service.control.snapshot()
        activation = OwnerTransition('blocked', OwnerMode.ACTIVE, replace(service.spot.owner, generation=UUID(int=711)))
        with pytest.raises(StorageError, match='CONFIGURATION_HANDOFF_REQUIRED'):
            await service.control.apply(activation, approval=sign(service, signer, activation, current, 'ACTIVATE'), snapshot=current)
        discard = DiscardFinancialConfiguration('discard', 1, command.operation_id, service.spot.owner)
        await service.control.apply(discard, approval=sign(service, signer, discard, current), snapshot=current)
        assert not service.runtime._local_ready() and service.controller.config.model_dump_json() == active
        current = await service.control.snapshot()
        activation = replace(activation, operation_id='explicit-activation')
        await service.control.apply(activation, approval=sign(service, signer, activation, current, 'ACTIVATE'), snapshot=current)
        assert service.runtime._local_ready()
        assert (await service.control.apply(command, approval=signature, snapshot=snapshot)).replayed
        assert service.runtime._local_ready() and (await service.store.financial_configuration(service.store.binding)).pending is None
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_cold_service_loads_old_configuration_and_retains_pending_signed_revision(tmp_path):
    from hummingbot.strategy_v2.life_liquidity.storage.service import FileLiquidityService
    service, _, signer = await configured(tmp_path)
    config = service.config
    args = dict(connectors=service.connectors, sources=service.sources, market_data_provider=service.controller.market_data_provider)
    try:
        command, snapshot = await service.control.propose_financial_configuration({'reference': {'lookback': '30m'}}, operation_id='cold-candidate')
        await service.control.apply(command, approval=sign(service, signer, command, snapshot), snapshot=snapshot)
    finally:
        await service.close()
    cold = await FileLiquidityService.open(config, **args)
    try:
        assert cold.controller.config.model_dump_json() == config.controller.model_dump_json()
        assert (await cold.store.financial_configuration(cold.store.binding)).pending == command
        assert cold.store._cold_unarmed and not cold.runtime._local_ready()
        assert any('policy handoff required' in line for line in cold.status_lines())
    finally:
        await cold.close()


@pytest.mark.asyncio
async def test_control_source_capture_uses_independent_facts_and_keeps_admission_closed(tmp_path):
    service, _, signer = await configured(tmp_path)
    try:
        active = service.controller.config.model_dump_json()
        command, snapshot = await service.control.propose_financial_configuration({'session': {'duration': '2h'}}, operation_id='capture')
        await service.control.apply(command, approval=sign(service, signer, command, snapshot), snapshot=snapshot)
        before = await service.store.snapshot(service.store.binding)
        source = await service.control.financial_configuration_source()
        assert source.scope == 'CORE_SPOT' and source.pending == command
        assert source.cut == before.cut and source.unresolved_intents == ()
        assert source.facts.order_scope_complete and source.facts.bill_scope_complete and source.facts.runner_scope_complete
        assert (await service.store.snapshot(service.store.binding)).cut == before.cut
        assert service.controller.config.model_dump_json() == active
        assert service.admission_paused and not service.runtime._local_ready()
        assert await service.plan() == []
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_control_source_requires_a_staged_revision_and_independent_collector(tmp_path):
    service, _, signer = await configured(tmp_path)
    try:
        with pytest.raises(StorageError, match='CONFIG_HANDOFF_STAGED_SOURCE_REQUIRED'):
            await service.control.financial_configuration_source()
        assert not service.runtime._local_ready()
        command, snapshot = await service.control.propose_financial_configuration({'session': {'duration': '2h'}}, operation_id='source')
        await service.control.apply(command, approval=sign(service, signer, command, snapshot), snapshot=snapshot)
        service.spot._observer = None
        with pytest.raises(StorageError, match='SERVICE_CONTROL_SOURCE_REQUIRED'):
            await service.control.financial_configuration_source()
        assert not service.runtime._local_ready()
    finally:
        await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('changes', [{'risk': {'max_net_base': '1'}}, {'session': {'duration': 'invalid'}},
                                     {'session': {'duration': '9' * 70000 + 'h'}}])
async def test_bad_proposal_never_enters_signed_history_or_changes_active_config(tmp_path, changes):
    service, _, _ = await configured(tmp_path)
    try:
        active = service.controller.config.model_dump_json()
        with pytest.raises(StorageError):
            await service.control.propose_financial_configuration(changes, operation_id='bad')
        assert service.controller.config.model_dump_json() == active
        assert (await service.store.financial_configuration(service.store.binding)).sequence == 0
        assert not service.runtime._local_ready()
    finally:
        await service.close()
