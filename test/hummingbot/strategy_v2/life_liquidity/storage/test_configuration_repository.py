"""Configuration projection is an audit record, never a financial permission."""
import asyncio
import json
import threading
from dataclasses import replace
from test.hummingbot.strategy_v2.life_liquidity.storage.test_quote_service import quote_service

import pytest
from sqlalchemy import event

from hummingbot.model.controllers import Controllers
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError


@pytest.mark.asyncio
async def test_service_startup_reports_bound_configuration_once_on_worker(tmp_path):
    service, _ = await quote_service(tmp_path)
    try:
        head = await service.reporting.repository.configuration(service.store.binding)
        assert head.sequence == head.active_revision == 1
        assert head.controller_json == service.controller.config.model_dump_json()
        assert not head.rejection_latched and not head.kill_latched
        rows = await service.store._call(lambda: service.reporting.repository._configuration_rows())
        assert len(rows) == 1 and rows[0]['config']['binding'] == service.store.binding.json()
        assert (await service.reload_config(service.config, operation_id='startup-config')).replayed
        assert (await service.reporting.repository.configuration(service.store.binding)).sequence == 1
        assert not service.store._wal.all_records()
    finally:
        await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('committed', [False, True])
async def test_lost_configuration_sql_reply_repairs_original_on_cold_start_without_rearm(tmp_path, monkeypatch, committed):
    from hummingbot.strategy_v2.life_liquidity.storage.service import FileLiquidityService
    service, _ = await quote_service(tmp_path)
    args = dict(config=service.config, connectors=service.connectors, sources=service.sources,
                market_data_provider=service.controller.market_data_provider)
    native = service.reporting.repository._recorder
    original = native.project_configuration_reports

    def fail(*values):
        if committed:
            original(*values)
        raise OSError('synthetic lost configuration reply')

    monkeypatch.setattr(native, 'project_configuration_reports', fail)
    candidate = service.config.model_copy(update={'controller': service.controller.config.model_copy(update={'manual_kill_switch': True})})
    try:
        with pytest.raises(StorageError):
            await service.reload_config(candidate, operation_id='kill-update')
        assert service.store._uncertain
        assert not service.runtime._local_ready()
    finally:
        try:
            await service.close()
        except StorageError:
            pass
    monkeypatch.setattr(native, 'project_configuration_reports', original)
    cold = await FileLiquidityService.open(**args)
    try:
        head = await cold.reporting.repository.configuration(cold.store.binding)
        assert head.sequence == 2 and head.active_revision == 2 and head.kill_latched
        assert cold.controller.config.manual_kill_switch
        assert cold.store._cold_unarmed and not cold.runtime._local_ready()
        assert await cold.reporting.repository.pending_configurations(cold.store.binding) == ()
        receipt = await cold.reload_config(candidate, operation_id='kill-update')
        assert receipt.replayed and not cold.runtime._local_ready()
    finally:
        await cold.close()


