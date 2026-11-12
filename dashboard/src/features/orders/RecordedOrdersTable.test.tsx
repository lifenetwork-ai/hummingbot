import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { components } from '../../core/api/schema'
import { RecordedOrdersTable } from './RecordedOrdersTable'

describe('RecordedOrdersTable', () => {
  const sampleOrder: components['schemas']['OrderViewOutput'] = {
    id: 'order-1234567890abcdef',
    exchange_order_id: 'ex-98765',
    connector: 'okx',
    market: 'spot',
    pair: 'LIFE-USDT',
    side: 'BUY',
    price: '0.045000',
    amount: '1000',
    recorded_filled_amount: '0',
    recorded_remaining_amount: '1000',
    status: 'OPEN',
    creation_pending: false,
    leverage: 1,
    order_type: 'LIMIT',
    position: null,
    progress_basis: 'recorded_fills_only',
    created_at: '2026-11-10T00:00:00Z',
    updated_at: '2026-11-10T00:00:00Z',
  }

  it('renders order ID truncated with full ID in title for hover and has copy button', () => {
    const onSelect = vi.fn()
    render(<RecordedOrdersTable orders={[sampleOrder]} onSelectOrder={onSelect} />)

    // Truncated display text
    expect(screen.getByText('order-12…')).toBeInTheDocument()

    // Title attribute for native hover tooltip
    const idWrapper = screen.getByTitle('order-1234567890abcdef')
    expect(idWrapper).toBeInTheDocument()

    // Copy button exists with accessible label
    const copyBtn = screen.getByRole('button', { name: /Copy order ID/ })
    expect(copyBtn).toBeInTheDocument()
  })

  it('calls onSelectOrder when detail button is clicked', () => {
    const onSelect = vi.fn()
    render(<RecordedOrdersTable orders={[sampleOrder]} onSelectOrder={onSelect} />)

    fireEvent.click(screen.getByRole('button', { name: 'View' }))
    expect(onSelect).toHaveBeenCalledWith(sampleOrder)
  })
})
