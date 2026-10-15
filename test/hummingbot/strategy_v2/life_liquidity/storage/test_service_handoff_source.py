"""Configured source capture covers declared files and scoped SQL together."""
import json
from dataclasses import replace
from decimal import Decimal as D
from pathlib import Path
from test.hummingbot.strategy_v2.life_liquidity.storage.test_financial_configuration_service import configured, sign

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError

LIMITS = {'max_bytes': 8 * 1024 * 1024, 'max_rows': 128}  # Explicit offline capacities.


async def staged(tmp_path, **kwargs):
    service, state, signer = await configured(tmp_path, **kwargs)
    command, snapshot = await service.control.propose_financial_configuration({'session': {'duration': '2h'}}, operation_id='handoff')
    await service.control.apply(command, approval=sign(service, signer, command, snapshot), snapshot=snapshot)
    return service, state


@pytest.mark.asyncio
async def test_service_source_includes_quote_history_and_configuration_sql_without_writes(tmp_path):
    service, _ = await staged(tmp_path)
    try:
        before = await service.store.snapshot(service.store.binding)
        source = await service.control.financial_configuration_service_source(**LIMITS)
        assert source.scope == 'FILE_SERVICE_SCOPED' and source.core.cut == before.cut
        assert dict(source.ancillary_images)['quote_actions'] == (Path(service.config.state_dir) / 'quote_actions.json').read_bytes()
        tables = json.loads(source.recorder.rows_json)['tables']
        assert len(tables['Controllers']) == 1
        assert tables['Controllers'][0]['config']['binding'] == service.store.binding.json()
        assert source.recorder.cut == source.core.cut.checkpoint
        assert json.loads(source.service_json)['controller']['strategy']['config_version'] == 1
        assert source.core.pending.target_hash != service.store.binding.config_hash
        assert (await service.store.snapshot(service.store.binding)).cut == before.cut
        assert service.admission_paused and not service.runtime._local_ready()
        refreshed = await service.control.validate_financial_configuration_service_source(source, **LIMITS)
        assert refreshed.content_digest == source.content_digest
    finally:
        await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['missing', 'symlink', 'corrupt'])
