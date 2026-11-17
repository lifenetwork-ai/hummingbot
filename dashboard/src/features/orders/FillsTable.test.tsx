import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { components } from '../../core/api/schema'
import { FillsTable } from './FillsTable'

describe('FillsTable', () => {
  const sampleFill = {
    id: 'fill-1',
    order_id: 'order-abcdef1234567890',
    market: 'spot',
    pair: 'LIFE-USDT',
    side: 'BUY',
    price: '0.045000',
    amount: '500',
    fee_in_quote: '0.01',
    fee: { flat_fees: [] },
    timestamp: '2026-11-10T00:00:00Z',
  } as unknown as components['schemas']['FillViewOutput']

  it('uses the fill pair for base amount and preserves the fee token', () => {
    render(<FillsTable fills={[{ ...sampleFill, pair: 'BTC-USDT', amount: '0.00077758', fee: { fee_type: null, percent: '0', percent_token: null, flat_fees: [{ amount: '0.00000053', token: 'BTC' }] } }]} />)
    expect(screen.getByText('0.00077758 BTC')).toBeInTheDocument()
    expect(screen.getByText('0.00000053 BTC')).toBeInTheDocument()
    expect(screen.queryByText(/LIFE/)).not.toBeInTheDocument()
  })

  it('renders linked order ID truncated with full ID in title for hover and has copy button', () => {
    render(<FillsTable fills={[sampleFill]} />)

    // Truncated display text (10 chars + …)
    expect(screen.getByText('order-abcd…')).toBeInTheDocument()

    // Title attribute for native hover tooltip
    const idWrapper = screen.getByTitle('order-abcdef1234567890')
    expect(idWrapper).toBeInTheDocument()

    // Copy button exists with accessible label
    const copyBtn = screen.getByRole('button', { name: /Copy linked order ID/ })
    expect(copyBtn).toBeInTheDocument()
  })

  it('renders dash when order_id is missing', () => {
    const fillWithoutOrder = { ...sampleFill, order_id: '' }
    render(<FillsTable fills={[fillWithoutOrder]} />)

    expect(screen.getByText('—')).toBeInTheDocument()
  })
})
