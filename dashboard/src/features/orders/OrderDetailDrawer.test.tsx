import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { components } from '../../core/api/schema'
import { OrderDetailDrawer } from './OrderDetailDrawer'

describe('OrderDetailDrawer', () => {
  const sampleOrder: components['schemas']['OrderViewOutput'] = {
    id: 'ord-internal-123456789',
    exchange_order_id: 'ex-exchange-987654321',
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

  it('renders internal order ID and exchange order ID with copy buttons and hover titles', () => {
    const onClose = vi.fn()
    render(<OrderDetailDrawer open={true} onClose={onClose} order={sampleOrder} />)

    // Check internal order ID
    expect(screen.getByText('ord-internal-123456789')).toBeInTheDocument()
    expect(screen.getByTitle('ord-internal-123456789')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Copy internal order ID/ })).toBeInTheDocument()

    // Check exchange order ID
    expect(screen.getByText('ex-exchange-987654321')).toBeInTheDocument()
    expect(screen.getByTitle('ex-exchange-987654321')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Copy exchange order ID/ })).toBeInTheDocument()
  })

  it('calls onClose when close button is clicked', () => {
    const onClose = vi.fn()
    render(<OrderDetailDrawer open={true} onClose={onClose} order={sampleOrder} />)

    fireEvent.click(screen.getByLabelText('Close order details'))
    expect(onClose).toHaveBeenCalled()
  })
})
