import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { AdminRecoveryView as RecoveryView } from './AdminRecoveryView'
import { completionRecoveryComparison } from '../../test/mocks/completionFixtures'
import { deriveRecoveryPresentation } from '../recovery/presentation'
import type { RecoveryDecision, RecoveryOperation, RecoveryOverview } from '../recovery/recovery'
import { deriveRecoveryViewModel } from '../recovery/view-model'
import type { ReadResponse } from '../../core/api/admin'
import { contextStatus } from '../../test/mocks/contextFixtures'
import type { components } from '../../core/api/schema'

const allowedDecision: RecoveryDecision = { allowed: true }
const deniedDecision: RecoveryDecision = { allowed: false, reason: 'permission_required' }
const noHaltSummary = { cause: null, halted: false, check_available: false, reset_available: false, blockers: [], operation: null, control_version: 2 }

function AdminRecoveryView(props: {
  recoverySummary?: RecoveryOverview | null
  status?: ReadResponse<'getRuntimeStatus'> | null
  recoveryPresentation?: ReturnType<typeof deriveRecoveryPresentation> | null
  activeOperation?: RecoveryOperation
  checkDecision?: RecoveryDecision
  reviewDecision?: RecoveryDecision
  canRetryCheck?: boolean
  canRetryReset?: boolean
  isChecking?: boolean
  isResetting?: boolean
  checkError?: string
  resetError?: string
  comparison?: ReturnType<typeof deriveRecoveryViewModel>['comparison'] | null
  hasAccess?: boolean
  onCheckRecovery: () => Promise<unknown>
  onRetryCheck: () => Promise<unknown>
  onResetRecovery: () => Promise<unknown>
  onRetryReset: () => Promise<unknown>
  onRefresh: () => Promise<unknown>
}) {
  const status = props.status ?? contextStatus
  const model = deriveRecoveryViewModel({
    permission: props.hasAccess ?? true,
    summary: props.recoverySummary ?? noHaltSummary,
    status,
    operation: props.activeOperation,
    checkDecision: props.checkDecision ?? allowedDecision,
    reviewDecision: props.reviewDecision ?? allowedDecision,
    canRetryCheck: props.canRetryCheck ?? false,
    canRetryReset: props.canRetryReset ?? false,
    isChecking: props.isChecking ?? false,
    isResetting: props.isResetting ?? false,
    checkError: props.checkError,
    resetError: props.resetError,
    fresh: true,
    now: Date.now(),
  })
  return <RecoveryView model={{ ...model,
    ...(props.recoveryPresentation ? { presentation: props.recoveryPresentation } : {}),
    ...(props.comparison ? { comparison: props.comparison } : {}),
  }} onCheckRecovery={props.onCheckRecovery} onRetryCheck={props.onRetryCheck}
    onResetRecovery={props.onResetRecovery} onRetryReset={props.onRetryReset} onRefresh={props.onRefresh} />
}

