"""Actual successor runtime inherits money and history, without activation."""
import hashlib
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from test.hummingbot.strategy_v2.life_liquidity.storage.test_handoff_authorization import freeze, setup
from uuid import UUID

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import OwnerMode, StorageError, encode
from hummingbot.strategy_v2.life_liquidity.storage.handoff import ConsumeConfigurationHandoff
from hummingbot.strategy_v2.life_liquidity.storage.service import FileLiquidityService
from hummingbot.strategy_v2.life_liquidity.storage.service_config import FileServiceConfig
from hummingbot.strategy_v2.life_liquidity.storage.target_import import ImportConfigurationTarget, TargetImportManifest


def target_config(service, namespace, record, root):
    from hummingbot.strategy_v2.life_liquidity.config import StrategyConfig
    pending = service.control.configuration_state.pending
    strategy = StrategyConfig.model_validate_json(pending.candidate_json)
    owner = replace(service.config.owner, process_id=UUID(int=9901), generation=UUID(int=9902),
                    session_id=UUID(record.session_id), epoch=record.epoch, valid_from=record.started_at, expires_at=record.expires_at)
    controller = service.config.controller.model_copy(update={'strategy': strategy, 'storage_binding': namespace,
                                                              'recovery_state_dir': None if service.config.controller.recovery_state_dir is None else str(root)})
    return FileServiceConfig.model_validate({**{key: getattr(service.config, key) for key in FileServiceConfig.model_fields},
                                            'controller': controller, 'owner': owner, 'state_dir': str(root),
                                             'config_version': strategy.config_version})


async def prepared(tmp_path, *, joint=False, before_stage=None, changes=None, quote_kwargs=None):
    if not joint:
        from hummingbot.strategy_v2.life_liquidity.storage.recovery import SpotFinancialRecoveryPolicy
        quote_kwargs = {'recovery_policy': SpotFinancialRecoveryPolicy(10000), **(quote_kwargs or {})}  # Offline disk/CI allowance.
    service, signer, namespace, target = await setup(tmp_path, joint=joint, before_stage=before_stage, changes=changes, quote_kwargs=quote_kwargs)
    try:
        grant, snapshot, _, _ = await freeze(service, signer, namespace, target)
        source = await service.store.frozen_configuration_source(service.store.binding)
        config = target_config(service, namespace, target, tmp_path / 'successor')
        manifest = TargetImportManifest('target-v2', grant.operation_id, source.content_digest, config.model_dump_json())
        assert await service.store.validate_configuration_target(manifest) == manifest
        view = await service.store.snapshot(service.store.binding)
        consume = ConsumeConfigurationHandoff('consume-v2', 1, grant.owner, grant.operation_id, manifest.content_digest)
        approval = signer.sign('operator', service.store.binding, 'HANDOFF', consume.json(), view.cut, snapshot.facts,
                               issued_at=service.sources.clock(), expires_at=service.sources.clock() + timedelta(seconds=1))
        await service.store.consume_configuration_handoff(consume, approval=approval, facts=snapshot.facts, cut=view.cut)
        # Synthetic time advances to the reserved successor's absolute start.
        service.store._clock = lambda: target.started_at
        sources = replace(service.sources, clock=lambda: target.started_at)
        facts = replace(snapshot.facts, observed_at=target.started_at)
        cut = (await service.store.snapshot(service.store.binding)).cut
        command = ImportConfigurationTarget(manifest, hashlib.sha256(service.store._path.read_bytes()).hexdigest())
        signature = signer.sign('operator', namespace, 'IMPORT', command.json(), cut, facts,
                                issued_at=target.started_at, expires_at=target.started_at + timedelta(seconds=1))
        return service, signer, namespace, target, config, command, signature, facts, cut, sources
    except BaseException:
        await service.close()
        raise


