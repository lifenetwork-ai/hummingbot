"""Allowlisted HALT facts shared by durable state, API presentation and logging."""
import math
import os
import re
import time

RUNTIME_REASON = 'A required runtime task failed; inspect service availability status.'
LEGACY_REASONS = {
    RUNTIME_REASON: 'runtime_task_failed',
    'Trading/recorder health requires reconciliation.': 'trading_health_failed',
    'Strategy initialization/reconciliation failed; inspect sanitized logs.': 'strategy_reconciliation_failed',
    'A previous process has unresolved exchange effects; reconcile and verify it is stopped.': 'previous_runtime_unresolved',
    'Exchange reconciliation did not complete; new risk remains blocked.': 'exchange_reconciliation_incomplete',
    'An order submission has an unresolved outcome; no automatic resubmission is allowed.': 'order_outcome_unknown',
    'An order submission did not finish with a durable acknowledgment.': 'order_acknowledgment_missing',
    'Automatic recovery evidence conflicts with its durable account/financial scope.': 'recovery_evidence_conflict',
    'A fill arrived during position checkpoint cleanup; operator reconciliation is required.': 'cleanup_fill_reconciliation',
    'drawdown_controller_limit': 'drawdown_controller_limit',
    'drawdown_global_limit': 'drawdown_global_limit',
    'drawdown_checkpoint_invalid': 'drawdown_checkpoint_invalid',
    'drawdown_policy_mismatch': 'drawdown_policy_mismatch',
    'bootstrap_policy_unavailable': 'bootstrap_policy_unavailable',
    'spot_outflow_policy_unavailable': 'spot_outflow_policy_unavailable',
    'Pending order recovery exceeds its bounded scope.': 'pending_recovery_scope_limit',
    'An exchange-rejected order could not be resolved durably.': 'order_rejection_not_durable',
    'Automatic order reconciliation could not be recorded durably.': 'reconciliation_not_durable',
    'An unsent order intent could not be resolved durably.': 'unsent_intent_not_durable',
    'An exchange mutation transport ended without a confirmed outcome.': 'exchange_outcome_unknown',
    'An account mutation has an unresolved acknowledgment; verify exchange settings before recovery.': 'account_outcome_unknown',
    'A new fill arrived after its durable position transfer; financial reconciliation is required.': 'late_fill_reconciliation',
}
CAUSES = {
    'runtime_task_failed': ('technical', 'Required trading task stopped',
                            'A required trading task stopped. Verify the task and reconcile saved exchange state before restarting.'),
    'trading_health_failed': ('technical', 'Trading state needs reconciliation',
                              'Trading or recorder health could not be verified. Reconcile saved exchange state before restarting.'),
    'strategy_reconciliation_failed': ('technical', 'Strategy startup verification failed',
                                       'Strategy initialization or exchange reconciliation failed.'),
    'previous_runtime_unresolved': ('technical', 'Previous runtime needs verification',
                                    'A previous runtime left exchange effects that have not been verified.'),
    'exchange_reconciliation_incomplete': ('technical', 'Exchange reconciliation incomplete',
                                           'Exchange state has not been fully reconciled.'),
    'order_outcome_unknown': ('technical', 'Order outcome unconfirmed',
                              'An order may have reached the exchange. Verify its outcome; do not resubmit it.'),
    'order_acknowledgment_missing': ('technical', 'Order acknowledgment missing',
                                     'An order did not finish with a durable acknowledgment. Verify it before restarting.'),
    'recovery_evidence_conflict': ('technical', 'Recovery evidence conflicts',
                                   'Recovery evidence conflicts with the saved account or financial scope.'),
    'cleanup_fill_reconciliation': ('technical', 'Cleanup fill needs reconciliation',
                                    'A fill arrived during cleanup. Reconcile positions and fills before restarting.'),
    'drawdown_controller_limit': ('risk', 'Controller drawdown limit reached',
                                  'A controller reached its drawdown limit. Technical recovery cannot reset risk limits or peaks.'),
    'drawdown_global_limit': ('risk', 'Global drawdown limit reached',
                              'The session reached its global drawdown limit. Technical recovery cannot reset risk limits or peaks.'),
    'drawdown_checkpoint_invalid': ('risk', 'Drawdown information cannot be verified',
                                    'Saved drawdown information requires review before trading can restart.'),
    'shutdown_verification_incomplete': ('shutdown', 'Shutdown verification incomplete',
                                         'New orders are fenced until shutdown and saved exchange state are verified.'),
    'halt_reason_unknown': ('unknown', 'Trading halted; cause unavailable',
                            'The saved stop does not contain a recognized cause. Technical review is required.'),
}
CAUSES.update({code: ('technical', label, detail) for code, label, detail in (
    ('pending_recovery_scope_limit', 'Order recovery scope exceeded', 'Pending orders exceed the supported recovery scope. Review them before restarting.'),
    ('order_rejection_not_durable', 'Order rejection not recorded', 'The exchange rejection could not be recorded durably. Reconcile it before restarting.'),
    ('reconciliation_not_durable', 'Reconciliation result not recorded', 'Order reconciliation could not be recorded durably.'),
    ('unsent_intent_not_durable', 'Unsent order state not recorded', 'An unsent order could not be resolved durably.'),
    ('exchange_outcome_unknown', 'Exchange outcome unconfirmed', 'An exchange mutation ended without a confirmed outcome. Verify it before restarting.'),
    ('account_outcome_unknown', 'Account setting unconfirmed', 'An account setting change has an unconfirmed outcome. Verify it before restarting.'),
    ('late_fill_reconciliation', 'Late fill needs reconciliation', 'A fill arrived after its saved position transfer. Reconcile fills and positions.'),
)})
CAUSES.update({code: ('risk', label, detail) for code, label, detail in (
    ('drawdown_policy_mismatch', 'Drawdown policy changed', 'The configured drawdown policy differs from its saved binding. Review the policy before restarting.'),
    ('bootstrap_policy_unavailable', 'Bootstrap policy unavailable', 'The bound bootstrap policy cannot be loaded. Review its configuration.'),
    ('spot_outflow_policy_unavailable', 'Spot spending policy unavailable', 'The bound spot spending policy cannot be loaded. Review its configuration.'),
)})
RUNTIME_REASONS = frozenset({
    'clock_task_stopped', 'connector_task_stopped', 'executor_action_listener_stopped',
    'controller_task_stopped', 'controller_task_failed', 'controller_data_failed', 'controller_task_stalled',
} | {
    f'controller_{stage}_failed' for stage in (
        'position_mode_lock', 'position_mode_read', 'position_mode_change', 'position_mode_confirmation',
        'leverage_change', 'reference_metadata', 'reference_books')
})
TASKS = frozenset({'_clock_task', '_status_polling_task', '_user_stream_tracker_task', '_user_stream_event_listener_task',
                  '_trading_rules_polling_task', 'listen_to_executor_actions_task', '_control_loop_task'})
