import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import * as history from '../features/history/useHistoryQueries'
import { OrdersPage } from './OrdersPage'

describe('OrdersPage', () => {
  it('renders orders page and allows tab switching', () => {
    const mockOrder = {
      id: 'ord-1',
      exchange_order_id: 'ex-1',
      connector: 'okx',
      market: 'spot' as const,
      pair: 'LIFE-USDT',
      side: 'BUY' as const,
      price: '0.045000',
      amount: '1000',
      recorded_filled_amount: '0',
      recorded_remaining_amount: '1000',
      status: 'BuyOrderCreated',
      creation_pending: false,
      leverage: 1,
      order_type: 'LIMIT',
      position: null,
      progress_basis: 'recorded_fills_only' as const,
      created_at: '2026-11-10T00:00:00Z',
      updated_at: '2026-11-10T00:00:00Z',
    }

    vi.spyOn(history, 'useOrders').mockReturnValue({
      data: {
        pages: [{ items: [mockOrder], next_cursor: null }],
        pageParams: [undefined],
      },
      isLoading: false,
      isFetchingNextPage: false,
      hasNextPage: false,
      fetchNextPage: vi.fn(),
      error: null,
    } as unknown as ReturnType<typeof history.useOrders>)

    vi.spyOn(history, 'useFills').mockReturnValue({
      data: {
        pages: [{ items: [], next_cursor: null }],
        pageParams: [undefined],
      },
      isLoading: false,
      isFetchingNextPage: false,
      hasNextPage: false,
      fetchNextPage: vi.fn(),
      error: null,
    } as unknown as ReturnType<typeof history.useFills>)

    vi.spyOn(history, 'useOrderDetail').mockReturnValue({
      data: null,
      isLoading: false,
      error: null,
    } as unknown as ReturnType<typeof history.useOrderDetail>)

    render(<OrdersPage />)

    expect(screen.getByText('Order Book & Trade History (Orders & Fills)')).toBeInTheDocument()
    expect(screen.getByText('Open Orders in Bot Data')).toBeInTheDocument()
    expect(screen.getByText('0.0450')).toBeInTheDocument()

    // Switch to Recorded Orders
    fireEvent.click(screen.getByRole('button', { name: /Recorded Order History/i }))
    expect(screen.getByText('Order ID')).toBeInTheDocument()

    // Switch to Fills
    fireEvent.click(screen.getByRole('button', { name: /Fill History/i }))
    expect(screen.getByText(/No fills recorded in this session./i)).toBeInTheDocument()
  })
})
