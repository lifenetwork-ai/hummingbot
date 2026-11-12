import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { DataSourceErrorBanner } from './DataSourceErrorBanner'

const ready = { name: 'Balances', phase: 'ready' as const, error: null, data: {}, refreshing: false, retry: vi.fn() }

describe('DataSourceErrorBanner', () => {
  it('renders nothing when every source is healthy', () => {
    const { container } = render(<DataSourceErrorBanner sources={[ready, ready]} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('lists each failed source once with retry buttons that fire only their own retry', () => {
    const retryLiquidity = vi.fn()
    const retryStatus = vi.fn()
    render(
      <DataSourceErrorBanner
        sources={[
          { name: 'Runtime status', phase: 'error', error: new Error('status down'), data: undefined, refreshing: false, retry: retryStatus },
          ready,
          { name: 'Liquidity', phase: 'error', error: new Error('liquidity down'), data: { retained: true }, refreshing: false, retry: retryLiquidity },
        ]}
      />,
    )

    expect(screen.getByText('Runtime status: API error')).toBeInTheDocument()
    expect(screen.getByText(/liquidity down\. Previous observations are retained/)).toBeInTheDocument()
    expect(screen.getByText(/status down\. No observation received\./)).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /retry liquidity/i }))
    expect(retryLiquidity).toHaveBeenCalledTimes(1)
    expect(retryStatus).not.toHaveBeenCalled()
  })

  it('disables retry while a source is refreshing', () => {
    render(
      <DataSourceErrorBanner
        sources={[{ name: 'Balances', phase: 'error', error: new Error('x'), data: undefined, refreshing: true, retry: vi.fn() }]}
      />,
    )
    expect(screen.getByRole('button', { name: /retry balances/i })).toBeDisabled()
  })
})
