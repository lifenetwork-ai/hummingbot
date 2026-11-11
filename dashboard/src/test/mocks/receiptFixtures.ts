import type { components } from '../../core/api/schema'

const timestamp = '2026-11-10T00:00:00Z'

export const commandReceipt = {
  id: 'fixture-command-receipt', action: 'stop', status: 'succeeded', session_id: 'fixture-session',
  expected_version: 1, effective_version: 2, error: null, created_at: timestamp, updated_at: timestamp,
  payload: { action: 'stop', expected_version: 1, session_id: 'fixture-session' },
} satisfies components['schemas']['CommandResponseOutput']

export const credentialReceipt = {
  id: 'fixture-credential-receipt', action: 'credentials_disconnect', status: 'failed', error: 'cleanup_pending',
  created_at: timestamp, updated_at: timestamp, expected_version: 1, effective_version: 1, session_id: null,
  payload: {
    action: 'credentials_disconnect', actor_id: 'fixture-admin', actor_kind: 'admin', bot_id: 'fixture-bot',
    cleanup_operation_id: 'fixture-cleanup', connector: 'okx', environment: 'demo', exchange: 'okx',
    expected_control_version: 2, expected_operation_version: 1, expected_version: 1,
  },
  result: {
    account_verified: false, blocked_stage: 'exchange_cleanup', cleanup_operation_id: 'fixture-cleanup',
    consumers_detached: true, credential_version: 1, ledger_confirmed: false, next_retry_at: timestamp,
    observed_at: timestamp, operation_version: 2, operator_action_required: true,
    orders_terminal_verified: null, owner_generation: 1, owner_uuid: 'fixture-owner',
    position_checkpoints_verified: null, positions_flat_verified: null, reason_code: 'cleanup_incomplete',
    remaining_order_count: 1, remaining_position_count: 0, stage: 'blocked',
  },
} satisfies components['schemas']['CredentialReceiptOutput']

export const reconciliationReceipt = {
  id: 'fixture-reconciliation-receipt', status: 'succeeded', error: null, created_at: timestamp, updated_at: timestamp,
  payload: { lookback_seconds: 3600, market: 'all', max_orders: 100, max_pages: 5, pair: null },
  report: { finished_at: timestamp, started_at: timestamp, scope: 'bot_owned_orders_and_configured_perpetual_exposure', state: 'mismatch', markets: [
    { complete: true, differences: [], exchange_observed_at: timestamp, fills_checked: 1, local_observed_at: timestamp,
      market: 'spot', orders_checked: 1, positions_checked: 0, reasons: ['exchange_order_missing_locally'], state: 'mismatch' },
  ] },
} satisfies components['schemas']['ReconciliationReceiptOutput']

export const recoveryCheckReceipt = {
  id: 'fixture-recovery-check', action: 'recovery_check', status: 'succeeded', can_recover: false,
  blockers: [{ code: 'review_required', detail: 'Review proposed repair', next_action: 'review' }],
  confirmation_token: 'SECRET-FIXTURE-CONFIRMATION-TOKEN', created_at: timestamp, detail: 'Review required',
  expires_at: timestamp, label: 'Check Recovery', proposed_changes: [], repaired_positions: [], stage: 'review_required',
  trading_state: 'stopped', updated_at: timestamp,
} satisfies components['schemas']['RecoveryOperationOutput']

export const recoveryResetReceipt = {
  id: 'fixture-recovery-reset', action: 'recovery_reset', status: 'succeeded', can_recover: true,
  blockers: [], confirmation_token: 'SECRET-FIXTURE-RESET-TOKEN', created_at: timestamp, detail: 'Recovery completed',
  expires_at: null, label: 'Reset Recovery', proposed_changes: [], repaired_positions: [], stage: 'recovered',
  trading_state: 'stopped', updated_at: timestamp,
} satisfies components['schemas']['RecoveryOperationOutput']