COMPONENTS = frozenset({'clock', 'okx', 'okx_perpetual', 'strategy', 'controller'})


def _revision(value):
    return value if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,128}', value) else None


def _timestamp(value):
    try:
        result = float(value) if not isinstance(value, bool) else math.nan
        return result if math.isfinite(result) and 0 < result < 4102444800 else None
    except (TypeError, ValueError, OverflowError):
        return None


def _task_fields(details):
    return {key: value for key, allowed in (
        ('runtime_reason_code', RUNTIME_REASONS), ('component', COMPONENTS), ('task', TASKS),
        ('task_state', frozenset({'completed', 'failed', 'cancelled', 'stalled'})))
        if isinstance(value := details.get(key), str) and value in allowed}


def record_halt(state, reason, **details):
    # Preserve the first stop; later cleanup failures do not rewrite its cause
    # or pretend the latest process observed the original event time.
    existing = state.get('halt') and state.get('reason')
    state.update(halt=True, halt_kind='manual', reason=reason)
    if existing:
        if not state.get('halt_diagnostic'):
            state['halt_diagnostic'] = dict(reason_code=LEGACY_REASONS.get(existing, 'halt_reason_unknown'),
                                            occurred_at=None, revision=None)
        return
    state['halt_diagnostic'] = dict(reason_code=LEGACY_REASONS.get(reason, 'halt_reason_unknown'),
                                    occurred_at=time.time(), revision=_revision(os.environ.get('K_REVISION') or
                                                                                os.environ.get('COMMIT_SHA') or 'local'),
                                    **_task_fields(details))


def halt_cause(state):
    if not (state.get('halt') or state.get('halted')):
        return None
    reason = state.get('reason')
    code = LEGACY_REASONS.get(reason, 'halt_reason_unknown') if isinstance(reason, str) else 'halt_reason_unknown'
    if not reason and state.get('halt_kind') == 'shutdown_pending':
        code = 'shutdown_verification_incomplete'
    metadata = state.get('halt_diagnostic')
    metadata = metadata if isinstance(metadata, dict) else {}
    # Metadata retains the original cause; the current reason still controls
    # recovery eligibility and may reflect a subsequent risk/cleanup failure.
    current_code = code
    persisted_code = metadata.get('reason_code')
    matching = isinstance(persisted_code, str) and persisted_code in CAUSES and persisted_code != 'halt_reason_unknown'
    if matching:
        code = persisted_code
    category, label, detail = CAUSES[code]
    automatic = state.get('automatic_recovery')
    automatic = automatic if isinstance(automatic, dict) else {}
    return dict(reason_code=code, current_reason_code=current_code, category=category, label=label, detail=detail,
                occurred_at=_timestamp(metadata.get('occurred_at')) if matching else None,
                revision=_revision(metadata.get('revision')) if matching else None,
                evidence_source='persisted' if matching else 'legacy_reason' if code != 'halt_reason_unknown' else 'unavailable',
                automatic_recovery_expected=bool(code == 'shutdown_verification_incomplete'
                                                 and not state.get('pending_mutations')
                                                 and not automatic.get('operator_required')
                                                 and automatic.get('cold_recovery_supported') is True),
                cleanup_incomplete=state.get('shutdown_incomplete') is True,
                **_task_fields(metadata if matching else {}))


def publication_halt_fields():
    # Constant allowlist only. Do not transfer arbitrary reason/metadata JSON or
    # archived checkpoints to the API worker just to explain a stopped runtime.
    reasons = ', '.join("'" + value.replace("'", "''") + "'" for value in LEGACY_REASONS)
    fields = ', '.join("'%s', left(recovery_state->'halt_diagnostic'->>'%s',128)" % (key, key)
                       for key in ('reason_code', 'occurred_at', 'revision', 'runtime_reason_code', 'component', 'task', 'task_state'))
    return (f"'reason', CASE WHEN recovery_state->>'reason' IN ({reasons}) THEN recovery_state->>'reason' END, "
            "'halt_kind', CASE WHEN recovery_state->>'halt_kind'='shutdown_pending' "
            "AND COALESCE(recovery_state->>'reason','')='' THEN 'shutdown_pending' END, "
            "'shutdown_incomplete', recovery_state->'shutdown_incomplete'='true'::jsonb, "
            f"'halt_diagnostic', jsonb_build_object({fields}), ")