@pytest.mark.asyncio
async def test_target_runtime_uses_approved_revision_with_same_money_and_separate_activation(tmp_path):
    service, signer, namespace, target, config, command, signature, facts, cut, sources = await prepared(tmp_path)
    imported = None
    try:
        result = await service.store.import_configuration_target(command, approval=signature, facts=facts, cut=cut)
        assert result.state == 'TARGET_IMPORTED' and not result.replayed
        before = await service.store.snapshot(service.store.binding)
        await service.close()
        imported = await FileLiquidityService.open(config, connectors=service.connectors, sources=sources,
                                                   market_data_provider=service.controller.market_data_provider)
        after = await imported.store.snapshot(namespace)
        assert imported.controller.config.strategy == config.controller.strategy
        assert after.session.config_version == 2 and after.session.session_id == target.session_id
        assert after.session.expires_at == target.expires_at
        assert after.balance == before.balance
        assert after.budget.campaign_execution_loss_usdt == before.budget.campaign_execution_loss_usdt
        assert (await imported.store.current_owner(namespace)).mode is OwnerMode.RECOVERY_ONLY
        assert imported.store._cold_unarmed and not imported.runtime._local_ready()
        assert imported.configuration.state.controller_json == config.controller.model_dump_json()
        assert (Path(config.state_dir) / 'ancestry.json').is_file()
        assert encode((await imported.store.read(namespace)).resources['commands']) == encode(
            (await imported.store._call(imported.store._import_source_resources))['commands'])
    finally:
        if imported is not None:
            await imported.close()
        await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['signature', 'role', 'balance', 'scope', 'age', 'source', 'foreign_directory'])
async def test_invalid_target_import_creates_no_executable_target(tmp_path, fault):
    service, signer, namespace, target, config, command, signature, facts, cut, sources = await prepared(tmp_path)
    try:
        if fault == 'signature':
            signature = replace(signature, signature='0' * 128)
        elif fault == 'role':
            signature = signer.sign('operator', namespace, 'HANDOFF', command.json(), cut, facts,
                                    issued_at=target.started_at, expires_at=target.started_at + timedelta(seconds=1))
        elif fault == 'balance':
            from decimal import Decimal
            facts = replace(facts, account_quote_usdt=Decimal('999'))
            signature = signer.sign('operator', namespace, 'IMPORT', command.json(), cut, facts,
                                    issued_at=target.started_at, expires_at=target.started_at + timedelta(seconds=1))
        elif fault == 'scope':
            facts = replace(facts, runner_scope_complete=False)
            signature = signer.sign('operator', namespace, 'IMPORT', command.json(), cut, facts,
                                    issued_at=target.started_at, expires_at=target.started_at + timedelta(seconds=1))
        elif fault == 'age':
            service.store._clock = lambda: target.started_at + timedelta(seconds=2)
        elif fault == 'source':
            command = replace(command, source_root_sha256='a' * 64)
        else:
            Path(config.state_dir).mkdir()
            (Path(config.state_dir) / 'foreign.txt').write_text('not an import')
        source_bytes = service.store._path.read_bytes()
        with pytest.raises(StorageError):
            await service.store.import_configuration_target(command, approval=signature, facts=facts, cut=cut)
        assert service.store._path.read_bytes() == source_bytes
        assert not (Path(config.state_dir) / 'binding.json').exists()
        assert (await service.store.configuration_handoff(service.store.binding)).consumed is not None
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_interrupted_target_files_resume_with_fresh_signature_after_old_approval_expires(tmp_path, monkeypatch):
    from hummingbot.strategy_v2.life_liquidity.storage import target_import
    service, signer, namespace, target, config, command, signature, facts, cut, sources = await prepared(tmp_path)
    try:
        original = target_import.publish_exact

        def interrupted(path, raw, **kwargs):
            if path.name == 'reservations.json':
                raise StorageError('SYNTHETIC_IMPORT_INTERRUPTION')
            return original(path, raw, **kwargs)
        with monkeypatch.context() as patch:
            patch.setattr(target_import, 'publish_exact', interrupted)
            with pytest.raises(StorageError, match='SYNTHETIC_IMPORT_INTERRUPTION'):
                await service.store.import_configuration_target(command, approval=signature, facts=facts, cut=cut)
        assert (Path(config.state_dir) / 'ancestry.json').is_file()
        assert not (Path(config.state_dir) / 'binding.json').exists()
        now = target.started_at + timedelta(seconds=2)
        service.store._clock = lambda: now
        facts = replace(facts, observed_at=now)
        fresh = signer.sign('operator', namespace, 'IMPORT', command.json(), cut, facts, issued_at=now, expires_at=now + timedelta(seconds=1))
        result = await service.store.import_configuration_target(command, approval=fresh, facts=facts, cut=cut)
        assert result.state == 'TARGET_IMPORTED'
        await service.close()
        cold = await FileLiquidityService.open(config, connectors=service.connectors, sources=replace(sources, clock=lambda: now),
                                               market_data_provider=service.controller.market_data_provider)
        try:
            assert cold.controller.config.strategy.config_version == 2 and cold.store._cold_unarmed
        finally:
            await cold.close()
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_joint_hedge_carry_target_preserves_native_state_under_new_policy_and_stays_unarmed(tmp_path):
    import json
    service, signer, namespace, target, config, command, signature, facts, cut, sources = await prepared(tmp_path, joint=True)
    cold = None
    try:
        source = await service.store.frozen_configuration_source(service.store.binding)
        await service.store.import_configuration_target(command, approval=signature, facts=facts, cut=cut)
        await service.close()

        cold = await FileLiquidityService.open(config, connectors=service.connectors, sources=sources,
                                               market_data_provider=service.controller.market_data_provider)
        assert cold.swap is not None and cold.hedge is not None and cold.products is not None
        assert cold.controller.config.strategy.config_version == 2 and cold.store._cold_unarmed
        assert not cold.joint._recovery._armed and not cold.swap.sender._runtime_enabled
        names = {'hedge': 'hedge_coordinator.json'}
        for name, raw in source.ancillary_images:
            before = json.loads(raw)
            after = json.loads((Path(config.state_dir) / names.get(name, name + '.json')).read_bytes())
            if 'state' in before:
                assert after['state'] == before['state'], name
            else:
                assert after == before, name
        assert await cold.plan() == []
    finally:
        if cold is not None:
            await cold.close()
        await service.close()


