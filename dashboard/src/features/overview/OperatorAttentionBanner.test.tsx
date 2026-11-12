import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { OperatorAttentionBanner } from './OperatorAttentionBanner'

describe('OperatorAttentionBanner backend guidance', () => {
  it('does not recategorize a risk stop as technical recovery or invent a recovery action', () => {
    const action = vi.fn()
    render(<OperatorAttentionBanner recovery={{ halted: true, detail: 'Risk policy remains active; contact support.', next_action: 'contact_support', check_available: false, reset_available: false, pending_mutations: false, status_url: '/recovery' }} onAction={action} />)
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
})