async def test_declared_journal_cannot_be_omitted_or_adopted(tmp_path, fault):
    service, _ = await staged(tmp_path)
    try:
        path = Path(service.config.state_dir) / 'quote_actions.json'
        original = path.read_bytes()
        if fault == 'missing':
            path.unlink()
        elif fault == 'symlink':
            other = tmp_path / 'copy.json'
            other.write_bytes(original)
            path.unlink()
            path.symlink_to(other)
        else:
            value = json.loads(original)
            value['account_uid'] = '999'
            path.write_text(json.dumps(value))
        with pytest.raises(StorageError):
            await service.control.financial_configuration_service_source(**LIMITS)
        assert not service.runtime._local_ready()
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_service_source_limit_never_returns_partial_graph(tmp_path):
    service, _ = await staged(tmp_path)
    try:
        before = await service.store.snapshot(service.store.binding)
        with pytest.raises(StorageError, match='CAPACITY'):
            await service.control.financial_configuration_service_source(max_bytes=64, max_rows=128)
        assert (await service.store.snapshot(service.store.binding)).cut == before.cut
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_risk_and_both_probe_policies_are_included_without_rearming(tmp_path):
    from test.hummingbot.strategy_v2.life_liquidity.storage.test_markout_probe_service import markout_policy
    from test.hummingbot.strategy_v2.life_liquidity.storage.test_risk_service import policy, sources

    from hummingbot.strategy_v2.life_liquidity.config import ResumePolicyConfig
    _, callbacks = sources()
    callbacks['horizon_observation'] = lambda *_: None
    service, _ = await staged(tmp_path, risk_config=policy(stable_data_ms=2000, markout=markout_policy(), recovery_probe_campaign_base=D('.2')),
                              risk_sources=callbacks, resume_policy=ResumePolicyConfig(stable_data_duration='2s', probe_size_base='.1'))
    try:
        before = await service.store.snapshot(service.store.binding)
        image = await service.control.financial_configuration_service_source(**LIMITS)
        assert set(dict(image.ancillary_images)) == {'quote_actions', 'safety', 'recovery_probe', 'markout_probe'}
        for name, raw in image.ancillary_images:
            assert raw == (Path(service.config.state_dir) / (name + '.json')).read_bytes()
        assert service.risk.gate.state == 'PAUSED'
        assert (await service.store.snapshot(service.store.binding)).budget == before.budget
        assert not service.runtime._local_ready()
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_inventory_exit_metadata_and_separate_exit_budget_are_retained(tmp_path):
    from datetime import timedelta
    from test.hummingbot.strategy_v2.life_liquidity.storage.test_exit_budget_repository import EXIT_BUDGET
    from test.hummingbot.strategy_v2.life_liquidity.storage.test_risk_service import policy, sources
    from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_financial_contracts import NOW

    from hummingbot.strategy_v2.life_liquidity.inventory_execution import InventoryExecutionPolicy
    from hummingbot.strategy_v2.life_liquidity.storage.service_config import ExitServiceConfig, InventoryServiceConfig
    p = InventoryExecutionPolicy('SELL', D('2'), D('1'), NOW + timedelta(minutes=1), D('1'), D('3'),
                                 D('500'), D('.1'), D('1'), D('.1'), D('1'), D('.01'), D('.1'), D('.1'))
    inventory = InventoryServiceConfig(campaign_id='source-campaign', started_at=NOW, policy=p, max_age_ms=1000)
    exit_config = ExitServiceConfig(target_base=D('3'), max_child_base=D('1'), max_slippage_bps=D('150'), max_age_ms=1000, budget=EXIT_BUDGET)
    _, callbacks = sources()
    callbacks.update(inventory_observation=lambda: None, exit_observation=lambda: None)
    service, _ = await staged(tmp_path, risk_config=policy(), risk_sources=callbacks, inventory_config=inventory, exit_config=exit_config)
    try:
        before = await service.store.snapshot(service.store.binding)
        image = await service.control.financial_configuration_service_source(**LIMITS)
        assert dict(image.core.journal_images)['exit_subsidy'] == (Path(service.config.state_dir) / 'exit_budget.json').read_bytes()
        resources = json.loads(image.core.checkpoint_bytes)['state']['resources']
        assert resources['inventory_service_policy']['campaign_id'] == 'source-campaign'
        assert resources['exit_service_policy']['budget']['campaign_id'] == EXIT_BUDGET.campaign_id
        assert resources['inventory_children'] == resources['exit_children'] == {}
        assert (await service.store.snapshot(service.store.binding)).budget == before.budget
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_verified_new_action_invalidates_old_image_without_releasing_proposals(tmp_path):
    from hummingbot.strategy_v2.life_liquidity.action_journal import QuoteActionRecord
    service, _ = await staged(tmp_path)
    try:
        image = await service.control.financial_configuration_service_source(**LIMITS)
        session = (await service.store.snapshot(service.store.binding)).session
        claim = QuoteActionRecord('retained-proposal', 'life', session.session_id, session.epoch, 1, 'LIFE-USDT', 'BUY', 0)
        await service.store._call(service.quotes.native.action_journal.claim_batch, (claim,))
        with pytest.raises(StorageError, match='SERVICE_HANDOFF_SOURCE_CHANGED'):
            await service.control.validate_financial_configuration_service_source(image, **LIMITS)
        assert await service.store._call(service.quotes.native.action_journal.active_records) == (claim,)
        assert not service.runtime._local_ready()
    finally:
        await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['missing_app', 'path', 'engine'])
async def test_configured_participant_identity_cannot_be_changed(tmp_path, monkeypatch, fault):
    service, _ = await staged(tmp_path)
    try:
        with monkeypatch.context() as patch:
            if fault == 'missing_app':
                patch.setattr(service, 'quotes', None)
            elif fault == 'path':
                patch.setattr(service.quotes.native.action_journal, 'path', tmp_path / 'elsewhere.json')
            else:
                patch.setattr(service.quotes.native.reference_engine, 'life_anchor_usdt', D('2'))
            with pytest.raises(StorageError, match='PARTICIPANT|SERVICE_QUOTE_POLICY_CHANGED'):
                await service.control.financial_configuration_service_source(**LIMITS)
        assert not service.runtime._local_ready()
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_cold_source_readback_preserves_image_and_does_not_rearm(tmp_path):
    from hummingbot.strategy_v2.life_liquidity.storage.service import FileLiquidityService
    service, _ = await staged(tmp_path)
    image = await service.control.financial_configuration_service_source(**LIMITS)
    args = dict(config=service.config, connectors=service.connectors, sources=service.sources,
                market_data_provider=service.controller.market_data_provider)
    await service.close()
    cold = await FileLiquidityService.open(**args)
    from test.hummingbot.strategy_v2.life_liquidity.storage.test_service_control import attach_empty_observer
    attach_empty_observer(cold)
    try:
        current = await cold.control.validate_financial_configuration_service_source(image, **LIMITS)
        assert current.content_digest == image.content_digest
        assert cold.store._cold_unarmed and not cold.runtime._local_ready()
        assert (await cold.store.snapshot(cold.store.binding)).session.config_version == 1
    finally:
        await cold.close()