async def paid_history(service):
    from decimal import Decimal as D
    from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_fill_contracts import (
        command as filled,
        fill,
        prepare_arm,
    )
    from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_financial_recovery import facts as account_facts
    from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_terminal_contracts import (
        facts as terminal_facts,
        terminal,
    )
    from test.hummingbot.strategy_v2.life_liquidity.test_executor_protected_send import _setup
    from types import SimpleNamespace

    from hummingbot.strategy_v2.life_liquidity.action_journal import QuoteActionRecord
    from hummingbot.strategy_v2.life_liquidity.storage.control_plane import ControlSnapshot
    from hummingbot.strategy_v2.life_liquidity.storage.recorder import ExecutorProjection
    from hummingbot.strategy_v2.models.base import RunnableStatus
    store, binding, owner = service.store, service.store.binding, service.spot.owner
    actions = service.quotes.native.action_journal
    claim = QuoteActionRecord('intent1', 'life', str(owner.session_id), owner.epoch, 1, 'LIFE-USDT', 'BUY', 0)
    await store._call(actions.claim_batch, (claim,))
    await prepare_arm(store, binding, owner)
    await store.apply_fills(filled(owner), cut=(await store.snapshot(binding)).cut, owner=owner)
    observed = terminal_facts(binding, cumulative_base_life=D('1'), fills=(fill(),), account_trade_ids=('trade1',),
                              account_base_life=D('6'), account_quote_usdt=D('8.98'))
    await store.confirm_terminal(terminal(binding, owner, observed), cut=(await store.snapshot(binding)).cut, owner=owner)
    await store._call(lambda: actions.transition('intent1', expected='PROPOSED', state='RECONCILED'))
    _, executor, _, _, _, _ = _setup(Path(service.config.state_dir).parent / 'reporting-native', recovery_account_uid=binding.account_uid)
    executor.config = executor.config.model_copy(update={'id': 'intent1', 'controller_id': 'life', 'timestamp': 1.0})
    info = executor.executor_info.model_copy(update={'id': 'intent1', 'controller_id': 'life', 'status': RunnableStatus.TERMINATED,
                                                     'is_active': False, 'is_trading': False, 'close_timestamp': 1.1,
                                                     'custom_info': {**executor.executor_info.custom_info, 'recovery_order_ids': ['wire1']}})
    projection = ExecutorProjection.from_info('paid-history-report', binding, info)
    await service.reporting.repository.project(projection, (await store.read(binding)).cut, owner)
    from hummingbot.core.data_type.common import OrderType, TradeType
    from hummingbot.core.data_type.trade_fee import AddedToCostTradeFee, TokenAmount
    from hummingbot.core.event.events import BuyOrderCreatedEvent, MarketEvent, OrderCancelledEvent, OrderFilledEvent
    from hummingbot.strategy_v2.life_liquidity.storage.recorder_events import EventProjection, EventSource
    native = service.reporting.repository._recorder
    source = EventSource('life', native.config_file_path, native.strategy_name, 'okx')
    for tag, event in (
        (MarketEvent.BuyOrderCreated, BuyOrderCreatedEvent(1, OrderType.LIMIT_MAKER, 'LIFE-USDT', D('2'), D('1'), 'wire1', 1, 'exchange1')),
        (MarketEvent.OrderFilled, OrderFilledEvent(1.1, 'wire1', 'LIFE-USDT', TradeType.BUY, OrderType.LIMIT_MAKER, D('1'), D('1'),
                                                   AddedToCostTradeFee(flat_fees=[TokenAmount('USDT', D('-.02'))]), 'trade1', 'exchange1')),
        (MarketEvent.OrderCancelled, OrderCancelledEvent(1.2, 'wire1')),
    ):
        event_command = EventProjection.capture(binding, source, tag.value, event, {})
        await service.reporting.repository.project_event(event_command, cut=(await store.read(binding)).cut, owner=owner)
    from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_capital_contracts import nav
    await store.observe_nav(nav(), cut=(await store.snapshot(binding)).cut, owner=owner)

    async def independently_collected():
        cut = (await store.snapshot(binding)).cut
        facts = account_facts(binding, accepted_wire_ids=(), runner_wire_ids=(), observed_at=service.sources.clock())
        return ControlSnapshot(binding, cut, facts)
    service.spot._observer = SimpleNamespace(control_snapshot=independently_collected)


