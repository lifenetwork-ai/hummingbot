import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { components } from '../../core/api/schema'
import { WorkingOrdersTable } from './WorkingOrdersTable'

describe('WorkingOrdersTable', () => {
  const sampleWorkingOrder: components['schemas']['OrderViewOutput'] = {
    id: 'work-ord-1234567890',
    exchange_order_id: 'ex-123',
    connector: 'okx',
    market: 'spot',
    pair: 'LIFE-USDT',
    side: 'BUY',
    price: '0.045000',
    amount: '1000',
    recorded_filled_amount: '0',
    recorded_remaining_amount: '1000',
    status: 'BuyOrderCreated',
    creation_pending: false,
    leverage: 1,
    order_type: 'LIMIT',
    position: null,
    progress_basis: 'recorded_fills_only',
    created_at: '2026-11-10T00:00:00Z',
    updated_at: '2026-11-10T00:00:00Z',
  }

  it.each(['PendingBuy', 'PendingSell', 'BuyOrderCreated', 'SellOrderCreated', 'OrderFilled'])(
    'renders backend-filtered open orders with recorded status %s', (status) => {
      render(<WorkingOrdersTable orders={[{ ...sampleWorkingOrder, status }]} onSelectOrder={vi.fn()} />)

      expect(screen.getByTitle(sampleWorkingOrder.id)).toBeInTheDocument()
      expect(screen.getByText(status)).toBeInTheDocument()
      expect(screen.queryByText('No open orders found on this results page.')).not.toBeInTheDocument()
    },
  )

  it('shows an empty result when the API returns no orders', () => {
    render(<WorkingOrdersTable orders={[]} onSelectOrder={vi.fn()} />)
    expect(screen.getByText('No open orders found on this results page.')).toBeInTheDocument()
  })

  it('shows loading and error states, then renders recovered data', () => {
    const props = { orders: [sampleWorkingOrder], onSelectOrder: vi.fn() }
    const { rerender } = render(<WorkingOrdersTable {...props} isLoading />)
    expect(screen.getByText('Loading open orders in bot data...')).toBeInTheDocument()
    expect(screen.queryByTitle(sampleWorkingOrder.id)).not.toBeInTheDocument()

    rerender(<WorkingOrdersTable {...props} error={new Error('Unavailable')} />)
    expect(screen.getByRole('alert')).toHaveTextContent('Failed to load open orders in bot data.')
    expect(screen.queryByTitle(sampleWorkingOrder.id)).not.toBeInTheDocument()

    rerender(<WorkingOrdersTable {...props} />)
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.getByTitle(sampleWorkingOrder.id)).toBeInTheDocument()
  })

  it('requests the next page and disables pagination while it is loading', () => {
    const onNextPage = vi.fn()
    const props = { orders: [sampleWorkingOrder], onSelectOrder: vi.fn(), nextCursor: true, onNextPage }
    const { rerender } = render(<WorkingOrdersTable {...props} />)
    fireEvent.click(screen.getByRole('button', { name: 'Load more recorded orders' }))
    expect(onNextPage).toHaveBeenCalledTimes(1)

    rerender(<WorkingOrdersTable {...props} isFetchingNextPage />)
    expect(screen.getByRole('button', { name: 'Loading...' })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: 'Loading...' }))
    expect(onNextPage).toHaveBeenCalledTimes(1)
  })

  it('renders order ID truncated with full ID in title for hover and has copy button', () => {
    const onSelect = vi.fn()
    render(<WorkingOrdersTable orders={[sampleWorkingOrder]} onSelectOrder={onSelect} />)

    // Truncated display text (8 chars + …)
    expect(screen.getByText('work-ord…')).toBeInTheDocument()

    // Title attribute for native hover tooltip
    const idWrapper = screen.getByTitle('work-ord-1234567890')
    expect(idWrapper).toBeInTheDocument()

    // Copy button exists with accessible label
    const copyBtn = screen.getByRole('button', { name: /Copy order ID/ })
    expect(copyBtn).toBeInTheDocument()
  })

  it('calls onSelectOrder when detail button is clicked', () => {
    const onSelect = vi.fn()
    render(<WorkingOrdersTable orders={[sampleWorkingOrder]} onSelectOrder={onSelect} />)

    fireEvent.click(screen.getByRole('button', { name: 'View' }))
    expect(onSelect).toHaveBeenCalledWith(sampleWorkingOrder)
  })
})
