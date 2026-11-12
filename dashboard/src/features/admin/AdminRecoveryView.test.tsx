import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { AdminRecoveryView } from './AdminRecoveryView'
import { completionRecoveryComparison } from '../../test/mocks/completionFixtures'
import type { RecoveryDecision } from '../recovery/recovery'
import type { components } from '../../core/api/schema'

describe('AdminRecoveryView', () => {
  const allowedDecision: RecoveryDecision = { allowed: true }
  const deniedDecision: RecoveryDecision = { allowed: false, reason: 'permission_required' }

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