@pytest.mark.asyncio
async def test_configuration_sql_missing_rows_repair_but_conflicting_or_foreign_rows_fail(tmp_path):
    service, _ = await quote_service(tmp_path)
    repo = service.reporting.repository
    try:
        state = await repo.configuration(service.store.binding)

        def delete():
            with repo._recorder.sql_manager.get_new_session() as sql:
                sql.query(Controllers).filter(Controllers.id == repo._configuration_sql_id()).delete()
                sql.commit()
        await service.store._call(delete)
        with pytest.raises(StorageError):
            await repo.configuration(service.store.binding)
        await service.reporting.recover()
        assert (await repo.configuration(service.store.binding)).sequence == state.sequence

        def corrupt():
            with repo._recorder.sql_manager.get_new_session() as sql:
                row = sql.query(Controllers).filter(Controllers.id == repo._configuration_sql_id()).one()
                row.config = {'unsigned': 'replacement'}
                sql.commit()
        await service.store._call(corrupt)
        with pytest.raises(StorageError):
            await service.reporting.recover()
        assert not service.reporting.ready
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_reload_configuration_sql_and_history_never_run_on_event_loop(tmp_path):
    service, _ = await quote_service(tmp_path)
    loop_thread, calls = threading.get_ident(), []
    engine = service.reporting.repository._recorder.sql_manager.engine

    def before_sql(*_):
        calls.append(threading.get_ident())
        assert threading.get_ident() != loop_thread

    event.listen(engine, 'before_cursor_execute', before_sql)
    try:
        result = await service.reload_config(service.config, operation_id='same-config')
        assert result.reason_code == 'CONFIG_UNCHANGED'
        assert 'CONFIG_UNCHANGED' in ' '.join(service.controller.to_format_status())
        assert calls and set(calls) == {service.store._worker_thread_id}
        assert not service.store._wal.all_records()
    finally:
        event.remove(engine, 'before_cursor_execute', before_sql)
        await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('field', ['strategy', 'owner', 'recorder', 'policy', 'binding'])
async def test_unsupported_reload_preserves_configuration_money_and_sticky_cold_rejection(tmp_path, field):
    from hummingbot.strategy_v2.life_liquidity.storage.service import FileLiquidityService
    service, _ = await quote_service(tmp_path)
    original = service.config
    values = {}
    if field == 'strategy':
        strategy = original.controller.strategy.model_copy(update={'config_version': 2})
        values['controller'] = original.controller.model_copy(update={'strategy': strategy})
    elif field == 'owner':
        from uuid import UUID
        values['owner'] = replace(original.owner, generation=UUID(int=5617))
    elif field == 'recorder':
        values['recorder_config_name'] = 'different-service'
    elif field == 'policy':
        values['ack_timeout_s'] = 2
    else:
        values['controller'] = original.controller.model_copy(update={'storage_binding': replace(
            service.store.binding, account_uid='999999')})
    args = dict(config=original, connectors=service.connectors, sources=service.sources,
                market_data_provider=service.controller.market_data_provider)
    try:
        before = await service.store.snapshot(service.store.binding)
        rejected = await service.reload_config(original.model_copy(update=values), operation_id='invalid-' + field)
        assert rejected.reason_code in ('CONFIG_UPDATE_UNSUPPORTED', 'CONFIG_VALIDATION_FAILED')
        assert service.config == original and not service.configuration.ready
        assert service.controller.config_update_state.last_rejection is not None
        valid = await service.reload_config(original, operation_id='later-valid')
        assert valid.reason_code == 'CONFIG_UNCHANGED'
        assert not service.configuration.ready and not service.controller.config_update_state.order_permission()
        assert await service.plan() == [] and not service.store._wal.all_records()
        assert (await service.store.snapshot(service.store.binding)).balance == before.balance
    finally:
        await service.close()
    cold = await FileLiquidityService.open(**args)
    try:
        assert cold.configuration.state.rejection_latched
        assert cold.configuration.state.rejection_reason_code == rejected.reason_code
        assert rejected.reason_code in ' '.join(cold.status_lines())
        assert not cold.controller.config_update_state.order_permission()
        assert cold.store._cold_unarmed and not cold.runtime._local_ready()
    finally:
        await cold.close()


