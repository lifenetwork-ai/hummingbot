import { useState } from 'react'
import { ChevronDown, Coins } from 'lucide-react'
import type { components } from '../../core/api/schema'
import { AllocationBar } from './visuals/AllocationBar'
import { InfoStatusBadge } from './visuals/InfoStatusBadge'
import { CopyableId } from '../../shared/components/CopyableId'
import { Surface } from '../../shared/components/Surface'
import { cn } from '../../shared/utils/cn'
import { formatAssetAmount } from '../../shared/utils/formatters'

function currencyColor(currency: string): string {
  if (currency === 'USDT') return 'bg-emerald-500'
  if (currency === 'BTC') return 'bg-amber-400'
  if (currency === 'ETH') return 'bg-app-action'
  return 'bg-slate-500'
}

export interface SharedBalancesCardProps {
  balances?: components['schemas']['BalancesResponseOutput'] | null
  available: boolean
  isLoading?: boolean
  error?: Error | null
  className?: string
}


/** Display backend account snapshots without filling unknown values or combining different asset units. */
export function SharedBalancesCard({ balances, available, isLoading, error, className }: SharedBalancesCardProps) {
  const accounts = balances?.accounts ?? []
  const [hideZero, setHideZero] = useState(false)
  const currentSnapshotAvailable = available && !error

  return (
    <Surface className={cn('flex h-full flex-col p-4 sm:p-5', className)}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="font-heavy text-base font-bold text-app-text">Shared Balances</h3>
        <InfoStatusBadge
          label={isLoading ? 'SYNCING' : error ? 'API ERROR' : !available ? 'NO DATA' : 'SHARED WALLET'}
          tone={error || !available ? 'warning' : 'neutral'}
          detail={isLoading ? 'Updating balances' : error ? 'The balances request failed; retained values are marked stale' : !available ? 'Balances not available from backend' : 'Markets share a unified wallet; balances are not double-counted'}
        />
      </div>

      {error && (
        <p role="alert" className="mt-3 rounded-card bg-app-surface-muted/30 p-3 text-xs text-app-danger">
          Could not refresh balances: {error.message}.{' '}
          {accounts.length > 0 ? 'Showing the last received values as stale.' : 'No balance observation is available.'}
        </p>
      )}

      {isLoading && accounts.length === 0 ? (
        <div className="mt-4 h-10 animate-pulse rounded-pill bg-app-surface-muted" />
      ) : accounts.length === 0 ? (
        <div className="mt-4 rounded-card bg-app-surface-muted/30 p-4 text-center text-xs text-app-muted">
          No balance data returned from exchange.
        </div>
      ) : (
        <div className="mt-4 space-y-4">
          {accounts.map((account) => {
            const sortedBalances = [...(account.balances ?? [])].sort((a, b) => {
              if (a.currency === 'USDT') return -1
              if (b.currency === 'USDT') return 1
              return a.currency.localeCompare(b.currency)
            })

            const usdt = sortedBalances.find((b) => b.currency === 'USDT')
            const usdtCash = usdt?.cash_balance == null || usdt.cash_balance === '' ? null : Number(usdt.cash_balance)
            const usdtAvail = usdt?.available_cash == null || usdt.available_cash === '' ? null : Number(usdt.available_cash)
            const validUsdtAmounts = usdtCash !== null && usdtAvail !== null && Number.isFinite(usdtCash) && Number.isFinite(usdtAvail)
            const usdtUnavailable = validUsdtAmounts ? Math.max(0, usdtCash - usdtAvail) : null
            const usdtSnapshotCurrent = currentSnapshotAvailable && usdt !== undefined && !usdt.stale && usdt.present_in_latest_snapshot

            return (
              <div key={account.account_id || account.environment} className="space-y-3">
                {/* Compare only known USDT cash values; the difference is not classified as orders or margin. */}
                {usdtSnapshotCurrent && validUsdtAmounts && usdtCash > 0 && usdtAvail <= usdtCash && (
                  <>
                    <AllocationBar
                      aria-label={`USDT cash: available ${usdtAvail} / cash unavailable ${usdtUnavailable}`}
                      segments={[
                        { key: 'usdt-free', fraction: usdtAvail!, colorClass: 'bg-emerald-500', label: `Available ${formatAssetAmount(usdtAvail, 'USDT')}` },
                        ...(usdtUnavailable! > 0
                          ? [{ key: 'usdt-unavailable', fraction: usdtUnavailable!, colorClass: 'bg-amber-400', hatched: true, label: `Cash unavailable ${formatAssetAmount(usdtUnavailable, 'USDT')}` }]
                          : []),
                      ]}
                    />
                    <p className="text-[10px] text-app-muted">Cash minus available cash; backend does not classify the difference as orders or margin.</p>
                  </>
                )}

                {/* Detailed breakdown accordion */}
                <details className="group rounded-card bg-app-surface-muted/20 p-2.5 text-xs">
                  <summary className="flex cursor-pointer select-none items-center justify-between text-app-muted hover:text-app-text">
                    <span className="flex items-center gap-1.5">
                      <Coins size={12} className="text-app-action" />
                      <span>Detailed breakdown ({sortedBalances.filter((b) => !hideZero || b.cash_balance == null || b.equity == null || Number(b.cash_balance) > 0 || Number(b.equity) > 0).length} assets)</span>
                    </span>
                    <ChevronDown size={13} className="transition-transform group-open:rotate-180" />
                  </summary>
                  <div className="mt-2 space-y-2 border-t border-app-border/40 pt-2">
                    <div className="flex items-center justify-between text-[10px] text-app-muted">
                      {account.account_id ? (
                        <span className="flex items-center gap-1">
                          Account <CopyableId id={account.account_id} truncateLength={12} copyLabel="Copy account ID" />
                        </span>
                      ) : (
                        <span>OKX Account</span>
                      )}
                      <span className={cn('font-mono font-semibold', account.environment === 'live' ? 'text-app-danger' : 'text-app-muted')}>
                        {account.environment.toUpperCase()}
                      </span>
                    </div>
                    <button
                      type="button"
                      onClick={() => setHideZero(!hideZero)}
                      className="text-[10px] text-app-action hover:underline"
                    >
                      {hideZero ? 'Show all' : 'Hide zero balances'}
                    </button>
                    <div className="overflow-x-auto">
                      <table className="w-full min-w-[300px] text-left text-[11px]">
                        <thead className="text-[10px] text-app-muted">
                          <tr>
                            <th className="py-1 pr-2">Asset</th>
                            <th className="py-1 pr-2">Cash</th>
                            <th className="py-1 pr-2">Available</th>
                            <th className="py-1 text-right">Equity</th>
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-app-border/30 font-mono">
                          {sortedBalances
                            .filter((b) => {
                              if (!hideZero) return true
                              if (b.cash_balance == null || b.equity == null) return true
                              const c = Number(b.cash_balance)
                              const e = Number(b.equity)
                              return !Number.isFinite(c) || !Number.isFinite(e) || c > 0 || e > 0
                            })
                            .map((b) => (
                              <tr key={b.currency}>
                                <td className="py-1.5 pr-2 font-sans font-bold">
                                  <span className="flex items-center gap-1.5">
                                    <span className={cn('h-2 w-2 rounded-full', currencyColor(b.currency))} />
                                    {b.currency}
                                    {(!currentSnapshotAvailable || b.stale || !b.present_in_latest_snapshot) && <span className="text-[9px] font-medium text-amber-500">stale</span>}
                                  </span>
                                </td>
                                <td className="py-1.5 pr-2 text-app-text">{formatAssetAmount(b.cash_balance, b.currency)}</td>
                                <td className="py-1.5 pr-2 font-bold text-emerald-500">{formatAssetAmount(b.available_cash, b.currency)}</td>
                                <td className="py-1.5 text-right text-app-text">{formatAssetAmount(b.equity, b.currency)}</td>
                              </tr>
                            ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                </details>
              </div>
            )
          })}
        </div>
      )}
    </Surface>
  )
}