@pytest.mark.asyncio
async def test_target_retains_actual_paid_loss_economic_hold_trade_wire_and_sql_history(tmp_path):
    from decimal import Decimal as D
    service, signer, namespace, target, config, command, signature, facts, cut, sources = await prepared(tmp_path, before_stage=paid_history)
    cold = None
    try:
        before = await service.store.snapshot(service.store.binding)
        capital = (await service.store.read(service.store.binding)).resources['nav_operations']['nav1']['measurement']
        assert before.budget.campaign_execution_loss_usdt == D('.12')
        assert before.budget.campaign_subsidy_committed_usdt == D('.12')
        await service.store.import_configuration_target(command, approval=signature, facts=facts, cut=cut)
        await service.close()
        cold = await FileLiquidityService.open(config, connectors=service.connectors, sources=sources,
                                               market_data_provider=service.controller.market_data_provider)
        after = await cold.store.snapshot(namespace)
        assert after.balance == before.balance and after.balance.total_quote_usdt == D('8.98')
        assert after.budget.campaign_execution_loss_usdt == D('.12')
        assert after.budget.campaign_subsidy_committed_usdt == D('.12')
        inherited_capital = await cold.store.capital(namespace)
        assert inherited_capital.highwater_usdt == D(capital['highwater_usdt'])
        assert inherited_capital.adjusted_nav_usdt == D(capital['adjusted_nav_usdt'])
        assert (await cold.store.attempt('intent1', namespace)).client_order_id == 'wire1'
        assert 'trade1' in (await cold.store.read(namespace)).resources['fills']
        rows = await cold.reporting.repository.read(namespace)
        assert len(rows) == 1 and rows[0].executor_id == 'intent1'
        from hummingbot.strategy_v2.life_liquidity.storage.contracts import thaw
        for key, row in (await cold.store.read(namespace)).resources['recorder_events'].items():
            origin = await cold.reporting.repository.event_origin(key, namespace)
            assert origin.command.binding.json() == thaw(row['command']['binding'])
        assert not cold.runtime._local_ready()
    finally:
        if cold is not None:
            await cold.close()
        await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('route', ['risk_probes', 'inventory_exit'])
