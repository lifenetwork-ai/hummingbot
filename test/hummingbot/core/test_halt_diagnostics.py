"""Public HALT explanations are bounded facts, not arbitrary saved error text."""
import json
import logging
from contextlib import nullcontext
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.exc import OperationalError

from hummingbot.core.bot_ownership import BotOwnership, OwnershipLost


def test_runtime_halt_retains_original_cause_when_cleanup_also_requires_review(monkeypatch):
    owner = BotOwnership(MagicMock(), 'offline')
    state = {}
    owner._mutate_recovery = lambda change: change(state)
    monkeypatch.setenv('K_REVISION', 'revision-17')
    owner.require_recovery('A required runtime task failed; inspect service availability status.',
                           runtime_reason='connector_task_stopped', component='okx_perpetual',
                           task='_user_stream_tracker_task', task_state='completed')
    first = dict(state['halt_diagnostic'])
    owner.require_recovery('A fill arrived during position checkpoint cleanup; operator reconciliation is required.')
    assert state['halt_diagnostic'] == first
    assert state['reason'] == 'A fill arrived during position checkpoint cleanup; operator reconciliation is required.'
    assert first['reason_code'] == 'runtime_task_failed'
    assert first['runtime_reason_code'] == 'connector_task_stopped'
    assert first['revision'] == 'revision-17'
    assert first['occurred_at'] > 0
    assert not owner.allow_new_orders
    from hummingbot.core.halt_diagnostics import halt_cause
    assert halt_cause(state)['reason_code'] == 'runtime_task_failed'
    assert halt_cause(state)['current_reason_code'] == 'cleanup_fill_reconciliation'


def test_later_risk_reason_remains_authoritative_for_recovery():
    from hummingbot.core.halt_diagnostics import RUNTIME_REASON, halt_cause, record_halt
    from hummingbot.core.recovery import recovery_blockers
    recovery = {}
    record_halt(recovery, RUNTIME_REASON)
    record_halt(recovery, 'drawdown_global_limit')
    state = dict(desired_state='stopped', control_version=1, acknowledged_version=1, recovery_state=recovery)
    assert 'recovery_risk_stop' in recovery_blockers(state)
    assert halt_cause(recovery)['reason_code'] == 'runtime_task_failed'
    assert halt_cause(recovery)['current_reason_code'] == 'drawdown_global_limit'


@pytest.mark.parametrize('kind,code', [('missing', 'ownership_session_unavailable'),
                                       ('closed', 'ownership_connection_closed'),
                                       ('invalidated', 'ownership_connection_invalidated'),
                                       ('lost', 'ownership_already_lost'),
                                       ('mismatch', 'ownership_identity_mismatch'),
                                       ('database', 'ownership_database_error')])
def test_owner_loss_preserves_specific_safe_code_and_closes_admission(kind, code):
    owner = BotOwnership(MagicMock(), 'offline')
    owner.connection = MagicMock(closed=kind == 'closed', invalidated=kind == 'invalidated')
    owner.lost, owner.allow_new_orders = kind == 'lost', True
    if kind == 'missing':
        owner.connection = None
    if kind == 'mismatch':
        owner.connection.execute.return_value.mappings.return_value.first.return_value = None
    if kind == 'database':
        original = RuntimeError('private-dsn-and-password')
        original.pgcode = '08006'
        owner.connection.execute.side_effect = OperationalError('private query', {}, original, connection_invalidated=True)
    with patch('hummingbot.core.bot_ownership._ownership_read', lambda connection: nullcontext()):
        with pytest.raises(OwnershipLost) as caught:
            owner._read_owner()
    assert caught.value.reason_code == code
    assert owner.lost and not owner.allow_new_orders
    assert 'private' not in json.dumps(caught.value.diagnostic)
    if kind == 'database':
        assert caught.value.diagnostic['sqlstate'] == '08006'
        assert caught.value.diagnostic['connection_invalidated'] is True


def test_public_cause_handles_legacy_runtime_reason_and_rejects_private_metadata():
    from hummingbot.core.halt_diagnostics import halt_cause
    state = dict(halt=True, reason='A required runtime task failed; inspect service availability status.')
    value = halt_cause(state)
    assert value['reason_code'] == 'runtime_task_failed'
    assert value['occurred_at'] is None and value['revision'] is None
    assert value['evidence_source'] == 'legacy_reason'
    assert not value['automatic_recovery_expected']
    state.update(reason='private-secret', halt_diagnostic=dict(reason_code='private-secret',
                 occurred_at='private-secret', revision='private/secret', task='private-secret'))
    value = halt_cause(state)
    assert value['reason_code'] == 'halt_reason_unknown'
    assert 'private' not in json.dumps(value)
    assert halt_cause(dict(halt=False, halt_diagnostic=state['halt_diagnostic'])) is None


def test_safe_owner_failure_fields_survive_structured_logging():
    from hummingbot.logger.service_logging import ServiceFormatter
    record = logging.LogRecord('service', logging.ERROR, '', 0, 'Service stopped', (), None)
    record.ownership_reason_code = 'ownership_database_error'
    record.sqlstate, record.connection_invalidated = '08006', True
    result = json.loads(ServiceFormatter('json', {}).format(record))
    assert result['ownership_reason_code'] == 'ownership_database_error'
    assert result['sqlstate'] == '08006' and result['connection_invalidated'] is True
