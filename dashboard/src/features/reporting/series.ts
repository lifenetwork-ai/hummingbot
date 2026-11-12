import type { components } from '../../core/api/schema'
import { subtractDecimalStrings, sumDecimalStrings } from '../../shared/utils/decimal'
import type { buildReportingTimeline } from './model'

type Market = components['schemas']['DailyMarketOutput']
export function summarizeReporting(markets: Market[]) {
  const days = markets.flatMap((market) => market.days)
  const funding = markets.filter((market) => market.market === 'perpetual').flatMap((market) => market.days)
  const fees = new Map<string, { paid: string[]; rebates: string[] }>()
  for (const day of days) for (const fee of day.fees) {
    const entry = fees.get(fee.currency) ?? { paid: [], rebates: [] }
    entry.paid.push(fee.paid); entry.rebates.push(fee.rebates); fees.set(fee.currency, entry)
  }
  const fillCount = days.length ? days.reduce((sum, day) => sum + day.fill_count, 0) : null
  const converted = days.reduce((sum, day) => sum + day.converted_fill_count, 0)
  return {
    buy: sumDecimalStrings(days.map((day) => day.buy_volume_quote)),
    sell: sumDecimalStrings(days.map((day) => day.sell_volume_quote)),
    fundingPaid: sumDecimalStrings(funding.map((day) => day.funding_paid)),
    fundingReceived: sumDecimalStrings(funding.map((day) => day.funding_received)),
    fillCount,
    conversionFraction: fillCount ? converted / fillCount : null,
    fees: [...fees].map(([currency, values]) => ({ currency, paid: sumDecimalStrings(values.paid), rebates: sumDecimalStrings(values.rebates) })),
  }
}
export function buildChartSeries(timeline: ReturnType<typeof buildReportingTimeline>) {
  return timeline.map(({ day, markets }) => {
    const buckets = markets.map((market) => market.bucket)
    const buy = sumDecimalStrings(buckets.map((bucket) => bucket?.buy_volume_quote))
    const sell = sumDecimalStrings(buckets.map((bucket) => bucket?.sell_volume_quote))
    const perpetual = markets.filter((market) => market.market === 'perpetual')
    const fundingPaid = sumDecimalStrings(perpetual.map((market) => market.bucket?.funding_paid))
    const fundingReceived = sumDecimalStrings(perpetual.map((market) => market.bucket?.funding_received))
    return { day, buy, sell, total: sumDecimalStrings([buy, sell]),
      fills: buckets.length && buckets.every(Boolean) ? buckets.reduce((sum, bucket) => sum + (bucket?.fill_count ?? 0), 0) : null,
      fundingPaid, fundingReceived, fundingNet: subtractDecimalStrings(fundingReceived, fundingPaid) }
  })
}
export type ReportingSeries = ReturnType<typeof buildChartSeries>
