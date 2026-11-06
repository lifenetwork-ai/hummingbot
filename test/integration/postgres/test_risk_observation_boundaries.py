"""Malformed checkpoint diagnostics preserve durable data and the real send fence."""
import asyncio
from test.integration.postgres.test_ownership import owned as _owned
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import text

from hummingbot.cli.service import ServiceHost
from hummingbot.client.config.runtime_config import RuntimeSettings
from hummingbot.core.bot_ownership import OwnershipLost
from hummingbot.core.order_submission_guard import SubmissionBlocked

owned = _owned


def service(context):
    item = ServiceHost(RuntimeSettings.from_environment({'DATABASE_URL': 'postgresql://unused/test'}),
                       asyncio.Event(), MagicMock())
    item.owner, item.store = context.owner, context.store
    item.phase, item.bootstrapped = 'stopped', True
    item.applied_version = context.store.read()['control_version']
    item.hb = SimpleNamespace(trading_core=SimpleNamespace(
        strategy=None, connector_manager=SimpleNamespace(connectors={}), _is_running=False, _strategy_running=False,
        get_status=lambda: dict(clock_running=False, strategy_running=False)))
    item.poll_reconciliation = AsyncMock()
    item.poll_funding = item.poll_outflow = MagicMock()
    return item


@pytest.mark.parametrize('kind', ['outflow', 'drawdown'])
@pytest.mark.parametrize('running', [False, True])
def test_real_checkpoint_projection_preserves_blob_and_running_halt_blocks_sends(owned, kind, running):
    if not running:
        owned.store.control(expected_version=1, desired_state='stopped')
    raw = {'spot_outflow': {'policy': {}}} if kind == 'outflow' else {
        'strategy_checkpoints': {'life_mm_profiles.yaml': 'invalid-private-checkpoint'}}
    owned.owner._mutate_recovery(lambda state: state.update(**raw, kept_marker={'untouched': True}))
    item = service(owned)
    owned.loop.run_until_complete(item.observe_once())
    row = owned.store.read()
    for key, value in raw.items():
        assert row['recovery_state'][key] == value
    assert row['recovery_state']['kept_marker'] == {'untouched': True}
    assert row['acknowledged_version'] == row['control_version']
    reason = 'spot_outflow_checkpoint_invalid' if kind == 'outflow' else 'drawdown_checkpoint_invalid'
    assert any(x['reason_code'] == reason for x in row['status']['availability']['incidents'])
    assert 'invalid-private-checkpoint' not in str(row['status'])
    if running:
        assert row['recovery_state']['halt'] and row['recovery_state']['reason'] == reason
        assert item.phase == 'halted' and not owned.owner.allow_new_orders
        with pytest.raises(SubmissionBlocked):
            owned.owner.check_owner(new_risk=True)
    else:
        assert not row['recovery_state'].get('halt')
        assert item.phase == 'stopped'
    assert owned.loop.run_until_complete(item.health(SimpleNamespace(path='/live'))).status == 200


def test_failed_required_halt_write_still_loses_authority_and_propagates(owned):
    owned.owner._mutate_recovery(lambda state: state.update(spot_outflow={'policy': {}}))
    with owned.engine.begin() as connection:
        connection.execute(text("ALTER TABLE bot_state ADD CONSTRAINT reject_test_halt CHECK (NOT COALESCE((recovery_state->>'halt')::boolean,false))"))
    item = service(owned)
    with pytest.raises(OwnershipLost):
        owned.loop.run_until_complete(item.observe_once())
    assert owned.owner.lost and not owned.owner.allow_new_orders
    row = owned.store.read()
    assert row['recovery_state']['spot_outflow'] == {'policy': {}}
    assert not row['recovery_state'].get('halt')
