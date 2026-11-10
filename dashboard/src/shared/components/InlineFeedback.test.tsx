import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { InlineFeedback } from './InlineFeedback'

describe('InlineFeedback', () => {
  it('renders error role and message by default', () => {
    render(<InlineFeedback id="email-error" message="Please enter a valid email address." />)
    const alert = screen.getByRole('alert')
    expect(alert).toHaveTextContent('Please enter a valid email address.')
    expect(alert).toHaveAttribute('id', 'email-error')
  })

  it('renders non-error variants with role="status"', () => {
    const { rerender } = render(<InlineFeedback variant="info" message="Changes will apply on next cycle." />)
    expect(screen.getByRole('status')).toHaveTextContent('Changes will apply on next cycle.')

    rerender(<InlineFeedback variant="warning" message="High volatility may widen spread." />)
    expect(screen.getByRole('status')).toHaveTextContent('High volatility may widen spread.')

    rerender(<InlineFeedback variant="success" message="Token validated." />)
    expect(screen.getByRole('status')).toHaveTextContent('Token validated.')
  })

  it('renders description and optional action', () => {
    render(
      <InlineFeedback
        message="Spread threshold exceeded."
        description="Current spread is 1.8%, max allowed is 1.5%."
        action={<button type="button">Adjust</button>}
      />,
    )
    expect(screen.getByText('Current spread is 1.8%, max allowed is 1.5%.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Adjust' })).toBeInTheDocument()
  })
})