@pytest.mark.asyncio
async def test_joint_hedge_carry_source_keeps_all_declared_native_origins(tmp_path):
    from test.hummingbot.strategy_v2.life_liquidity.storage.test_control_plane import facts
    from test.hummingbot.strategy_v2.life_liquidity.storage.test_service_control import control_policy, keys
    from test.hummingbot.strategy_v2.life_liquidity.storage.test_service_startup import joint_service

    from hummingbot.strategy_v2.life_liquidity.storage.contracts import encode
    from hummingbot.strategy_v2.life_liquidity.storage.control_plane import OperatorKeys
    existing = keys()
    signer = OperatorKeys(existing.keys, {key: roles | frozenset(('CONFIGURE',)) for key, roles in existing.roles.items()})

    def extra(config, sources):
        return {'control_seed': lambda b, o: encode(signer.seed(b, {'mode': 'ACTIVE', 'token': o.json()})),
                'joint_spot_recovery': lambda: ((), replace(facts(config.controller.storage_binding), observed_at=sources.clock()))}
    service, _, _, _, _ = await joint_service(tmp_path, carry=True, config_extra={'control': control_policy(signer)}, source_extra=extra)
    try:
        command, snapshot = await service.control.propose_financial_configuration({'session': {'duration': '2h'}}, operation_id='joint-source')
        await service.control.apply(command, approval=sign(service, signer, command, snapshot), snapshot=snapshot)
        before = await service.store.snapshot(service.store.binding)
        image = await service.control.financial_configuration_service_source(**LIMITS)
        assert set(dict(image.ancillary_images)) == {'shared_capital', 'requests', 'swap_wal', 'swap_actions', 'joint_recovery', 'hedge', 'carry'}
        assert image.recorder.cut == image.core.cut.checkpoint
        after = await service.store.snapshot(service.store.binding)
        assert after.balance == before.balance and after.budget == before.budget and after.session == before.session
        assert not service.runtime._local_ready() and not service.swap.sender._runtime_enabled
        from test.hummingbot.strategy_v2.life_liquidity.test_checkpoint_archive import capacity
        await service.store._call(service.joint._recovery.capital.journal.migrate_archive, capacity())
        with pytest.raises(StorageError, match='ARCHIVE_UNSUPPORTED'):
            await service.control.financial_configuration_service_source(**LIMITS)
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_cancelled_service_capture_retains_worker_until_close_and_never_publishes(tmp_path, monkeypatch):
    import asyncio
    import threading
    service, _ = await staged(tmp_path)
    entered, release = threading.Event(), threading.Event()
    task = closer = None
    try:
        repo = service.reporting.repository
        original = repo._handoff_source
        checkpoint = service.store._files.binding.read_bytes()

        def blocked(*args):
            entered.set()
            if not release.wait(5):
                raise AssertionError('test source worker not released')
            return original(*args)
        monkeypatch.setattr(repo, '_handoff_source', blocked)
        task = asyncio.create_task(service.control.financial_configuration_service_source(**LIMITS))
        assert await asyncio.to_thread(entered.wait, 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert service.store._pending and not service.store._uncertain
        closer = asyncio.create_task(service.close())
        await asyncio.sleep(0)
        assert not closer.done() and service.store._account_lock._fd is not None
        release.set()
        await closer
        assert service.store._files.binding.read_bytes() == checkpoint
        assert service.store._account_lock._fd is None
    finally:
        release.set()
        if task is not None and not task.done():
            await task
        if closer is not None:
            await closer
        await service.close()
