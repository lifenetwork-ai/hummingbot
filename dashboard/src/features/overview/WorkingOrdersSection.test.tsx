import { render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { WorkingOrdersSection } from './WorkingOrdersSection'
import { completionOverview } from '../../test/mocks/completionFixtures'

vi.mock('@tanstack/react-router', () => ({
  Link: ({ children, to, className }: { children: React.ReactNode; to?: string; className?: string }) => (
    <a href={to ?? '#'} className={className}>
      {children}
    </a>
  ),
}))

describe('WorkingOrdersSection presentation', () => {
  it('renders both BUY and SELL sides with status, pending, and reason codes', () => {
    render(<WorkingOrdersSection markets={completionOverview.markets} />)

    // Spot LIFE-USDT market
    expect(screen.getByText('LIFE-USDT')).toBeInTheDocument()

    // BUY side: short badge + detail in drawer
    expect(screen.getAllByText('Buy (Bid)').length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText('Sell (Ask)').length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText('BUY restricted')).toBeInTheDocument()
    expect(screen.getByText('inventory_restricted')).toBeInTheDocument()

    // Diagnosis guidance (in drawer)
    expect(screen.getAllByText(/Review inventory; do not relax guards/).length).toBeGreaterThanOrEqual(1)

    // Per-order acknowledged quantities are exact; tracker pending counts remain separate.
    const table = screen.getByRole('table')
    expect(within(table).getByText('SELL')).toBeInTheDocument()
    expect(within(table).getByText('200')).toBeInTheDocument()
    expect(within(table).getByText('20')).toBeInTheDocument()
    expect(screen.getByText('Pending create: 1 · Pending cancel: 0')).toBeInTheDocument()
    expect(screen.getByText('Pending create: 0 · Pending cancel: 1')).toBeInTheDocument()
  })

  it('does not derive remaining quantities when live rows are unavailable', () => {
    const markets = completionOverview.markets.map((market) => ({ ...market, workingOrders: null }))
    render(<WorkingOrdersSection markets={markets} />)
    expect(screen.getByText(/No current order rows in DTO/)).toBeInTheDocument()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })

  it('shows short PAUSED badges for both sides when idle, with detail in tooltip instead of long banners', () => {
    const idleMarkets = completionOverview.markets.map((market) => ({
      ...market,
      sides: market.sides.map((side) => ({
        ...side,
        activity: {
          ...side.activity,
          state: 'idle',
          detail: 'The market is not actively quoting.',
        },
        diagnosis: {
          stage: 'market_data',
          source: 'controller',
          error_type: null,
          observed_seconds: 15,
          next_action: 'Waiting for market data...',
        },
      })),
    }))
    const { container } = render(<WorkingOrdersSection markets={idleMarkets as unknown as Parameters<typeof WorkingOrdersSection>[0]['markets']} />)

    // Short PAUSED label for each side
    expect(screen.getAllByText('PAUSED').length).toBeGreaterThanOrEqual(2)

    // Detail only in tooltip (title), not in card surface
    expect(screen.queryByText('Both sides (BUY & SELL) are paused')).not.toBeInTheDocument()
    // Detailed text in drawer (details)
    const detailNodes = screen.getAllByText('The market is not actively quoting.')
    for (const node of detailNodes) {
      expect(node.closest('details')).not.toBeNull()
    }
    expect(container.querySelector('[title*="The market is not actively quoting."]')).not.toBeNull()
  })

  it('renders empty message when no markets exist', () => {
    render(<WorkingOrdersSection markets={[]} />)
    expect(screen.getByText('No current order observations.')).toBeInTheDocument()
  })
  it('preserves unknown pending counts rather than inventing zero', () => {
    const markets = completionOverview.markets.map((market) => ({ ...market,
      sides: market.sides.map((side) => ({ ...side, pendingCreateCount: null, pendingCancelCount: null })),
    }))
    render(<WorkingOrdersSection markets={markets} />)
    expect(screen.getAllByText('Pending create: — · Pending cancel: —')).toHaveLength(2)
    expect(screen.queryByText('Pending create: 0 · Pending cancel: 0')).not.toBeInTheDocument()
  })

})