@pytest.mark.asyncio
async def test_manual_kill_false_and_historical_replay_never_resume_or_roll_back_latest_config(tmp_path):
    service, _ = await quote_service(tmp_path)
    original = service.config
    try:
        kill = original.model_copy(update={'controller': original.controller.model_copy(update={'manual_kill_switch': True})})
        first = await service.reload_config(kill, operation_id='kill')
        assert first.active_revision == 2 and service.controller.config.manual_kill_switch
        assert service._revoked and not service.runtime._local_ready()
        second = await service.reload_config(original, operation_id='clear-field-only')
        assert second.active_revision == 3 and not service.controller.config.manual_kill_switch
        assert service.configuration.state.kill_latched and not service.runtime._local_ready()
        replay = await service.reload_config(kill, operation_id='kill')
        assert replay.replayed and not service.controller.config.manual_kill_switch
        assert service.configuration.state.active_revision == 3 and not service.runtime._local_ready()
        assert not service.store._wal.all_records()
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_conflicting_operation_id_does_not_rewrite_native_reporting(tmp_path):
    service, _ = await quote_service(tmp_path)
    try:
        await service.reload_config(service.config, operation_id='same')
        kill = service.config.model_copy(update={'controller': service.controller.config.model_copy(update={'manual_kill_switch': True})})
        with pytest.raises(StorageError, match='CONFIG_OPERATION_CONFLICT'):
            await service.reload_config(kill, operation_id='same')
        assert (await service.reporting.repository.configuration(service.store.binding)).sequence == 2
        assert not service.runtime._local_ready()
    finally:
        with pytest.raises(StorageError, match='CONFIG_REPAIR_REQUIRED'):
            await service.close()


@pytest.mark.asyncio
async def test_different_invalid_candidates_cannot_reuse_an_operation_id(tmp_path):
    service, _ = await quote_service(tmp_path)
    try:
        first = await service.reload_config(service.config.model_copy(update={'max_pending': 0}), operation_id='invalid')
        assert first.reason_code == 'CONFIG_VALIDATION_FAILED'
        with pytest.raises(StorageError, match='CONFIG_OPERATION_CONFLICT'):
            await service.reload_config(service.config.model_copy(update={'max_pending': -1}), operation_id='invalid')
        assert not service.runtime._local_ready()
    finally:
        with pytest.raises(StorageError, match='CONFIG_REPAIR_REQUIRED'):
            await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['sequence', 'revision', 'changed-policy', 'reason'])
async def test_native_configuration_report_rejects_invalid_transition_before_checkpoint(tmp_path, fault):
    from hummingbot.strategy_v2.life_liquidity.storage.contracts import fingerprint
    service, _ = await quote_service(tmp_path)
    repo = service.reporting.repository
    try:
        _, history = await service.store._call(repo._configuration_history)
        command = replace(history[-1][0], operation_id='new-report', expected_sequence=1,
                          outcome='UNCHANGED', reason_code='CONFIG_UNCHANGED')
        if fault == 'sequence':
            command = replace(command, expected_sequence=0)
        elif fault == 'revision':
            command = replace(command, active_revision=2)
        elif fault == 'changed-policy':
            command = replace(command, policy_hash=fingerprint({'changed': True}))
        else:
            with pytest.raises(StorageError, match='CONFIG_REPORT_INVALID'):
                replace(command, reason_code='CONFIG_UPDATE_APPLIED')
            return
        cut = (await service.store.read(service.store.binding)).cut
        with pytest.raises(StorageError):
            await repo.report_configuration(command, cut=cut, owner=service.runtime.owner)
        assert (await service.store.read(service.store.binding)).cut == cut
        assert not service.store._uncertain
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_cancelled_reload_keeps_worker_and_account_lock_until_sql_finishes(tmp_path, monkeypatch):
    from hummingbot.strategy_v2.life_liquidity.account_lock import AccountLockUnavailable, AccountRiskPoolLock
    service, _ = await quote_service(tmp_path)
    entered, release = threading.Event(), threading.Event()
    native = service.reporting.repository._recorder
    original = native.project_configuration_reports

    def blocked(*args):
        entered.set()
        assert release.wait(5)
        original(*args)
    monkeypatch.setattr(native, 'project_configuration_reports', blocked)
    pending = service.reload_config(service.config, operation_id='blocked-sql')
    await asyncio.to_thread(entered.wait, 2)
    competitor = AccountRiskPoolLock(service.store.binding.account_uid, service.config.account_lock_root)
    try:
        assert entered.is_set() and service.admission_paused
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert service.configuration._pending and not service.runtime._local_ready()
        closing = asyncio.create_task(service.close())
        await asyncio.sleep(.02)
        assert not closing.done()
        with pytest.raises(AccountLockUnavailable):
            competitor.acquire()
    finally:
        release.set()
        await closing
        competitor.acquire()
        competitor.release()


