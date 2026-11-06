"""Observational faults stay visible; critical risk and ownership faults propagate."""
import asyncio
import copy
import json
import time
from test.hummingbot.cli.test_service_availability import host
from test.hummingbot.strategy_v2.test_mm_drawdown import runner
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.exc import OperationalError

from hummingbot.core.bot_ownership import OwnershipLost
from hummingbot.core.spot_outflow import SpotOutflowConfig, SpotOutflowProjection
from hummingbot.core.spot_outflow_guard import SpotOutflowGuard


def checkpoint():
    return dict(binding=dict(account_id='account', environment='demo', trading_pair='LIFE-USDT'),
                policy=dict(window_seconds=10, max_net_outflow_quote='100', fee_allowance_rate='.01', trading_pair='LIFE-USDT'),
                coverage_start_ms=0)


def observing(monkeypatch, recovery):
    item = host()
    item.bootstrapped, item.phase, item.applied_version = True, 'stopped', 0
    item.observer_progress_at = time.monotonic()
    item.owner = MagicMock(owner_uuid='owner', generation=1)
    item.owner.spot_outflow = None
    row = dict(control_version=0, config_type='controller', config_file_path='test.yml',
               desired_state='stopped', recovery_state=recovery, mm_session=None)
    item.owner.check_owner.return_value = row
    item.owner.store.publish.return_value = True
    item.stop_core = AsyncMock(return_value=True)
    item.poll_reconciliation = AsyncMock()
    item.poll_funding = item.poll_outflow = MagicMock()
    monkeypatch.setattr('hummingbot.core.order_submission_guard.install_guard', lambda owner: None)
    return item, row


@pytest.mark.parametrize('bad', [{}, {'policy': {}}, [], 'invalid-private-checkpoint', 0])
@pytest.mark.asyncio
async def test_stopped_invalid_outflow_checkpoint_publishes_diagnostic_and_can_recover(monkeypatch, bad):
    item, row = observing(monkeypatch, {'spot_outflow': copy.deepcopy(bad)})
    before = copy.deepcopy(row['recovery_state'])
    await item.observe_once()
    status = item.owner.store.publish.call_args.kwargs['status']
    assert status['spot_outflow'] is None
    incident = next(x for x in status['availability']['incidents'] if x['scope'] == 'spot_outflow_checkpoint')
    assert incident['reason_code'] == 'spot_outflow_checkpoint_invalid' and incident['operator_action_required']
    assert row['recovery_state'] == before and 'invalid-private' not in json.dumps(status)
    item.owner.require_recovery.assert_not_called()
    assert (await item.health(SimpleNamespace(path='/live'))).status == 200
    row['recovery_state']['spot_outflow'] = checkpoint()
    await item.observe_once()
    status = item.owner.store.publish.call_args.kwargs['status']
    assert status['spot_outflow']['reason_code'] == 'spot_outflow_not_running'
    assert not status['spot_outflow']['buy_allowed']
    assert not any(x['scope'] == 'spot_outflow_checkpoint' for x in status['availability']['incidents'])


@pytest.mark.asyncio
async def test_running_invalid_checkpoint_uses_existing_halt_without_resetting_data(monkeypatch):
    item, row = observing(monkeypatch, {'spot_outflow': {'policy': {}}, 'kept': {'unchanged': True}})
    row['desired_state'] = 'running'

    def halt(reason):
        row['recovery_state']['halt'] = True
        item.owner.allow_new_orders = False

    item.owner.require_recovery.side_effect = halt
    await item.observe_once()
    assert item.phase == 'halted' and not item.owner.allow_new_orders
    item.owner.require_recovery.assert_called_once_with('spot_outflow_checkpoint_invalid')
    assert row['recovery_state']['spot_outflow'] == {'policy': {}}
    assert row['recovery_state']['kept'] == {'unchanged': True}


def drawdown_host():
    item = host()
    strategy = runner()
    strategy.control_max_drawdown()
    item.hb.trading_core.strategy = strategy
    item.owner = SimpleNamespace(spot_outflow=None)
    return item, strategy


@pytest.mark.parametrize('failure', ['read', 'model'])
def test_drawdown_observation_fault_is_unavailable_and_recovers(failure):
    item, strategy = drawdown_host()
    real = strategy.mm_drawdown_status
    strategy.mm_drawdown_status = MagicMock(side_effect=RuntimeError('private-valuation-data')) if failure == 'read' else lambda: {'invalid': 'private-valuation-data'}
    status = item.snapshot()
    assert status['mm_drawdown'] is None
    incident = next(x for x in status['availability']['incidents'] if x['scope'] == 'drawdown_reporting')
    assert incident['reason_code'] == 'drawdown_observation_unavailable'
    assert 'private-valuation' not in json.dumps(status)
    strategy.mm_drawdown_status = real
    assert item.snapshot()['mm_drawdown']['ready']
    assert not any(x['scope'] == 'drawdown_reporting' for x in item.availability.snapshot()['incidents'])