async def test_optional_product_policy_handoff_keeps_original_state_and_deadlines(tmp_path, route):
    import json
    from datetime import timedelta
    from decimal import Decimal as D
    from test.hummingbot.strategy_v2.life_liquidity.storage.test_risk_service import policy, sources
    _, callbacks = sources()
    if route == 'risk_probes':
        from test.hummingbot.strategy_v2.life_liquidity.storage.test_markout_probe_service import markout_policy

        from hummingbot.strategy_v2.life_liquidity.config import ResumePolicyConfig
        callbacks['horizon_observation'] = lambda *_: None
        options = {'risk_config': policy(stable_data_ms=2000, markout=markout_policy(), recovery_probe_campaign_base=D('.2')),
                   'risk_sources': callbacks, 'resume_policy': ResumePolicyConfig(stable_data_duration='2s', probe_size_base='.1')}
    else:
        from test.hummingbot.strategy_v2.life_liquidity.storage.test_exit_budget_repository import EXIT_BUDGET
        from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_financial_contracts import NOW

        from hummingbot.strategy_v2.life_liquidity.inventory_execution import InventoryExecutionPolicy
        from hummingbot.strategy_v2.life_liquidity.storage.service_config import (
            ExitServiceConfig,
            InventoryServiceConfig,
        )
        p = InventoryExecutionPolicy('SELL', D('2'), D('1'), NOW + timedelta(minutes=1), D('1'), D('3'),
                                     D('500'), D('.1'), D('1'), D('.1'), D('1'), D('.01'), D('.1'), D('.1'))
        callbacks.update(inventory_observation=lambda: None, exit_observation=lambda: None)
        options = {'risk_config': policy(), 'risk_sources': callbacks,
                   'inventory_config': InventoryServiceConfig(campaign_id='import-campaign', started_at=NOW, policy=p, max_age_ms=1000),
                   'exit_config': ExitServiceConfig(target_base=D('3'), max_child_base=D('1'), max_slippage_bps=D('150'), max_age_ms=1000, budget=EXIT_BUDGET)}
    service, signer, namespace, target, config, command, signature, facts, cut, source_callbacks = await prepared(tmp_path, quote_kwargs=options)
    cold = None
    try:
        source = await service.store.frozen_configuration_source(service.store.binding)
        await service.store.import_configuration_target(command, approval=signature, facts=facts, cut=cut)
        await service.close()
        cold = await FileLiquidityService.open(config, connectors=service.connectors, sources=source_callbacks,
                                               market_data_provider=service.controller.market_data_provider)
        assert not cold.runtime._local_ready() and cold.controller.config.strategy.config_version == 2
        for name, raw in source.ancillary_images:
            assert json.loads((Path(config.state_dir) / (name + '.json')).read_bytes()).get('state') == json.loads(raw).get('state')
        assert (await cold.store.snapshot(namespace)).balance.total_quote_usdt == facts.account_quote_usdt
        assert await cold.plan() == []
        if route == 'inventory_exit':
            assert cold.config.inventory.policy.deadline_utc == service.config.inventory.policy.deadline_utc
            assert (Path(config.state_dir) / 'exit_budget.json').read_bytes() == dict(source.core.journal_images)['exit_subsidy']
    finally:
        if cold is not None:
            await cold.close()
        await service.close()


