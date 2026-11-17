import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { ReadResponse } from '../../core/api/admin'
import { contextStatus } from '../../test/mocks/contextFixtures'
import { deriveRecoveryPresentation } from '../recovery/presentation'
import { OperatorAttentionBanner } from './OperatorAttentionBanner'

const haltedWaitStatus = {
  ...contextStatus,
  recovery: { halted: true, detail: 'Shutdown verification in progress.', next_action: 'wait', check_available: false, reset_available: false, pending_mutations: false, status_url: '/recovery' },
  operator: { state: 'waiting', label: 'Shutdown being verified', detail: 'Start and Switch stay disabled; Stop remains available.', next_action: 'wait' },
} as ReadResponse<'getRuntimeStatus'>

describe('OperatorAttentionBanner backend guidance', () => {
  it('presents a temporary shutdown fence as returned progress instead of an emergency', () => {
    render(<OperatorAttentionBanner recovery={{ halted: true, cause: null, detail: 'Wait for owner recovery.',
      next_action: 'wait', pending_mutations: false, check_available: false, reset_available: false, status_url: '/recovery' }}
      operator={{ state: 'stopping', label: 'Trading owner is shutting down', detail: 'Shutdown verification is in progress.', next_action: 'wait' }} />)
    expect(screen.getByText('Trading owner is shutting down')).toBeInTheDocument()
    expect(screen.getByText('Shutdown verification is in progress.')).toBeInTheDocument()
    expect(screen.queryByText(/EMERGENCY/)).not.toBeInTheDocument()
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })
  it('shows the persisted cause and known event time without an invented emergency label', () => {
    render(<OperatorAttentionBanner recovery={{ halted: true, detail: 'Run a recovery check.', next_action: 'contact_support',
      pending_mutations: false, check_available: false, reset_available: false, status_url: '/recovery',
      cause: { reason_code: 'runtime_task_failed', current_reason_code: 'runtime_task_failed', category: 'technical',
        label: 'Required trading task stopped', detail: 'Verify the user stream before restarting.',
        occurred_at: '2026-11-16T08:16:54Z', revision: 'hummingbot-demo-00158-dxf', evidence_source: 'persisted',
        automatic_recovery_expected: false, cleanup_incomplete: false, component: 'okx_perpetual',
        task: '_user_stream_tracker_task', task_state: 'completed', runtime_reason_code: 'connector_task_stopped' },
    }} />)
    expect(screen.getByText('Required trading task stopped')).toBeInTheDocument()
    expect(screen.getByText('Verify the user stream before restarting.')).toBeInTheDocument()
    expect(screen.queryByText(/EMERGENCY/)).not.toBeInTheDocument()
    expect(screen.getByText(/runtime_task_failed/)).toBeInTheDocument()
    expect(screen.getByText(/hummingbot-demo-00158-dxf/)).toBeInTheDocument()
    expect(screen.getByText(/2026-11-16T08:16:54Z/)).toBeInTheDocument()
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })

  it('keeps unknown legacy event time unknown', () => {
    render(<OperatorAttentionBanner recovery={{ halted: true, detail: 'Contact support.', next_action: 'contact_support',
      pending_mutations: false, check_available: false, reset_available: false, status_url: '/recovery',
      cause: { reason_code: 'halt_reason_unknown', current_reason_code: 'halt_reason_unknown', category: 'unknown',
        label: 'Trading halted; cause unavailable', detail: 'Technical review is required.', occurred_at: null,
        revision: null, evidence_source: 'unavailable', automatic_recovery_expected: false, cleanup_incomplete: true,
        component: null, runtime_reason_code: null, task: null, task_state: null },
    }} />)
    expect(screen.getByText('Trading halted; cause unavailable')).toBeInTheDocument()
    expect(screen.getByText(/Event time unavailable/)).toBeInTheDocument()
  })

  it('renders operator guidance instead of an EMERGENCY HALT banner while recovery.next_action=wait', () => {
    const action = vi.fn()
    render(
      <OperatorAttentionBanner
        operator={haltedWaitStatus.operator}
        recovery={haltedWaitStatus.recovery}
        recoveryPresentation={deriveRecoveryPresentation(haltedWaitStatus, true)}
        onAction={action}
      />,
    )
    expect(screen.queryByText(/EMERGENCY HALT/)).not.toBeInTheDocument()
    expect(screen.getByText('Shutdown being verified')).toBeInTheDocument()
    expect(screen.getByText(/Start and Switch stay disabled; Stop remains available/)).toBeInTheDocument()
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })

  it('keeps the manual HALT banner when next_action is not wait', () => {
    const status = {
      ...contextStatus,
      recovery: { halted: true, detail: 'Manual HALT active.', next_action: 'check_recovery', check_available: true, reset_available: false, pending_mutations: false, status_url: '/recovery' },
    } as ReadResponse<'getRuntimeStatus'>
    render(
      <OperatorAttentionBanner
        recovery={status.recovery}
        recoveryPresentation={deriveRecoveryPresentation(status, true)}
      />,
    )
    expect(screen.getByText('Trading halted')).toBeInTheDocument()
  })
  it('does not recategorize a risk stop as technical recovery or invent a recovery action', () => {
    const action = vi.fn()
    render(<OperatorAttentionBanner recovery={{ halted: true, cause: null, detail: 'Risk policy remains active; contact support.', next_action: 'contact_support', check_available: false, reset_available: false, pending_mutations: false, status_url: '/recovery' }} onAction={action} />)
    expect(screen.getByText('Risk policy remains active; contact support.')).toBeInTheDocument()
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
    expect(screen.getByText(/Contact technical support/)).toBeInTheDocument()
    expect(screen.queryByText(/technical issue/)).not.toBeInTheDocument()
    expect(action).not.toHaveBeenCalled()
  })

  it('renders waiting state with instruction and does not create dead buttons', () => {
    const action = vi.fn()
    render(
      <OperatorAttentionBanner
        operator={{
          state: 'waiting',
          label: 'Inventory restriction',
          detail: 'Spot BUY is restricted; SELL observation remains independent.',
          next_action: 'wait',
        }}
        onAction={action}
      />,
    )
    expect(screen.getByText('Inventory restriction')).toBeInTheDocument()
    expect(screen.getByText('Spot BUY is restricted; SELL observation remains independent.')).toBeInTheDocument()
    expect(screen.getByText(/Recommended next step: Waiting for system conditions to synchronize/)).toBeInTheDocument()
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
    expect(action).not.toHaveBeenCalled()
  })

  it('indicates stale observation without turning into a current conclusion', () => {
    render(
      <OperatorAttentionBanner
        operator={{
          state: 'needs_attention',
          label: 'Spread deviation',
          detail: 'Last known deviation exceeds spread policy threshold.',
          next_action: 'monitor',
        }}
        observation="stale"
      />,
    )
    expect(screen.getByText(/Observation data is not current/)).toBeInTheDocument()
    expect(screen.getByText(/Recommended next step: Continue monitoring activity/)).toBeInTheDocument()
  })

  it('renders navigation button only for actions with supported navigation', () => {
    const action = vi.fn()
    render(
      <OperatorAttentionBanner
        operator={{
          state: 'needs_attention',
          label: 'Session expired',
          detail: 'Session completed. Start a new session if required.',
          next_action: 'start',
        }}
        onAction={action}
      />,
    )
    const button = screen.getByRole('button', { name: /Start new session/ })
    expect(button).toBeInTheDocument()
    button.click()
    expect(action).toHaveBeenCalledWith('start')
  })

  it('blocks navigation actions when the observation is stale', () => {
    const action = vi.fn()
    render(
      <OperatorAttentionBanner
        operator={{ state: 'needs_attention', label: 'Session expired', detail: 'Start is available after refresh.', next_action: 'start' }}
        observation="stale"
        onAction={action}
      />,
    )
    expect(screen.getByText(/Observation data is not current/)).toBeInTheDocument()
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
    expect(action).not.toHaveBeenCalled()
  })
})

it('shows backend technical guidance for a connector HALT', () => {
  render(<OperatorAttentionBanner
    operator={{ state: 'needs_attention', label: 'Technical review needed', detail: 'Trading cannot restart until the cause is resolved.', next_action: 'contact_support' }}
    recovery={{ halted: true, cause: null, detail: 'Contact support before restarting trading.', next_action: 'contact_support', check_available: false, reset_available: false, pending_mutations: false, status_url: '/recovery' }}
  />)
  expect(screen.getByText('Trading halted — Technical review needed')).toBeInTheDocument()
  expect(screen.getByText('Trading cannot restart until the cause is resolved.')).toBeInTheDocument()
  expect(screen.queryByText(/EMERGENCY HALT/)).not.toBeInTheDocument()
  expect(screen.queryByRole('button')).not.toBeInTheDocument()
})
