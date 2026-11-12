import { buildChartSeries } from '../reporting/series'
import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { DailyVolumeChart } from './DailyVolumeChart'
import type { components } from '../../core/api/schema'

function toSeries(markets: components['schemas']['DailyMarketOutput'][]) {
  return buildChartSeries([...new Set(markets.flatMap((market) => market.days.map((bucket) => bucket.day)))].sort().map((day) => ({ day, balances: [], markets: markets.map((market) => ({ market: market.market, connector: market.connector, bucket: market.days.find((bucket) => bucket.day === day) ?? null })) })))
}

describe('DailyVolumeChart', () => {
  const mockMarkets = [
    {
      connector: 'okx',
      market: 'spot',
      funding_status: 'not_applicable',
      days: [
        {
          day: '2026-11-08',
          buy_volume_quote: '12000.50',
          sell_volume_quote: '8000.50',
          buy_volume_base: '100',
          sell_volume_base: '80',
          percentage_fee_fill_count: 24,
          unconverted_fill_count: 0,
          source: 'recorder_tables',
          converted_fill_count: 24,
          fill_count: 24,
          fee_in_usdt: '12.0',
          fees: [],
          funding_paid: null,
          funding_received: null,
          last_event_at: '2026-11-08T23:59:00Z',
          observed_at: '2026-11-08T23:59:00Z',
        },
        {
          day: '2026-11-09',
          buy_volume_quote: '25000.00',
          sell_volume_quote: '15000.00',
          buy_volume_base: '200',
          sell_volume_base: '150',
          percentage_fee_fill_count: 50,
          unconverted_fill_count: 0,
          source: 'recorder_tables',
          converted_fill_count: 50,
          fill_count: 50,
          fee_in_usdt: '25.0',
          fees: [],
          funding_paid: null,
          funding_received: null,
          last_event_at: '2026-11-09T23:59:00Z',
          observed_at: '2026-11-09T23:59:00Z',
        },
      ],
    },
  ] as unknown as components['schemas']['DailyMarketOutput'][]

  it('renders loading state when isLoading is true', () => {
    render(<DailyVolumeChart series={toSeries([])} isLoading={true} />)
    expect(screen.getByText('Loading volume chart...')).toBeInTheDocument()
  })

  it('renders empty state when there are no days in markets', () => {
    render(<DailyVolumeChart series={toSeries([])} isLoading={false} />)
    expect(screen.getByText('No volume data for the selected date range.')).toBeInTheDocument()
  })

  it('renders vertical stacked bar chart with compact totals and dates', () => {
    render(<DailyVolumeChart series={toSeries(mockMarkets)} />)

    // Header and title
    expect(screen.getByText('Daily Trading Volume Chart')).toBeInTheDocument()
    expect(screen.getAllByText(/Buy Volume/i).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/Sell Volume/i).length).toBeGreaterThan(0)

    // Compact day labels (08/11 and 09/11)
    expect(screen.getByText('08/11')).toBeInTheDocument()
    expect(screen.getByText('09/11')).toBeInTheDocument()

    // Compact top numbers: 20K (12K + 8K = 20K) and 40K (25K + 15K = 40K)
    expect(screen.getByText('20K')).toBeInTheDocument()
    expect(screen.getByText('40K')).toBeInTheDocument()

    // In-column numbers: 12K, 8K, 25K, 15K
    expect(screen.getByText('12K')).toBeInTheDocument()
    expect(screen.getByText('8K')).toBeInTheDocument()
    expect(screen.getByText('25K')).toBeInTheDocument()
    expect(screen.getByText('15K')).toBeInTheDocument()

    // Inspection banner displays detailed numbers for active day (defaults to latest 2026-11-09)
    expect(screen.getAllByText('2026-11-09').length).toBeGreaterThan(0)
    expect(screen.getAllByText('40,000.00 USDT').length).toBeGreaterThan(0)
    expect(screen.getAllByText('25,000.00 USDT').length).toBeGreaterThan(0)
    expect(screen.getAllByText('15,000.00 USDT').length).toBeGreaterThan(0)
    expect(screen.getByText('(50 total fills)')).toBeInTheDocument()
  })

  it('updates inspection banner on hover or click of a bar', () => {
    render(<DailyVolumeChart series={toSeries(mockMarkets)} />)

    // Find the button for 2026-11-08
    const barDay8 = screen.getByRole('button', { name: /Volume for 2026-11-08/i })
    expect(barDay8).toBeInTheDocument()

    // Hover on Day 8
    fireEvent.mouseEnter(barDay8)
    expect(screen.getAllByText('20,001.00 USDT').length).toBeGreaterThan(0)
    expect(screen.getAllByText('12,000.50 USDT').length).toBeGreaterThan(0)
    expect(screen.getAllByText('8,000.50 USDT').length).toBeGreaterThan(0)
    expect(screen.getByText('(24 total fills)')).toBeInTheDocument()

    // Mouse leave
    fireEvent.mouseLeave(barDay8)

    // Click to select Day 8
    fireEvent.click(barDay8)
    expect(screen.getAllByText('20,001.00 USDT').length).toBeGreaterThan(0)
  })

  it('renders expandable data table with complete day rows', () => {
    render(<DailyVolumeChart series={toSeries(mockMarkets)} />)

    expect(screen.getByText(/Detailed metrics table \(2 days\)/i)).toBeInTheDocument()
    expect(screen.getByText('Buy / Sell Ratio')).toBeInTheDocument()
  })

  it('renders a floating tooltip directly next to the hovered column', () => {
    render(<DailyVolumeChart series={toSeries(mockMarkets)} />)
    expect(screen.queryByRole('tooltip')).not.toBeInTheDocument()

    const barDay8 = screen.getByRole('button', { name: /Volume for 2026-11-08/i })
    fireEvent.mouseEnter(barDay8)

    const tooltip = screen.getByRole('tooltip')
    expect(tooltip).toBeInTheDocument()
    expect(within(tooltip).getByText('2026-11-08')).toBeInTheDocument()
    expect(within(tooltip).getByText(/12,000.50 USDT/)).toBeInTheDocument()
    expect(within(tooltip).getByText(/8,000.50 USDT/)).toBeInTheDocument()
    expect(within(tooltip).getByText('20,001.00 USDT')).toBeInTheDocument()
    expect(within(tooltip).getByText('(24 fills)')).toBeInTheDocument()

    fireEvent.mouseLeave(barDay8)
    expect(screen.queryByRole('tooltip')).not.toBeInTheDocument()
  })
})
