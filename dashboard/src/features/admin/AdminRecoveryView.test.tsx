import { fireEvent, render, screen, within } from '@testing-library/react'
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
  uncertainOrders?: ReturnType<typeof deriveRecoveryViewModel>['uncertainOrders']
  onScopeChange?: (ids: string[]) => void
  onClearScope?: () => void
  operationPending?: boolean
  onCheckRecovery: () => Promise<unknown>
  onRetryCheck: () => Promise<unknown>
  onResetRecovery: (options?: { confirmOrderChanges?: boolean }) => Promise<unknown>
  onRetryReset: () => Promise<unknown>
  onRefresh: () => Promise<unknown>
}) {
  const status = props.status ?? contextStatus
  const model = deriveRecoveryViewModel({
    permission: props.hasAccess ?? true,
    summary: props.recoverySummary ?? noHaltSummary,
    status,
    operation: props.activeOperation,
    uncertainOrders: props.uncertainOrders,
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
  return (
    <RecoveryView
      model={{
        ...model,
        ...(props.recoveryPresentation ? { presentation: props.recoveryPresentation } : {}),
        ...(props.comparison ? { comparison: props.comparison } : {}),
      }}
      onCheckRecovery={props.onCheckRecovery}
      onRetryCheck={props.onRetryCheck}
      onResetRecovery={props.onResetRecovery}
      onRetryReset={props.onRetryReset}
      onRefresh={props.onRefresh}
      onScopeChange={props.onScopeChange}
      onClearScope={props.onClearScope}
      operationPending={props.operationPending}
    />
  )
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

  it('renders single BUY uncertain order preview with correct before/after transition', () => {
    const buyOrder: components['schemas']['RecoveryUncertainOrderRepairOutput'] = {
      id: 'Spot001',
      pair: 'LIFE-USDT',
      side: 'BUY',
      amount: '10.50',
      price: '1.25',
      before: 'PendingBuy',
      after: 'OrderFailure',
      connector: 'okx',
      deadline_ms: 1762300000000,
      evidence: 'expired_submission_and_reconciled_exchange',
    }
    const activeOperation = {
      id: 'op-check-1',
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
        uncertainOrders={{
          ids: ['Spot001'],
          validationError: null,
          proposed: [buyOrder],
          repaired: [],
          completionVerified: false,
          reviewAvailable: true,
          selectionLocked: true,
        }}
        checkDecision={allowedDecision}
        reviewDecision={allowedDecision}
        onCheckRecovery={vi.fn()}
        onRetryCheck={vi.fn()}
        onResetRecovery={vi.fn()}
        onRetryReset={vi.fn()}
        onRefresh={vi.fn()}
      />,
    )

    expect(screen.getByText('Proposed Uncertain Order Repairs (1)')).toBeInTheDocument()
    const table = screen.getByRole('table')
    expect(within(table).getByText('Spot001')).toBeInTheDocument()
    expect(within(table).getByText('LIFE-USDT')).toBeInTheDocument()
    expect(within(table).getByText('BUY')).toBeInTheDocument()
    expect(within(table).getByText('10.50')).toBeInTheDocument()
    expect(within(table).getByText('1.25')).toBeInTheDocument()
    expect(within(table).getByText('PendingBuy')).toBeInTheDocument()
    expect(within(table).getByText('OrderFailure')).toBeInTheDocument()
  })

  it('renders single SELL uncertain order preview with correct before/after transition', () => {
    const sellOrder: components['schemas']['RecoveryUncertainOrderRepairOutput'] = {
      id: 'Sell001',
      pair: 'BTC-USDT',
      side: 'SELL',
      amount: '0.05',
      price: '65000.00',
      before: 'PendingSell',
      after: 'OrderFailure',
      connector: 'okx',
      deadline_ms: 1762300000000,
      evidence: 'expired_submission_and_reconciled_exchange',
    }
    const activeOperation = {
      id: 'op-check-sell',
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
        uncertainOrders={{
          ids: ['Sell001'],
          validationError: null,
          proposed: [sellOrder],
          repaired: [],
          completionVerified: false,
          reviewAvailable: true,
          selectionLocked: true,
        }}
        checkDecision={allowedDecision}
        reviewDecision={allowedDecision}
        onCheckRecovery={vi.fn()}
        onRetryCheck={vi.fn()}
        onResetRecovery={vi.fn()}
        onRetryReset={vi.fn()}
        onRefresh={vi.fn()}
      />,
    )

    expect(screen.getByText('Proposed Uncertain Order Repairs (1)')).toBeInTheDocument()
    const table = screen.getByRole('table')
    expect(within(table).getByText('Sell001')).toBeInTheDocument()
    expect(within(table).getByText('BTC-USDT')).toBeInTheDocument()
    expect(within(table).getByText('SELL')).toBeInTheDocument()
    expect(within(table).getByText('0.05')).toBeInTheDocument()
    expect(within(table).getByText('PendingSell')).toBeInTheDocument()
    expect(within(table).getByText('OrderFailure')).toBeInTheDocument()
  })

  it('renders batch BUY/SELL orders showing all rows and requires full acknowledgment in dialog without partial confirm', async () => {
    const batchOrders: components['schemas']['RecoveryUncertainOrderRepairOutput'][] = [
      { id: 'Spot001', pair: 'LIFE-USDT', side: 'BUY', amount: '10.0', price: '1.20', before: 'PendingBuy', after: 'OrderFailure', connector: 'okx', deadline_ms: 1762300000000, evidence: 'expired_submission_and_reconciled_exchange' },
      { id: 'Spot002', pair: 'LIFE-USDT', side: 'SELL', amount: '5.0', price: '1.30', before: 'PendingSell', after: 'OrderFailure', connector: 'okx', deadline_ms: 1762300000000, evidence: 'expired_submission_and_reconciled_exchange' },
      { id: 'Spot003', pair: 'BTC-USDT', side: 'BUY', amount: '0.1', price: '64000.0', before: 'PendingBuy', after: 'OrderFailure', connector: 'okx', deadline_ms: 1762300000000, evidence: 'expired_submission_and_reconciled_exchange' },
    ]
    const activeOperation = {
      id: 'op-check-batch',
      action: 'recovery_check' as const,
      status: 'succeeded' as const,
      stage: 'review_required' as const,
      expires_at: new Date(Date.now() + 60000).toISOString(),
      blockers: [],
      proposed_changes: [],
    } as unknown as components['schemas']['RecoveryOperationOutput']
    const onResetRecovery = vi.fn().mockResolvedValue(undefined)

    render(
      <AdminRecoveryView
        activeOperation={activeOperation}
        uncertainOrders={{
          ids: ['Spot001', 'Spot002', 'Spot003'],
          validationError: null,
          proposed: batchOrders,
          repaired: [],
          completionVerified: false,
          reviewAvailable: true,
          selectionLocked: true,
        }}
        checkDecision={allowedDecision}
        reviewDecision={allowedDecision}
        onCheckRecovery={vi.fn()}
        onRetryCheck={vi.fn()}
        onResetRecovery={onResetRecovery}
        onRetryReset={vi.fn()}
        onRefresh={vi.fn()}
      />,
    )

    // All 3 rows present in proposed table
    expect(screen.getByText('Proposed Uncertain Order Repairs (3)')).toBeInTheDocument()
    const table = screen.getByRole('table')
    expect(within(table).getByText('Spot001')).toBeInTheDocument()
    expect(within(table).getByText('Spot002')).toBeInTheDocument()
    expect(within(table).getByText('Spot003')).toBeInTheDocument()

    // Open Reset dialog
    fireEvent.click(screen.getByRole('button', { name: 'Confirm Reset' }))
    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByText('Confirm Uncertain Order Recovery Reset')).toBeInTheDocument()
    expect(within(dialog).getByText(/Orders to be repaired \(3\):/)).toBeInTheDocument()

    // Button should be disabled until checkbox is checked
    const dialogConfirmBtn = within(dialog).getByRole('button', { name: 'Confirm Reset' })
    expect(dialogConfirmBtn).toBeDisabled()

    // Check acknowledgment
    const checkbox = within(dialog).getByRole('checkbox')
    fireEvent.click(checkbox)
    expect(dialogConfirmBtn).not.toBeDisabled()

    // Submit reset - must pass { confirmOrderChanges: true }
    fireEvent.click(dialogConfirmBtn)
    expect(onResetRecovery).toHaveBeenCalledWith({ confirmOrderChanges: true })
  })

  it('does not display "No ledger changes proposed" when position comparison is empty but uncertain order preview exists', () => {
    const buyOrder: components['schemas']['RecoveryUncertainOrderRepairOutput'] = {
      id: 'Spot001',
      pair: 'LIFE-USDT',
      side: 'BUY',
      amount: '10.50',
      price: '1.25',
      before: 'PendingBuy',
      after: 'OrderFailure',
      connector: 'okx',
      deadline_ms: 1762300000000,
      evidence: 'expired_submission_and_reconciled_exchange',
    }
    const activeOperation = {
      id: 'op-check-1',
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
        uncertainOrders={{
          ids: ['Spot001'],
          validationError: null,
          proposed: [buyOrder],
          repaired: [],
          completionVerified: false,
          reviewAvailable: true,
          selectionLocked: true,
        }}
        checkDecision={allowedDecision}
        reviewDecision={allowedDecision}
        onCheckRecovery={vi.fn()}
        onRetryCheck={vi.fn()}
        onResetRecovery={vi.fn()}
        onRetryReset={vi.fn()}
        onRefresh={vi.fn()}
      />,
    )

    expect(screen.queryByText(/No ledger changes proposed — this is expected on the checkpoint route/)).not.toBeInTheDocument()
    expect(screen.queryByText(/Receipt does not contain proposed changes to display/)).not.toBeInTheDocument()
    expect(screen.getByText('Proposed Uncertain Order Repairs (1)')).toBeInTheDocument()
  })

  it('manages draft input, displays validation errors, and allows clearing review when selectionLocked is true', () => {
    const onScopeChange = vi.fn()
    const { rerender } = render(
      <AdminRecoveryView
        uncertainOrders={{
          ids: ['Spot001'],
          validationError: null,
          proposed: [],
          repaired: [],
          completionVerified: false,
          reviewAvailable: false,
          selectionLocked: true,
        }}
        onScopeChange={onScopeChange}
        checkDecision={allowedDecision}
        reviewDecision={allowedDecision}
        onCheckRecovery={vi.fn()}
        onRetryCheck={vi.fn()}
        onResetRecovery={vi.fn()}
        onRetryReset={vi.fn()}
        onRefresh={vi.fn()}
      />,
    )

    // Clear review button is visible and enabled even though selectionLocked is true
    const clearBtn = screen.getByRole('button', { name: 'Xóa review' })
    expect(clearBtn).not.toBeDisabled()
    fireEvent.click(clearBtn)
    expect(onScopeChange).toHaveBeenCalledWith([])

    // Apply button is locked because selectionLocked is true for entering different IDs
    const applyBtn = screen.getByRole('button', { name: 'Apply order scope' })
    expect(applyBtn).toBeDisabled()

    // Now re-render with validation error from controller
    rerender(
      <AdminRecoveryView
        uncertainOrders={{
          ids: [],
          validationError: 'Order IDs must contain 1–32 letters or numbers.',
          proposed: [],
          repaired: [],
          completionVerified: false,
          reviewAvailable: false,
          selectionLocked: false,
        }}
        onScopeChange={onScopeChange}
        checkDecision={allowedDecision}
        reviewDecision={allowedDecision}
        onCheckRecovery={vi.fn()}
        onRetryCheck={vi.fn()}
        onResetRecovery={vi.fn()}
        onRetryReset={vi.fn()}
        onRefresh={vi.fn()}
      />,
    )
    expect(screen.getByText('Order IDs must contain 1–32 letters or numbers.')).toBeInTheDocument()
  })

  it('locks both scope apply and "Xóa review" when operation is pending', () => {
    const onScopeChange = vi.fn()
    render(
      <AdminRecoveryView
        operationPending={true}
        uncertainOrders={{
          ids: ['Spot001'],
          validationError: null,
          proposed: [],
          repaired: [],
          completionVerified: false,
          reviewAvailable: false,
          selectionLocked: true,
        }}
        onScopeChange={onScopeChange}
        checkDecision={allowedDecision}
        reviewDecision={allowedDecision}
        onCheckRecovery={vi.fn()}
        onRetryCheck={vi.fn()}
        onResetRecovery={vi.fn()}
        onRetryReset={vi.fn()}
        onRefresh={vi.fn()}
      />,
    )

    const clearBtn = screen.getByRole('button', { name: 'Xóa review' })
    expect(clearBtn).toBeDisabled()
    expect(clearBtn).toHaveAttribute('title', expect.stringContaining('Wait for the current recovery operation to finish'))

    const applyBtn = screen.getByRole('button', { name: 'Apply order scope' })
    expect(applyBtn).toBeDisabled()
  })

  it('disables Reset and shows readable lock reason when preview has expired', () => {
    const buyOrder: components['schemas']['RecoveryUncertainOrderRepairOutput'] = {
      id: 'Spot001',
      pair: 'LIFE-USDT',
      side: 'BUY',
      amount: '10.50',
      price: '1.25',
      before: 'PendingBuy',
      after: 'OrderFailure',
      connector: 'okx',
      deadline_ms: 1762300000000,
      evidence: 'expired_submission_and_reconciled_exchange',
    }
    const expiredOperation = {
      id: 'op-expired',
      action: 'recovery_check' as const,
      status: 'succeeded' as const,
      stage: 'review_required' as const,
      expires_at: new Date(Date.now() - 60000).toISOString(),
      blockers: [],
      proposed_changes: [],
    } as unknown as components['schemas']['RecoveryOperationOutput']

    render(
      <AdminRecoveryView
        activeOperation={expiredOperation}
        uncertainOrders={{
          ids: ['Spot001'],
          validationError: null,
          proposed: [buyOrder],
          repaired: [],
          completionVerified: false,
          reviewAvailable: false,
          selectionLocked: true,
        }}
        checkDecision={allowedDecision}
        reviewDecision={{ allowed: false, reason: 'preview_expired' }}
        onCheckRecovery={vi.fn()}
        onRetryCheck={vi.fn()}
        onResetRecovery={vi.fn()}
        onRetryReset={vi.fn()}
        onRefresh={vi.fn()}
      />,
    )

    const confirmBtn = screen.getByRole('button', { name: 'Confirm Reset' })
    expect(confirmBtn).toBeDisabled()
    expect(confirmBtn).toHaveAttribute('title', expect.stringContaining('Preview expired'))
    expect(screen.getByText(/Expired — run new Check/)).toBeInTheDocument()
  })

  it('renders explicit retry buttons for unknown Check/Reset outcomes', () => {
    const onRetryCheck = vi.fn()
    const onRetryReset = vi.fn()
    render(
      <AdminRecoveryView
        canRetryCheck={true}
        canRetryReset={true}
        checkDecision={{ allowed: false, reason: 'request_outcome_unknown' }}
        reviewDecision={{ allowed: false, reason: 'request_outcome_unknown' }}
        onCheckRecovery={vi.fn()}
        onRetryCheck={onRetryCheck}
        onResetRecovery={vi.fn()}
        onRetryReset={onRetryReset}
        onRefresh={vi.fn()}
      />,
    )

    const retryCheckBtn = screen.getByRole('button', { name: 'Retry same Check' })
    expect(retryCheckBtn).not.toBeDisabled()
    fireEvent.click(retryCheckBtn)
    expect(onRetryCheck).toHaveBeenCalled()

    const retryResetBtn = screen.getByRole('button', { name: 'Retry same Reset' })
    expect(retryResetBtn).not.toBeDisabled()
    fireEvent.click(retryResetBtn)
    expect(onRetryReset).toHaveBeenCalled()
  })

  it('shows Recovery Succeeded and stopped notice when all repaired IDs match and completionVerified is true', () => {
    const repairedOrder: components['schemas']['RecoveryUncertainOrderRepairOutput'] = {
      id: 'Spot001',
      pair: 'LIFE-USDT',
      side: 'BUY',
      amount: '10.50',
      price: '1.25',
      before: 'PendingBuy',
      after: 'OrderFailure',
      connector: 'okx',
      deadline_ms: 1762300000000,
      evidence: 'expired_submission_and_reconciled_exchange',
    }
    const resetOperation = {
      id: 'op-reset-succeeded',
      action: 'recovery_reset' as const,
      status: 'succeeded' as const,
      stage: 'recovered' as const,
      expires_at: null,
      blockers: [],
      proposed_changes: [],
    } as unknown as components['schemas']['RecoveryOperationOutput']

    render(
      <AdminRecoveryView
        activeOperation={resetOperation}
        uncertainOrders={{
          ids: ['Spot001'],
          validationError: null,
          proposed: [],
          repaired: [repairedOrder],
          completionVerified: true,
          reviewAvailable: false,
          selectionLocked: true,
        }}
        checkDecision={allowedDecision}
        reviewDecision={allowedDecision}
        onCheckRecovery={vi.fn()}
        onRetryCheck={vi.fn()}
        onResetRecovery={vi.fn()}
        onRetryReset={vi.fn()}
        onRefresh={vi.fn()}
      />,
    )

    expect(screen.getByText('Recovery Succeeded')).toBeInTheDocument()
    expect(screen.getByText(/Trading remains in STOPPED state. The system will not automatically Start./)).toBeInTheDocument()
    expect(screen.getByText('Repaired Uncertain Orders (1)')).toBeInTheDocument()
    const table = screen.getByRole('table')
    expect(within(table).getByText('Spot001')).toBeInTheDocument()
  })

  it('shows Recovery Unverified when receipt is recovered but repair proof is incomplete', () => {
    const resetOperation = {
      id: 'op-reset-unverified',
      action: 'recovery_reset' as const,
      status: 'succeeded' as const,
      stage: 'recovered' as const,
      expires_at: null,
      blockers: [],
      proposed_changes: [],
    } as unknown as components['schemas']['RecoveryOperationOutput']

    render(
      <AdminRecoveryView
        activeOperation={resetOperation}
        uncertainOrders={{
          ids: ['Spot001', 'Spot002'],
          validationError: null,
          proposed: [],
          repaired: [],
          completionVerified: false,
          reviewAvailable: false,
          selectionLocked: true,
        }}
        checkDecision={allowedDecision}
        reviewDecision={allowedDecision}
        onCheckRecovery={vi.fn()}
        onRetryCheck={vi.fn()}
        onResetRecovery={vi.fn()}
        onRetryReset={vi.fn()}
        onRefresh={vi.fn()}
      />,
    )

    expect(screen.getByText('Recovery Unverified')).toBeInTheDocument()
    expect(screen.getByText(/repair proof is incomplete for the selected order IDs/)).toBeInTheDocument()
    expect(screen.queryByText('Recovery Succeeded')).not.toBeInTheDocument()
  })

  it('allows filling draft from fresh runtime suggested orderId', () => {
    const statusWithOrderId = {
      ...contextStatus,
      automatic_recovery: {
        admission_mode: 'paused',
        cold_recovery_supported: true,
        isolated: false,
        last_progress_at: null,
        next_retry_at: null,
        operator_required: false,
        order_id: 'SpotSuggested99',
        phase: 'reconciling',
        quarantined_orders: 1,
        reason_code: 'order_outcome_unknown',
        reserved_base: undefined,
        reserved_quote_worst_case: '0',
        stale: false,
        started_at: 1762300000000,
      },
    } as unknown as components['schemas']['StatusResponseOutput']

    render(
      <AdminRecoveryView
        status={statusWithOrderId}
        recoveryPresentation={deriveRecoveryPresentation(statusWithOrderId as unknown as ReadResponse<'getRuntimeStatus'>, true)}
        uncertainOrders={{
          ids: [],
          validationError: null,
          proposed: [],
          repaired: [],
          completionVerified: false,
          reviewAvailable: false,
          selectionLocked: false,
        }}
        checkDecision={allowedDecision}
        reviewDecision={allowedDecision}
        onCheckRecovery={vi.fn()}
        onRetryCheck={vi.fn()}
        onResetRecovery={vi.fn()}
        onRetryReset={vi.fn()}
        onRefresh={vi.fn()}
      />,
    )

    // Switch to scoped mode
    fireEvent.click(screen.getByRole('tab', { name: 'Uncertain Orders' }))
    const suggestionBtn = screen.getByRole('button', { name: 'SpotSuggested99' })
    expect(suggestionBtn).toBeInTheDocument()
    fireEvent.click(suggestionBtn)
    const input = screen.getByLabelText('Client Order IDs') as HTMLInputElement
    expect(input.value).toBe('SpotSuggested99')
  })

  it('disables Check recovery when Uncertain Orders tab is selected but IDs have not been applied', () => {
    render(
      <AdminRecoveryView
        uncertainOrders={{
          ids: [],
          validationError: null,
          proposed: [],
          repaired: [],
          completionVerified: false,
          reviewAvailable: false,
          selectionLocked: false,
        }}
        checkDecision={allowedDecision}
        reviewDecision={allowedDecision}
        onCheckRecovery={vi.fn()}
        onRetryCheck={vi.fn()}
        onResetRecovery={vi.fn()}
        onRetryReset={vi.fn()}
        onRefresh={vi.fn()}
      />,
    )

    // Switch to Uncertain Orders
    fireEvent.click(screen.getByRole('button', { name: 'Review uncertain orders' }))
    const checkBtn = screen.getByRole('button', { name: 'Check recovery' })
    expect(checkBtn).toBeDisabled()
    expect(checkBtn).toHaveAttribute('title', 'Nhập và Apply ID lệnh trước khi Check')
    expect(checkBtn).toHaveAttribute('aria-describedby', 'check-recovery-guidance')
    expect(screen.getByText('Nhập và Apply ID lệnh trước khi Check')).toBeInTheDocument()
  })

  it('does NOT display Recovery Unverified when check succeeds with stage review_required', () => {
    const checkOp = {
      id: 'op-check-succ',
      action: 'recovery_check' as const,
      status: 'succeeded' as const,
      stage: 'review_required' as const,
      expires_at: new Date(Date.now() + 60000).toISOString(),
      blockers: [],
      proposed_changes: [],
    } as unknown as components['schemas']['RecoveryOperationOutput']

    render(
      <AdminRecoveryView
        activeOperation={checkOp}
        uncertainOrders={{
          ids: ['Spot001'],
          validationError: null,
          proposed: [
            { id: 'Spot001', pair: 'LIFE-USDT', side: 'BUY', amount: '10.0', price: '1.20', before: 'PendingBuy', after: 'OrderFailure', connector: 'okx', deadline_ms: 1762300000000, evidence: 'expired_submission_and_reconciled_exchange' },
          ],
          repaired: [],
          completionVerified: false,
          reviewAvailable: true,
          selectionLocked: true,
        }}
        checkDecision={allowedDecision}
        reviewDecision={allowedDecision}
        onCheckRecovery={vi.fn()}
        onRetryCheck={vi.fn()}
        onResetRecovery={vi.fn()}
        onRetryReset={vi.fn()}
        onRefresh={vi.fn()}
      />,
    )

    expect(screen.queryByText('Recovery Unverified')).not.toBeInTheDocument()
    expect(screen.getByText('Proposed Uncertain Order Repairs (1)')).toBeInTheDocument()
  })

  it('passes resetError into RecoveryResetDialog so error is visible inside the modal', () => {
    const checkOp = {
      id: 'op-check-ready',
      action: 'recovery_check' as const,
      status: 'succeeded' as const,
      stage: 'review_required' as const,
      expires_at: new Date(Date.now() + 60000).toISOString(),
      blockers: [],
      proposed_changes: [],
    } as unknown as components['schemas']['RecoveryOperationOutput']

    render(
      <AdminRecoveryView
        activeOperation={checkOp}
        resetError="Reset failed: simulated server conflict"
        uncertainOrders={{
          ids: ['Spot001'],
          validationError: null,
          proposed: [
            { id: 'Spot001', pair: 'LIFE-USDT', side: 'BUY', amount: '10.0', price: '1.20', before: 'PendingBuy', after: 'OrderFailure', connector: 'okx', deadline_ms: 1762300000000, evidence: 'expired_submission_and_reconciled_exchange' },
          ],
          repaired: [],
          completionVerified: false,
          reviewAvailable: true,
          selectionLocked: true,
        }}
        checkDecision={allowedDecision}
        reviewDecision={allowedDecision}
        onCheckRecovery={vi.fn()}
        onRetryCheck={vi.fn()}
        onResetRecovery={vi.fn()}
        onRetryReset={vi.fn()}
        onRefresh={vi.fn()}
      />,
    )

    // Open Reset dialog
    fireEvent.click(screen.getByRole('button', { name: 'Confirm Reset' }))
    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByText('Error applying Reset:')).toBeInTheDocument()
    expect(within(dialog).getByText(/Reset failed: simulated server conflict/)).toBeInTheDocument()
  })
})