@pytest.mark.asyncio
async def test_reload_capacity_does_not_admit_unbounded_reads_or_clear_unrecorded_rejection(tmp_path, monkeypatch):
    service, _ = await quote_service(tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()

    async def read(_):
        entered.set()
        await release.wait()
        return service.config
    monkeypatch.setattr(service, 'read_yaml', read)
    pending = service.reload_yaml(tmp_path / 'ignored.yml', operation_id='one')
    try:
        await entered.wait()
        with pytest.raises(StorageError, match='CONFIG_RELOAD_CLOSED_OR_BACKPRESSURE'):
            service.reload_yaml(tmp_path / 'ignored.yml', operation_id='two')
        assert len(service.configuration._pending) == 1
        release.set()
        await pending
        assert not service.configuration.ready and not service.runtime._local_ready()
        assert not service.controller.config_update_state.order_permission()
    finally:
        release.set()
        await service.close()


@pytest.mark.asyncio
async def test_joint_kill_reload_keeps_financial_namespace_and_cold_original_observation_policy(tmp_path):
    from test.hummingbot.strategy_v2.life_liquidity.storage.test_service_startup import joint_service

    from hummingbot.strategy_v2.life_liquidity.storage.contracts import thaw
    from hummingbot.strategy_v2.life_liquidity.storage.service import FileLiquidityService
    service, original, sources, spot, swap = await joint_service(tmp_path)
    provider = service.controller.market_data_provider
    try:
        before = thaw((await service.store.read(service.store.binding)).resources['joint_repository_policy'])
        kill = original.model_copy(update={'controller': original.controller.model_copy(update={'manual_kill_switch': True})})
        await service.reload_config(kill, operation_id='joint-kill')
        assert not service.swap.local_ready() and not service.runtime._local_ready()
        assert await service.store._call(service.joint._policy) == before
    finally:
        await service.close()
    cold = await FileLiquidityService.open(original, connectors={'okx': spot, 'okx_perpetual': swap}, sources=sources,
                                           market_data_provider=provider)
    try:
        assert cold.controller.config.manual_kill_switch
        assert cold.configuration.state.kill_latched and cold.store._cold_unarmed
        assert await cold.store._call(cold.joint._policy) == before
    finally:
        await cold.close()


@pytest.mark.asyncio
async def test_malformed_yaml_and_controller_hook_preserve_previous_and_do_not_store_invalid_payload(tmp_path):
    service, _ = await quote_service(tmp_path)
    path = tmp_path / 'bad.yml'
    path.write_text('bad: [synthetic-private-data')
    original = service.config
    try:
        receipt = await service.reload_yaml(path, operation_id='bad-yaml')
        assert receipt.reason_code == 'CONFIG_LOAD_FAILED' and service.config == original
        head = await service.reporting.repository.configuration(service.store.binding)
        assert head.rejection_latched
        rows = await service.store._call(service.reporting.repository._configuration_rows)
        assert 'synthetic-private-data' not in json.dumps(rows)
        result = await service.controller.update_config(service.controller.config.model_copy(deep=True))
        assert result.reason_code == 'CONFIG_UNCHANGED' and not service.configuration.ready
        failure = await service.controller.on_config_load_failure()
        assert failure.reason_code == 'CONFIG_LOAD_FAILED'
        assert not service.store._wal.all_records()
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_rejection_requires_later_valid_report_then_new_signed_activation(tmp_path):
    from datetime import timedelta
    from test.hummingbot.strategy_v2.life_liquidity.storage.test_service_control import (
        attach_empty_observer,
        control_policy,
        keys,
    )
    from uuid import UUID

    from hummingbot.strategy_v2.life_liquidity.storage.contracts import OwnerMode, encode
    from hummingbot.strategy_v2.life_liquidity.storage.control_plane import OwnerTransition
    signer = keys()
    service, _ = await quote_service(tmp_path, control_config=control_policy(signer),
                                     control_seed=lambda b, o: encode(signer.seed(b, {'mode': 'ACTIVE', 'token': o.json()})))
    attach_empty_observer(service)
    try:
        await service.reload_yaml(tmp_path / 'missing.yml', operation_id='invalid')
        await service.reload_config(service.config, operation_id='valid')
        assert not service.configuration.ready
        snapshot = await service.control.snapshot()
        token = replace(service.spot.owner, generation=UUID(int=5719))
        command = OwnerTransition('activate-config', OwnerMode.ACTIVE, token)
        approval = signer.sign('operator', service.store.binding, 'ACTIVATE', command.json(), snapshot.cut, snapshot.facts,
                               issued_at=service.sources.clock(), expires_at=service.sources.clock() + timedelta(seconds=1))
        await service.control.apply(command, approval=approval, snapshot=snapshot)
        assert service.configuration.ready and service.controller.config_update_state.order_permission()
        assert service.runtime._local_ready()
        await service.reload_config(service.config, operation_id='after-activation-noop')
        assert service.configuration.ready and service.runtime._local_ready()
        assert (await service.reload_config(service.config, operation_id='valid')).replayed
        assert service.configuration.ready and service.runtime._local_ready()
        await service.reload_yaml(tmp_path / 'missing.yml', operation_id='new-invalid')
        assert (await service.control.apply(command, approval=approval, snapshot=snapshot)).replayed
        assert not service.configuration.ready and not service.runtime._local_ready()
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_cold_start_honors_new_yaml_kill_and_stale_false_cannot_clear_it(tmp_path):
    from hummingbot.strategy_v2.life_liquidity.storage.service import FileLiquidityService
    service, _ = await quote_service(tmp_path)
    original = service.config
    args = dict(connectors=service.connectors, sources=service.sources, market_data_provider=service.controller.market_data_provider)
    await service.close()
    kill = original.model_copy(update={'controller': original.controller.model_copy(update={'manual_kill_switch': True})})
    for config in (kill, original):
        cold = await FileLiquidityService.open(config, **args)
        try:
            assert cold.controller.config.manual_kill_switch
            assert cold.configuration.state.kill_latched
            assert cold.configuration.state.sequence == cold.configuration.state.active_revision == 2
            assert cold.store._cold_unarmed and not cold.runtime._local_ready()
        finally:
            await cold.close()


@pytest.mark.asyncio
async def test_backend_neutral_configuration_report_cannot_be_published_by_file_adapter(tmp_path):
    from hummingbot.strategy_v2.life_liquidity.storage.recorder_configuration import ConfigurationReport
    service, _ = await quote_service(tmp_path)
    try:
        repo = service.reporting.repository
        config = service.controller.config.model_copy(update={'storage_backend': 'postgres'})
        head = await repo.configuration(service.store.binding)
        before = await service.store.read(service.store.binding)
        command = ConfigurationReport('wrong-backend', service.store.binding, config.id, head.sequence, head.active_revision,
                                      config.model_dump_json(), service.store.binding.policy_hash, 'e' * 64,
                                      'UNCHANGED', 'CONFIG_UNCHANGED', 1)
        with pytest.raises(StorageError, match='CONFIG_REPORT_BACKEND_INVALID'):
            await repo.report_configuration(command, cut=before.cut, owner=service.runtime.owner)
        assert (await repo.configuration(service.store.binding)).sequence == head.sequence
    finally:
        await service.close()