@pytest.mark.asyncio
async def test_separate_target_activation_quotes_with_approved_sizes_and_recovery_never_rearms(tmp_path):
    from decimal import Decimal as D
    from test.hummingbot.strategy_v2.life_liquidity.storage.test_service_control import attach_empty_observer

    from hummingbot.strategy_v2.life_liquidity.storage.control_plane import OwnerTransition
    changes = {'session': {'duration': '2h'}, 'reference': {'lookback': '30m'},
               'quotes': {'spreads_bps': ['200'], 'sizes_base': ['2']}}
    service, signer, namespace, target, config, command, signature, facts, cut, sources = await prepared(tmp_path, changes=changes)
    cold = None
    try:
        observed = service.sources.quote_snapshot()
        snapshot = replace(observed, session_id=target.session_id, epoch=target.epoch, config_version=2)
        sources = replace(sources, quote_snapshot=lambda: snapshot)
        await service.store.import_configuration_target(command, approval=signature, facts=facts, cut=cut)
        await service.close()
        cold = await FileLiquidityService.open(config, connectors=service.connectors, sources=sources,
                                               market_data_provider=service.controller.market_data_provider)
        assert await cold.plan() == []
        c = cold.controller
        c.listing_gate.instrument_found, c.listing_gate.instrument_state = True, 'live'
        c.listing_gate.instrument_rules = snapshot.rules
        c.continuous_gate.ready, c.continuous_gate.exchange_time_ms = True, 1100
        # Explicit synthetic book/reference readiness; real native quote/fee/risk checks remain active.
        from test.hummingbot.strategy_v2.life_liquidity.test_own_depth import book
        market = book(bids=[['0.98', '20']], asks=[['1.02', '20']])
        c.snapshot_gate.clock, c.snapshot_gate.ready, c.snapshot_gate.snapshot = lambda: 2.05, True, market
        c.trading_permissions_ready = lambda: True
        c.allow_create_executor_actions = lambda: True
        attach_empty_observer(cold)
        current = await cold.control.snapshot()
        owner = replace(config.owner, generation=UUID(int=9903))
        activation = OwnerTransition('activate-imported', OwnerMode.ACTIVE, owner)
        approval = signer.sign('operator', namespace, 'ACTIVATE', activation.json(), current.cut, current.facts,
                               issued_at=target.started_at, expires_at=target.started_at + timedelta(seconds=1))
        await cold.control.apply(activation, approval=approval, snapshot=current)
        assert cold.runtime._local_ready()
        actions = await cold.plan()
        assert len(actions) == 1, c._quote_action_planner.reason_code
        assert actions[0].executor_config.amount == D('2')
        assert actions[0].executor_config.price == D('.98')
        assert c.config.strategy.reference.lookback == '30m'
        current = await cold.control.snapshot()
        revoke = OwnerTransition('revoke-imported', OwnerMode.UNARMED, None)
        approval = signer.sign('operator', namespace, 'REVOKE', revoke.json(), current.cut, current.facts,
                               issued_at=target.started_at, expires_at=target.started_at + timedelta(seconds=1))
        await cold.control.apply(revoke, approval=approval, snapshot=current)
        current = await cold.control.snapshot()
        recover = OwnerTransition('recover-imported', OwnerMode.RECOVERY_ONLY, owner)
        approval = signer.sign('operator', namespace, 'RECOVER', recover.json(), current.cut, current.facts,
                               issued_at=target.started_at, expires_at=target.started_at + timedelta(seconds=1))
        await cold.control.apply(recover, approval=approval, snapshot=current)
        assert (await cold.store.current_owner(namespace)).mode is OwnerMode.RECOVERY_ONLY
        assert await cold.plan() == [] and not cold.runtime._local_ready()
    finally:
        if cold is not None:
            await cold.close()
        await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['ancestry', 'qualification', 'retired_source', 'sql'])
async def test_cold_target_denies_missing_or_tampered_import_origins(tmp_path, fault):
    import json
    import sqlite3
    service, signer, namespace, target, config, command, signature, facts, cut, sources = await prepared(tmp_path)
    await service.store.import_configuration_target(command, approval=signature, facts=facts, cut=cut)
    source_path = service.store._path
    await service.close()
    root = Path(config.state_dir)
    if fault == 'ancestry':
        (root / 'ancestry.json').write_bytes(b'{}')
    elif fault == 'qualification':
        next(root.glob('qualification-*.json')).unlink()
    elif fault == 'retired_source':
        source_path.unlink()
    else:
        with sqlite3.connect(config.recorder_path) as connection:
            connection.execute('DELETE FROM Controllers')
    with pytest.raises(StorageError):
        await FileLiquidityService.open(config, connectors=service.connectors, sources=sources,
                                        market_data_provider=service.controller.market_data_provider)
    state = json.loads((root / 'binding.json').read_bytes())['state']
    assert state['ownership']['mode'] == 'RECOVERY_ONLY'


