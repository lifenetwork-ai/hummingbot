import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { Banner } from './Banner'

describe('Banner', () => {
  it('renders title and uses status role for info by default', () => {
    render(<Banner title="Maintenance scheduled" />)
    const banner = screen.getByRole('status')
    expect(banner).toHaveTextContent('Maintenance scheduled')
    expect(screen.queryByRole('button', { name: 'Dismiss banner' })).not.toBeInTheDocument()
  })

  it('renders role="alert" for error variant', () => {
    render(
      <Banner
        variant="error"
        title="Connection lost"
        description="Reconnecting to gateway…"
      />,
    )
    const alert = screen.getByRole('alert')
    expect(alert).toHaveTextContent('Connection lost')
    expect(alert).toHaveTextContent('Reconnecting to gateway…')
  })

  it('supports warning and success variants', () => {
    const { rerender } = render(<Banner variant="warning" title="Stale market data" />)
    expect(screen.getByRole('status')).toHaveClass('border-app-warning/25')

    rerender(<Banner variant="success" title="Gateway restored" />)
    expect(screen.getByRole('status')).toHaveClass('border-app-success/25')
  })

  it('renders action button and triggers callback', () => {
    const onClick = vi.fn()
    render(
      <Banner
        title="Configuration update"
        action={{ label: 'Apply changes', onClick }}
      />,
    )
    const button = screen.getByRole('button', { name: 'Apply changes' })
    expect(button).toBeInTheDocument()
    fireEvent.click(button)
    expect(onClick).toHaveBeenCalledTimes(1)
  })

  it('renders dismiss button when onDismiss is supplied and calls it on click', () => {
    const onDismiss = vi.fn()
    render(
      <Banner
        title="Notice"
        onDismiss={onDismiss}
      />,
    )
    const dismissBtn = screen.getByRole('button', { name: 'Dismiss banner' })
    expect(dismissBtn).toBeInTheDocument()
    fireEvent.click(dismissBtn)
    expect(onDismiss).toHaveBeenCalledTimes(1)
  })
})
