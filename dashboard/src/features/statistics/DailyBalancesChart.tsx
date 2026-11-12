import { Wallet } from 'lucide-react'
import type { components } from '../../core/api/schema'
import { Surface } from '../../shared/components/Surface'
import { formatPrice, formatUsdt } from '../../shared/utils/formatters'

export interface DailyBalancesChartProps {
  balances: components['schemas']['DailyBalanceOutput'][]
  isLoading?: boolean
}

export function DailyBalancesChart({ balances, isLoading }: DailyBalancesChartProps) {
  return (
    <Surface className="p-5 md:p-6">
      <div className="flex items-center justify-between border-b border-app-border/80 pb-3">
        <div className="flex items-center gap-2">
          <Wallet size={18} className="text-app-action" />
          <h3 className="font-heavy text-base font-bold text-app-text">
            Daily Account Balance Fluctuations (Daily Balances)
          </h3>
        </div>
        <span className="text-[11px] text-app-muted">Shared wallets not aggregated</span>
      </div>

      <div className="mt-5">
        {isLoading ? (
          <div className="p-8 text-center text-xs text-app-muted">Loading balance history...</div>
        ) : balances.length === 0 ? (
          <div className="p-8 text-center text-xs text-app-muted">
            No historical balance data for the selected date range.
          </div>
        ) : (
          <div className="overflow-x-auto rounded-card border border-app-border">
            <table className="w-full min-w-[600px] text-left text-xs">
              <thead>
                <tr className="border-b border-app-border bg-app-surface-muted/60 text-app-muted font-semibold">
                  <th className="p-2.5">Day</th>
                  <th className="p-2.5">Account</th>
                  <th className="p-2.5">Currency</th>
                  <th className="p-2.5">Cash</th>
                  <th className="p-2.5">Equity</th>
                  <th className="p-2.5">Available Equity</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-app-border/60">
                {balances.map((b, idx) => (
                  <tr key={`${b.day}-${b.currency}-${idx}`} className="hover:bg-app-surface-muted/30">
                    <td className="p-2.5 font-mono text-app-muted">{b.day}</td>
                    <td className="p-2.5 font-mono text-app-text">{b.account_id}</td>
                    <td className="p-2.5 font-bold">{b.currency}</td>
                    <td className="p-2.5 font-mono">
                      {b.currency === 'USDT' ? formatUsdt(b.cash_balance) : `${formatPrice(b.cash_balance)} ${b.currency}`}
                    </td>
                    <td className="p-2.5 font-mono font-bold text-app-text">
                      {b.currency === 'USDT' ? formatUsdt(b.equity) : `${formatPrice(b.equity)} ${b.currency}`}
                    </td>
                    <td className="p-2.5 font-mono text-app-muted">
                      {b.currency === 'USDT' ? formatUsdt(b.available_equity) : `${formatPrice(b.available_equity)} ${b.currency}`}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </Surface>
  )
}