@pytest.mark.asyncio
@pytest.mark.parametrize('committed', [False, True])
async def test_unknown_target_commit_reply_requires_cold_source_readback(tmp_path, monkeypatch, committed):
    from test.hummingbot.strategy_v2.life_liquidity.storage.test_handoff_authorization import maintenance

    from hummingbot.strategy_v2.life_liquidity.storage import target_import
    from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown
    service, signer, namespace, target, config, command, signature, facts, cut, sources = await prepared(tmp_path)
    original = target_import.publish_exact

    def lost(path, raw, **kwargs):
        if path.name == 'binding.json':
            if committed:
                original(path, raw, **kwargs)
            raise OSError('synthetic lost target publication reply')
        return original(path, raw, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(target_import, 'publish_exact', lost)
        with pytest.raises(CommitUnknown):
            await service.store.import_configuration_target(command, approval=signature, facts=facts, cut=cut)
    assert service.store._uncertain
    await service.close()
    # Maintenance uses the configured source clock at successor start.
    service.sources = sources
    cold = await maintenance(service, signer)
    try:
        receipt = await cold.import_configuration_target(command, approval=signature, facts=facts, cut=cut)
        assert receipt.replayed is committed and cold._cold_unarmed and cold._handoff_frozen
    finally:
        await cold.close()


@pytest.mark.asyncio
async def test_expiry_during_marker_fsync_publishes_no_target(tmp_path, monkeypatch):
    from hummingbot.strategy_v2.life_liquidity.storage import target_import
    service, signer, namespace, target, config, command, signature, facts, cut, sources = await prepared(tmp_path)
    original = target_import.publish_exact

    def delayed(path, raw, **kwargs):
        if path.name == 'binding.json':
            check = kwargs['before_link']

            def after_fsync():
                service.store._clock = lambda: target.started_at + timedelta(seconds=2)
                check()
            kwargs['before_link'] = after_fsync
        return original(path, raw, **kwargs)
    try:
        monkeypatch.setattr(target_import, 'publish_exact', delayed)
        with pytest.raises(StorageError):
            await service.store.import_configuration_target(command, approval=signature, facts=facts, cut=cut)
        assert not (Path(config.state_dir) / 'binding.json').exists()
        assert not service.store._uncertain
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_position_reporting_continues_after_import_without_rewriting_ancestry(tmp_path):
    from test.hummingbot.strategy_v2.life_liquidity.storage.test_recorder_positions import report

    async def history(service):
        from decimal import Decimal
        service.controller.market_data_provider.get_price_by_type.return_value = Decimal('1')
        cmd = report(service.store.binding)
        await service.reporting.repository.report_positions(cmd, cut=(await service.store.read(cmd.binding)).cut,
                                                            owner=service.config.owner)
    service, signer, namespace, target, config, command, signature, facts, cut, sources = await prepared(tmp_path, before_stage=history)
    cold = None
    try:
        await service.store.import_configuration_target(command, approval=signature, facts=facts, cut=cut)
        await service.close()
        ancestry = (Path(config.state_dir) / 'ancestry.json').read_bytes()
        cold = await FileLiquidityService.open(config, connectors=service.connectors, sources=sources,
                                               market_data_provider=service.controller.market_data_provider)
        repo = cold.reporting.repository
        before = await repo.positions(namespace)
        cmd = report(namespace, 'target-positions', before.sequence)
        await repo.report_positions(cmd, cut=(await cold.store.read(namespace)).cut, owner=config.owner)
        assert (await repo.positions(namespace)).positions == cmd.positions
        assert (Path(config.state_dir) / 'ancestry.json').read_bytes() == ancestry
        assert (await cold.store.read(namespace)).resources['recorder_positions']['positions-1']['command']['binding'] != namespace.json()
        assert await cold.plan() == []
    finally:
        if cold is not None:
            await cold.close()
        await service.close()


@pytest.mark.asyncio
async def test_cancelled_target_import_retains_account_lock_until_physical_publication_finishes(tmp_path, monkeypatch):
    import asyncio
    import threading
    from test.hummingbot.strategy_v2.life_liquidity.storage.test_handoff_authorization import maintenance

    from hummingbot.strategy_v2.life_liquidity.account_lock import AccountLockUnavailable
    from hummingbot.strategy_v2.life_liquidity.storage import target_import
    service, signer, namespace, target, config, command, signature, facts, cut, sources = await prepared(tmp_path)
    original = target_import.publish_exact
    entered, release = threading.Event(), threading.Event()

    def blocked(path, raw, **kwargs):
        if path.name == 'binding.json':
            entered.set()
            if not release.wait(10):
                raise AssertionError('synthetic target import not released')
        return original(path, raw, **kwargs)
    monkeypatch.setattr(target_import, 'publish_exact', blocked)
    task = asyncio.create_task(service.store.import_configuration_target(command, approval=signature, facts=facts, cut=cut))
    closer = None
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert service.store._uncertain
        closer = asyncio.create_task(service.close())
        await asyncio.sleep(0)
        assert not closer.done() and service.store._account_lock._fd is not None
        with pytest.raises(AccountLockUnavailable):
            await maintenance(service, signer)
        release.set()
        await closer
        service.sources = sources
        cold = await maintenance(service, signer)
        try:
            assert (await cold.import_configuration_target(command, approval=signature, facts=facts, cut=cut)).replayed
            assert cold._cold_unarmed
        finally:
            await cold.close()
    finally:
        release.set()
        if closer is not None:
            await closer
        await service.close()