@pytest.mark.parametrize('failure', ['read', 'model'])
def test_outflow_observation_fault_does_not_change_the_actual_guard(failure):
    item = host()
    owner = SimpleNamespace()
    guard = SpotOutflowGuard(owner, checkpoint()['policy'])
    guard.projection = SpotOutflowProjection(SpotOutflowConfig(window_seconds=10, max_net_outflow_quote='100', fee_allowance_rate='.01'),
                                             'LIFE-USDT', coverage_start_ms=0)
    guard.projection.advance(20000)
    guard.reason_code = None
    item.owner = SimpleNamespace(spot_outflow=guard)
    real = guard.status
    guard.status = MagicMock(side_effect=RuntimeError('private-outflow-data')) if failure == 'read' else lambda: {'invalid': True}
    status = item.snapshot()
    assert status['spot_outflow'] is None
    assert guard.projection is not None and guard.reason_code is None
    assert any(x['reason_code'] == 'spot_outflow_observation_unavailable' for x in status['availability']['incidents'])
    assert 'private-outflow' not in json.dumps(status)
    guard.status = real
    assert item.snapshot()['spot_outflow']['ready']


@pytest.mark.parametrize('component', ['drawdown', 'outflow'])
@pytest.mark.parametrize('error', [OwnershipLost('private-owner'), OperationalError('private-sql', {}, RuntimeError('private-db')),
                                   asyncio.CancelledError()])
def test_critical_and_cancellation_failures_still_escape_risk_projection(error, component):
    item, strategy = drawdown_host()
    if component == 'drawdown':
        strategy.mm_drawdown_status = MagicMock(side_effect=error)
    else:
        guard = SpotOutflowGuard(SimpleNamespace(), checkpoint()['policy'])
        guard.status = MagicMock(side_effect=error)
        item.owner.spot_outflow = guard
    with pytest.raises(type(error)):
        item.snapshot()


@pytest.mark.parametrize('bad', ['invalid-private-drawdown', {'mm_drawdown_policy': {}, 'mm_drawdown_breach': 'bad'}])
@pytest.mark.asyncio
async def test_stopped_invalid_drawdown_checkpoint_keeps_service_available(monkeypatch, bad):
    item, row = observing(monkeypatch, {'strategy_checkpoints': {'life_mm_profiles.yaml': bad}})
    before = copy.deepcopy(row['recovery_state'])
    await item.observe_once()
    assert row['recovery_state'] == before
    assert any(x['reason_code'] == 'drawdown_checkpoint_invalid' for x in item.availability.snapshot()['incidents'])
    assert (await item.health(SimpleNamespace(path='/live'))).status == 200


@pytest.mark.parametrize('field,value', [('binding', None), ('coverage_start_ms', None), ('coverage_start_ms', True),
                                         ('binding', {'account_id': '', 'environment': 'demo', 'trading_pair': 'LIFE-USDT'})])
@pytest.mark.asyncio
async def test_stored_outflow_checkpoint_requires_verified_binding_and_coverage(monkeypatch, field, value):
    raw = {**checkpoint(), field: value}
    item, row = observing(monkeypatch, {'spot_outflow': raw})
    await item.observe_once()
    assert any(x['reason_code'] == 'spot_outflow_checkpoint_invalid' for x in item.availability.snapshot()['incidents'])
    assert row['recovery_state']['spot_outflow'] == raw


@pytest.mark.asyncio
async def test_invalid_drawdown_checkpoint_does_not_log_false_recovery_between_polls(monkeypatch, caplog):
    item, _ = observing(monkeypatch, {'strategy_checkpoints': {'life_mm_profiles.yaml': {'mm_drawdown_policy': {}}}})
    await item.observe_once()
    first = next(x for x in item.availability.snapshot()['incidents'] if x['scope'] == 'drawdown_checkpoint')
    await item.observe_once()
    second = next(x for x in item.availability.snapshot()['incidents'] if x['scope'] == 'drawdown_checkpoint')
    assert first['first_seen_at'] == second['first_seen_at'] and second['retry_count'] == 2
    assert not any(r.__dict__.get('event') == 'service_recovered' and r.__dict__.get('scope') == 'drawdown_checkpoint'
                   for r in caplog.records)


@pytest.mark.parametrize('critical', [False, True])
@pytest.mark.asyncio
async def test_stop_finishes_cleanup_despite_optional_drawdown_view_failure(critical):
    item, strategy = drawdown_host()
    strategy.mm_drawdown_status = MagicMock(side_effect=RuntimeError('private-view'))
    item.owner = MagicMock()
    item.owner.shutdown_core = AsyncMock(side_effect=RuntimeError('critical-checkpoint') if critical else None,
                                         return_value=True)
    if critical:
        with pytest.raises(RuntimeError, match='critical-checkpoint'):
            await item.stop_core()
    else:
        assert await item.stop_core()
    item.owner.shutdown_core.assert_awaited_once_with(item.hb.trading_core)
    assert not item.running and not item.owner.allow_new_orders


@pytest.mark.asyncio
async def test_drawdown_dependency_fault_still_blocks_admission_while_status_remains_available():
    from decimal import Decimal
    from test.hummingbot.cli.test_mm_service import setup_host
    item, controllers, _ = await setup_host()
    item.mm_session.catalogue = item.mm_session.catalogue.model_copy(update={'max_global_drawdown_quote': Decimal('10')})
    strategy = runner()
    strategy.controllers = controllers
    strategy.executor_orchestrator.mm_valuation_ready = MagicMock(side_effect=RuntimeError('private-valuation'))
    item.hb.trading_core.strategy = strategy
    item.owner = SimpleNamespace(spot_outflow=None, volatility_checkpoints=None)
    assert not item.mm_runtime_ready()
    assert item.mm_readiness_reason == 'readiness_check_unavailable'
    status = item.snapshot()
    assert status['mm_drawdown'] is None
    assert all(not controller._session_admitted for controller in controllers.values())
