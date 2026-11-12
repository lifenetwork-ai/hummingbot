import { buildChartSeries } from '../reporting/series'
import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { DailyFundingChart } from './DailyFundingChart'
import type { components } from '../../core/api/schema'

function toSeries(markets: components['schemas']['DailyMarketOutput'][]) {
  return buildChartSeries([...new Set(markets.flatMap((market) => market.days.map((bucket) => bucket.day)))].sort().map((day) => ({ day, balances: [], markets: markets.map((market) => ({ market: market.market, connector: market.connector, bucket: market.days.find((bucket) => bucket.day === day) ?? null })) })))
}

describe('DailyFundingChart', () => {
  const mockMarkets = [
    {
      connector: 'binance_perpetual',
      market: 'perpetual',
      funding_status: 'observed_only',
      days: [
        {
          day: '2026-11-08',
          buy_volume_quote: '0',
          sell_volume_quote: '0',
          buy_volume_base: '0',
          sell_volume_base: '0',
          percentage_fee_fill_count: 0,
          unconverted_fill_count: 0,
          source: 'recorder_tables',
          converted_fill_count: 0,
          fill_count: 0,
          fee_in_usdt: '0',
          fees: [],
          funding_paid: '5.20',
          funding_received: '18.40',
          last_event_at: '2026-11-08T23:59:00Z',
          observed_at: '2026-11-08T23:59:00Z',
        },
        {
          day: '2026-11-09',
          buy_volume_quote: '0',
          sell_volume_quote: '0',
          buy_volume_base: '0',
          sell_volume_base: '0',
          percentage_fee_fill_count: 0,
          unconverted_fill_count: 0,
          source: 'recorder_tables',
          converted_fill_count: 0,
          fill_count: 0,
          fee_in_usdt: '0',
          fees: [],
          funding_paid: '24.10',
          funding_received: '4.10',
          last_event_at: '2026-11-09T23:59:00Z',
          observed_at: '2026-11-09T23:59:00Z',
        },
      ],
    },
  ] as unknown as components['schemas']['DailyMarketOutput'][]

  it('renders loading state when isLoading is true', () => {
    render(<DailyFundingChart series={toSeries([])} isLoading={true} />)
    expect(screen.getByText('Loading funding data...')).toBeInTheDocument()
  })

  it('renders empty state when there is no perpetual market or days', () => {
    render(<DailyFundingChart series={toSeries([])} isLoading={false} />)
    expect(screen.getByText('No funding rate data within the observed range.')).toBeInTheDocument()
  })

  it('renders vertical bipolar chart with dates and funding values', () => {
    render(<DailyFundingChart series={toSeries(mockMarkets)} />)

    expect(screen.getByText('Daily Funding Payments')).toBeInTheDocument()
    expect(screen.getByText('Net income (+)')).toBeInTheDocument()
    expect(screen.getByText('Net paid (-)')).toBeInTheDocument()

    // Day labels
    expect(screen.getByText('08/11')).toBeInTheDocument()
    expect(screen.getByText('09/11')).toBeInTheDocument()

    // Inspection banner shows latest day (2026-11-09) with negative net: -20.00 USDT
    expect(screen.getAllByText('2026-11-09').length).toBeGreaterThan(0)
    expect(screen.getAllByText('-20.00 USDT').length).toBeGreaterThan(0)
  })

  it('updates inspection banner on hover', () => {
    render(<DailyFundingChart series={toSeries(mockMarkets)} />)

    const day8Btn = screen.getByRole('button', { name: /Funding for 2026-11-08/i })
    fireEvent.mouseEnter(day8Btn)

    // Positive net: 18.40 - 5.20 = +13.20 USDT
    expect(screen.getAllByText('+13.20 USDT').length).toBeGreaterThan(0)
  })

  it('renders a floating tooltip directly next to the hovered column', () => {
    render(<DailyFundingChart series={toSeries(mockMarkets)} />)

    expect(screen.queryByRole('tooltip')).not.toBeInTheDocument()

    const day8Btn = screen.getByRole('button', { name: /Funding for 2026-11-08/i })
    fireEvent.mouseEnter(day8Btn)

    const tooltip = screen.getByRole('tooltip')
    expect(tooltip).toBeInTheDocument()
    expect(tooltip).toHaveTextContent('2026-11-08')
    expect(tooltip).toHaveTextContent('+13.20 USDT')
    expect(tooltip).toHaveTextContent('18.40 USDT')
    expect(tooltip).toHaveTextContent('5.20 USDT')

    fireEvent.mouseLeave(day8Btn)
    expect(screen.queryByRole('tooltip')).not.toBeInTheDocument()
  })
})