describe('AdminRecoveryView', () => {
  it('uses shutdown guidance and disables manual actions while the runtime reports wait', () => {
    const status: ReadResponse<'getRuntimeStatus'> = { ...contextStatus, recovery: { ...contextStatus.recovery!, halted: true, next_action: 'wait' }, operator: { state: 'waiting', label: 'Verifying shutdown', detail: 'Wait for owner shutdown.', next_action: 'wait' } }
    render(<AdminRecoveryView recoverySummary={{ ...noHaltSummary, halted: true }} status={status}
      recoveryPresentation={deriveRecoveryPresentation(status, true)} checkDecision={allowedDecision} reviewDecision={allowedDecision}
      onCheckRecovery={vi.fn()} onRetryCheck={vi.fn()} onResetRecovery={vi.fn()} onRetryReset={vi.fn()} onRefresh={vi.fn()} />)
    expect(screen.getByText('Verifying shutdown')).toBeInTheDocument()
    expect(screen.queryByText('HALT Incident Recovery')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Check recovery' })).toBeDisabled()
  })
  const automatic: NonNullable<ReadResponse<'getRuntimeStatus'>['automatic_recovery']> = {
    admission_mode: 'paused', cold_recovery_supported: true, isolated: false, last_progress_at: null,
    next_retry_at: null, operator_required: false, order_id: null, phase: 'reconciling',
    quarantined_orders: 1, reason_code: 'order_outcome_unknown', reserved_base: { BTC: '0.125' },
    reserved_quote_worst_case: '5400.25', stale: false, started_at: 1762300000000,
  }
  const statusWithAutomatic = { ...contextStatus, automatic_recovery: automatic } as ReadResponse<'getRuntimeStatus'>

  it('does not report "No recovery needed" while automatic recovery is active', () => {
    render(
      <AdminRecoveryView
        recoverySummary={noHaltSummary}
        status={statusWithAutomatic as unknown as components['schemas']['StatusResponseOutput']}
        recoveryPresentation={deriveRecoveryPresentation(statusWithAutomatic, true)}
        checkDecision={allowedDecision}
        reviewDecision={allowedDecision}
        onCheckRecovery={vi.fn()}
        onRetryCheck={vi.fn()}
        onResetRecovery={vi.fn()}
        onRetryReset={vi.fn()}
        onRefresh={vi.fn()}
      />,
    )
    expect(screen.queryByText('No Incident Recovery Required')).not.toBeInTheDocument()
    expect(screen.getByText(/Automatic order recovery in progress/)).toBeInTheDocument()
    expect(screen.getByText('reconciling')).toBeInTheDocument()
    expect(screen.getByText(/0.125 BTC/)).toBeInTheDocument()
  })

  it('does not report "No recovery needed" when the observation is stale', () => {
    const staleAutomatic = { ...automatic, phase: 'idle' as const, quarantined_orders: 0, reserved_base: undefined, reserved_quote_worst_case: '0' }
    const staleStatus = { ...contextStatus, automatic_recovery: staleAutomatic } as ReadResponse<'getRuntimeStatus'>
    render(
      <AdminRecoveryView
        recoverySummary={noHaltSummary}
        status={staleStatus as unknown as components['schemas']['StatusResponseOutput']}
        recoveryPresentation={deriveRecoveryPresentation(staleStatus, false)}
        checkDecision={allowedDecision}
        reviewDecision={allowedDecision}
        onCheckRecovery={vi.fn()}
        onRetryCheck={vi.fn()}
        onResetRecovery={vi.fn()}
        onRetryReset={vi.fn()}
        onRefresh={vi.fn()}
      />,
    )
    expect(screen.queryByText('No Incident Recovery Required')).not.toBeInTheDocument()
  })

  it('reports "No recovery needed" only for a fresh idle observation', () => {
    const idleStatus = {
      ...contextStatus,
      recovery: { halted: false, detail: null, next_action: 'none', check_available: false, reset_available: false, pending_mutations: false, status_url: '/recovery' },
      automatic_recovery: { ...automatic, phase: 'idle' as const, quarantined_orders: 0, reserved_base: undefined, reserved_quote_worst_case: '0' },
    } as unknown as ReadResponse<'getRuntimeStatus'>
    render(
      <AdminRecoveryView
        recoverySummary={noHaltSummary}
        status={idleStatus as unknown as components['schemas']['StatusResponseOutput']}
        recoveryPresentation={deriveRecoveryPresentation(idleStatus, true)}
        checkDecision={allowedDecision}
        reviewDecision={allowedDecision}
        onCheckRecovery={vi.fn()}
        onRetryCheck={vi.fn()}
        onResetRecovery={vi.fn()}
        onRetryReset={vi.fn()}
        onRefresh={vi.fn()}
      />,
    )
    expect(screen.getByText('No Incident Recovery Required')).toBeInTheDocument()
  })

  it('presents verification per the verified-checkpoint scope, including orders_checked=0 meaning', () => {
    const activeOperation = {
      id: 'op-checkpoint',
      action: 'recovery_check' as const,
      status: 'succeeded' as const,
      stage: 'review_required' as const,
      expires_at: new Date(Date.now() + 60000).toISOString(),
      blockers: [],
      proposed_changes: [],
      verification: { scope: 'verified_checkpoint', orders_checked: 0, fills_checked: 12 },
    } as unknown as components['schemas']['RecoveryOperationOutput']

    render(
      <AdminRecoveryView
        activeOperation={activeOperation}
        checkDecision={allowedDecision}
        reviewDecision={allowedDecision}
        onCheckRecovery={vi.fn()}
        onRetryCheck={vi.fn()}
        onResetRecovery={vi.fn()}
        onRetryReset={vi.fn()}
        onRefresh={vi.fn()}
      />,
    )
    expect(screen.getByText('verified_checkpoint')).toBeInTheDocument()
    expect(screen.getByText(/financial rows, remaining cost basis and risk checkpoints are preserved/)).toBeInTheDocument()
    expect(screen.getByText(/orders_checked = 0 means no historical per-order REST reads were needed/)).toBeInTheDocument()
    expect(screen.getByText(/No ledger changes proposed — this is expected on the checkpoint route/)).toBeInTheDocument()
    expect(screen.getByText(/An empty proposed-change list is a valid review outcome/)).toBeInTheDocument()

    // Reset confirmation follows the scope copy: nothing to apply on the checkpoint route
    fireEvent.click(screen.getByRole('button', { name: 'Confirm Reset' }))
    expect(screen.getByText('Confirm verified checkpoint review')).toBeInTheDocument()
    expect(screen.getByText(/No ledger changes will be applied/)).toBeInTheDocument()
  })


  it('renders contact Admin instructions and no check/reset buttons when permission denied', () => {
    render(
      <AdminRecoveryView
        hasAccess={false}
        checkDecision={deniedDecision}
        reviewDecision={deniedDecision}
        onCheckRecovery={vi.fn()}
        onRetryCheck={vi.fn()}
        onResetRecovery={vi.fn()}
        onRetryReset={vi.fn()}
        onRefresh={vi.fn()}
      />,
    )

    expect(screen.getByText('Administrator Access Required (manage_recovery required)')).toBeInTheDocument()
    expect(screen.getByText(/Please contact a system Administrator/)).toBeInTheDocument()

    // No Check or Reset buttons should be present
    expect(screen.queryByRole('button', { name: /Check recovery/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Confirm Reset/i })).not.toBeInTheDocument()
  })

  it('renders all 7 ledger comparison fields before and after from completionRecoveryComparison', () => {
    const activeOperation = {
      id: 'op-recovery-1',
      action: 'recovery_check' as const,
      status: 'succeeded' as const,
      stage: 'review_required' as const,
      expires_at: new Date(Date.now() + 60000).toISOString(),
      blockers: [],
      proposed_changes: [],
    } as unknown as components['schemas']['RecoveryOperationOutput']

    render(
      <AdminRecoveryView
        activeOperation={activeOperation}
        comparison={completionRecoveryComparison}
        checkDecision={allowedDecision}
        reviewDecision={allowedDecision}
        onCheckRecovery={vi.fn()}
        onRetryCheck={vi.fn()}
        onResetRecovery={vi.fn()}
        onRetryReset={vi.fn()}
        onRefresh={vi.fn()}
      />,
    )

    // Header info
    expect(screen.getByText('LIFE-USDT')).toBeInTheDocument()
    expect(screen.getByText('(okx_perpetual)')).toBeInTheDocument()
    expect(screen.getByText('controller-1')).toBeInTheDocument()
    expect(screen.getByText('reconstructed_average_entry_not_live_pnl')).toBeInTheDocument()

    // All 7 fields
    expect(screen.getByText(/Side \(side\)/)).toBeInTheDocument()
    expect(screen.getByText(/Amount \(amount\)/)).toBeInTheDocument()
    expect(screen.getByText(/Breakeven Price \(breakeven_price\)/)).toBeInTheDocument()
    expect(screen.getByText(/Cumulative Fees \(cum_fees_quote\)/)).toBeInTheDocument()
    expect(screen.getByText(/Realized PnL \(realized_pnl_quote\)/)).toBeInTheDocument()
    expect(screen.getByText(/Unrealized PnL \(unrealized_pnl_quote\)/)).toBeInTheDocument()
    expect(screen.getByText(/Traded Volume \(volume_traded_quote\)/)).toBeInTheDocument()

    // Explanation
    expect(screen.getByText(/Values are reconstructed from checkpoint/)).toBeInTheDocument()
    expect(screen.getByText(/Live positions and actual PnL are separate exchange data feeds/)).toBeInTheDocument()

    // Preview expiry
    expect(screen.getByText(/Preview expiry/)).toBeInTheDocument()
  })

  it('renders notice when check succeeded but cannot recover (stage != review_required)', () => {
    const activeOperation = {
      id: 'op-recovery-blocked',
      action: 'recovery_check' as const,
      status: 'succeeded' as const,
      stage: 'blocked' as const,
      expires_at: null,
      blockers: [{ code: 'POSITION_MISMATCH', detail: 'Position does not match ledger', next_action: 'close_positions' }],
      proposed_changes: [],
    } as unknown as components['schemas']['RecoveryOperationOutput']

    render(
      <AdminRecoveryView
        activeOperation={activeOperation}
        checkDecision={allowedDecision}
        reviewDecision={{ allowed: false, reason: 'blocked' }}
        onCheckRecovery={vi.fn()}
        onRetryCheck={vi.fn()}
        onResetRecovery={vi.fn()}
        onRetryReset={vi.fn()}
        onRefresh={vi.fn()}
      />,
    )

    expect(screen.getByText(/Check completed but cannot recover \(can_recover: false\)/)).toBeInTheDocument()
    expect(screen.getByText('POSITION_MISMATCH')).toBeInTheDocument()
    expect(screen.getByText('Position does not match ledger')).toBeInTheDocument()
  })
})
