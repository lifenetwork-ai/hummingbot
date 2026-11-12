import type { summarizeReporting } from '../reporting/series'
import { Surface } from '../../shared/components/Surface'
import { formatPercentage, formatPrice, formatUsdt } from '../../shared/utils/formatters'

export interface DailySummaryMetricsProps { summary: ReturnType<typeof summarizeReporting>; isLoading?: boolean }
export function DailySummaryMetrics({ summary, isLoading }: DailySummaryMetricsProps) {
  return <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
    <Surface className="p-4"><span className="text-xs text-app-muted">Recorded Volume</span>
      <p className="mt-2 font-mono font-bold" title={summary.buy ?? undefined}>Buy: {isLoading ? '...' : formatUsdt(summary.buy)}</p>
      <p className="font-mono text-sm" title={summary.sell ?? undefined}>Sell: {formatUsdt(summary.sell)}</p>
      <span className="text-[11px] text-app-muted">{summary.fillCount ?? '—'}  fills; recorder scope</span>
    </Surface>
    <Surface className="p-4"><span className="text-xs text-app-muted">Fees Paid / Rebates by Currency</span>
      {summary.fees.length === 0 ? <p className="mt-2">—</p> : summary.fees.map((fee) => <p key={fee.currency} className="mt-2 font-mono text-xs">{fee.currency}: paid {formatPrice(fee.paid)} / rebate {formatPrice(fee.rebates)}</p>)}
      <span className="text-[11px] text-app-muted">Different currencies are not aggregated.</span>
    </Surface>
    <Surface className="p-4"><span className="text-xs text-app-muted">Funding Fee (Perpetual)</span>
      <p className="mt-2 font-mono text-sm" title={summary.fundingReceived ?? undefined}>Received: {formatUsdt(summary.fundingReceived)}</p>
      <p className="font-mono text-sm" title={summary.fundingPaid ?? undefined}>Paid: {formatUsdt(summary.fundingPaid)}</p>
      <span className="text-[11px] text-app-muted">Entries without a settlement basis remain unknown.</span>
    </Surface>
    <Surface className="p-4"><span className="text-xs text-app-muted">Fill Conversion Rate</span>
      <p className="mt-2 font-mono text-xl font-bold">{formatPercentage(summary.conversionFraction)}</p>
      <span className="text-[11px] text-app-muted">Converted fills / recorded fills ratio.</span>
    </Surface>
  </div>
}
