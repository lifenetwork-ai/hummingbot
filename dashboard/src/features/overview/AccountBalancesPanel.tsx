import { useState } from 'react'
import { Link } from '@tanstack/react-router'
import { ArrowUpRight, BarChart3, ChevronDown, LayoutList, RefreshCw, TrendingDown, TrendingUp } from 'lucide-react'
import type { ReadResponse } from '../../core/api/admin'
import { Badge } from '../../shared/components/Badge'
import { Dialog } from '../../shared/components/Dialog'
import { cn } from '../../shared/utils/cn'
import { formatQuantityForDisplay } from './formatNumbers'
import type { Source } from './model'
import { parseAmount } from './parseAmount'
import { AllocationBar } from './visuals/AllocationBar'

type Props = {
  open: boolean
  onClose: () => void
  source: Source<ReadResponse<'getBalances'>>
  onRefresh: () => unknown
}

type BalanceItem = ReadResponse<'getBalances'>['accounts'][number]['balances'][number]

function computeBalanceMetrics(balance: BalanceItem) {
  const cashBal = parseAmount(balance.cash_balance) ?? 0
  const availCash = parseAmount(balance.available_cash) ?? 0
  const frozenCash = Math.max(0, cashBal - availCash)
  const cashAvailRatio = cashBal > 0 ? availCash / cashBal : 0
  const cashFrozenRatio = cashBal > 0 ? frozenCash / cashBal : 0

  const eq = parseAmount(balance.equity) ?? 0
  const availEq = parseAmount(balance.available_equity) ?? 0
  const usedMargin = Math.max(0, eq - availEq)
  const eqAvailRatio = eq > 0 ? availEq / eq : 0
  const eqMarginRatio = eq > 0 ? usedMargin / eq : 0

  const hasPnl = balance.equity !== null && balance.cash_balance !== null
  const pnl = hasPnl ? eq - cashBal : null

  return {
    cashBal,
    availCash,
    frozenCash,
    cashAvailRatio,
    cashFrozenRatio,
    eq,
    availEq,
    usedMargin,
    eqAvailRatio,
    eqMarginRatio,
    hasPnl,
    pnl,
  }
}

function computeAccountSummary(balances: BalanceItem[]) {
  let totalCashUsdt = 0
  let totalAvailCashUsdt = 0
  let usdtFound = false
  let netPnl = 0
  let hasAnyPnl = false

  for (const b of balances) {
    const cash = parseAmount(b.cash_balance) ?? 0
    const avail = parseAmount(b.available_cash) ?? 0
    const eq = parseAmount(b.equity)

    if (b.currency.toUpperCase() === 'USDT') {
      usdtFound = true
      totalCashUsdt += cash
      totalAvailCashUsdt += avail
    }

    if (eq !== null && b.cash_balance !== null) {
      const diff = eq - cash
      if (Math.abs(diff) > 0.00001) {
        netPnl += diff
        hasAnyPnl = true
      }
    }
  }

  const usdtFreePct = totalCashUsdt > 0 ? Math.round((totalAvailCashUsdt / totalCashUsdt) * 100) : null

  return {
    count: balances.length,
    usdtFound,
    totalCashUsdt,
    totalAvailCashUsdt,
    usdtFreePct,
    netPnl,
    hasAnyPnl,
  }
}

export function AccountBalancesPanel({ open, onClose, source, onRefresh }: Props) {
  const [viewMode, setViewMode] = useState<'visual' | 'table'>('visual')
  const accounts = source.data?.accounts ?? []

  return (
    <Dialog
      open={open}
      onClose={onClose}
      placement="right"
      className="sm:max-w-5xl"
      title="Account balances"
      description="Balances in each currency’s native units."
    >
      {/* Top Snapshot / Refresh inline trigger */}
      <div className="flex flex-wrap items-center justify-between gap-3 text-xs text-app-muted">
        <button
          type="button"
          aria-label={source.refreshing ? 'Refreshing…' : 'Refresh'}
          disabled={source.refreshing}
          onClick={() => { void onRefresh() }}
          className="inline-flex min-h-8 items-center gap-2 rounded-sm text-xs text-app-muted transition-colors hover:text-app-action disabled:opacity-60 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-app-action"
          title="Click to refresh balances snapshot"
        >
          <RefreshCw size={13} className={cn(source.refreshing && 'animate-spin text-app-action')} aria-hidden="true" />
          <span>
            {source.refreshing
              ? 'Refreshing snapshot…'
              : source.data?.server_time
                ? `Snapshot: ${source.data.server_time}`
                : 'Snapshot time unavailable (click to refresh)'}
          </span>
        </button>
      </div>

      {/* Common App Tab Navigation */}
      {accounts.length > 0 && (
        <div className="mt-2 border-b border-app-border">
          <div className="flex space-x-2" role="tablist" aria-label="Balance view tabs">
            <button
              type="button"
              role="tab"
              aria-selected={viewMode === 'visual'}
              onClick={() => setViewMode('visual')}
              className={cn(
                'inline-flex items-center gap-2 border-b-2 px-4 py-2 text-xs font-bold transition-colors whitespace-nowrap',
                viewMode === 'visual'
                  ? 'border-app-action text-app-action'
                  : 'border-transparent text-app-muted hover:border-app-border hover:text-app-text',
              )}
            >
              <BarChart3 size={15} aria-hidden="true" />
              <span>Visual bars</span>
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={viewMode === 'table'}
              onClick={() => setViewMode('table')}
              className={cn(
                'inline-flex items-center gap-2 border-b-2 px-4 py-2 text-xs font-bold transition-colors whitespace-nowrap',
                viewMode === 'table'
                  ? 'border-app-action text-app-action'
                  : 'border-transparent text-app-muted hover:border-app-border hover:text-app-text',
              )}
            >
              <LayoutList size={15} aria-hidden="true" />
              <span>Table</span>
            </button>
          </div>
        </div>
      )}

      {source.phase === 'loading' && !source.data && <p role="status" className="mt-4 text-sm text-app-muted">Loading balances…</p>}
      {source.phase === 'error' && <p role="alert" className="mt-4 text-sm text-app-danger">Unable to refresh balances. {source.data ? 'Showing the last snapshot.' : 'Try refreshing again.'}</p>}
      {source.data && !source.data.owner_available && <p role="status" className="mt-4 text-sm text-app-muted">Balance source unavailable. Snapshot is unverified.</p>}
      {source.phase === 'ready' && accounts.length === 0 && <p className="mt-4 text-sm text-app-muted">No account balances reported.</p>}

      <div className="mt-5 space-y-8">
        {accounts.map((account, index) => {
          const summary = computeAccountSummary(account.balances)

          return (
            <section key={`${account.account_id ?? 'unknown'}-${index}`} aria-label={`Account ${account.account_id ?? 'unverified'}`}>
              <div className="mb-3 flex flex-wrap items-center justify-between gap-2 text-xs">
                <h3 className="break-all font-semibold text-app-text">Account {account.account_id ?? 'unverified'}</h3>
                <span className="rounded-pill bg-app-surface-muted px-2 py-1 text-app-muted">{account.environment}</span>
              </div>
              {!account.identity_verified && <p className="mb-3 text-xs text-app-muted">Account identity unverified</p>}

              {/* Account Quick Summary Cards */}
              {account.balances.length > 0 && (
                <div className="mb-4 grid grid-cols-2 gap-3 sm:grid-cols-3">
                  <div className="rounded-button border border-app-border/80 bg-app-surface-muted/30 p-2.5">
                    <div className="text-[10px] font-medium uppercase tracking-wider text-app-muted">Reported Assets</div>
                    <div className="mt-1 flex items-baseline gap-1.5">
                      <span className="font-mono text-sm font-bold text-app-text">{summary.count}</span>
                      <span className="text-[11px] text-app-muted">{summary.count === 1 ? 'currency' : 'currencies'}</span>
                    </div>
                  </div>

                  <div className="rounded-button border border-app-border/80 bg-app-surface-muted/30 p-2.5">
                    <div className="text-[10px] font-medium uppercase tracking-wider text-app-muted">
                      {summary.usdtFound ? 'USDT Free Capital' : 'Cash Liquidity'}
                    </div>
                    <div className="mt-1 flex items-baseline gap-1.5">
                      {summary.usdtFound && summary.usdtFreePct !== null ? (
                        <>
                          <span className="font-mono text-sm font-bold text-app-text">{summary.usdtFreePct}%</span>
                          <span className="text-[11px] text-app-muted">available</span>
                        </>
                      ) : (
                        <span className="font-mono text-sm font-semibold text-app-text">Active</span>
                      )}
                    </div>
                  </div>

                  <div className="col-span-2 rounded-button border border-app-border/80 bg-app-surface-muted/30 p-2.5 sm:col-span-1">
                    <div className="text-[10px] font-medium uppercase tracking-wider text-app-muted">Net Pos. PnL (Equity - Cash)</div>
                    <div className="mt-1 flex items-baseline gap-1.5">
                      {summary.hasAnyPnl ? (
                        <span className={cn('font-mono text-sm font-bold', summary.netPnl >= 0 ? 'text-app-success' : 'text-app-danger')}>
                          {summary.netPnl >= 0 ? '+' : ''}{formatQuantityForDisplay(String(summary.netPnl))}
                        </span>
                      ) : (
                        <span className="font-mono text-xs text-app-muted">Neutral / No open pos.</span>
                      )}
                    </div>
                  </div>
                </div>
              )}

              {account.balances.length === 0 && <p className="text-sm text-app-muted">No currencies reported.</p>}

              {/* VISUAL BARS VIEW (Default) */}
              {account.balances.length > 0 && viewMode === 'visual' && (
                <div className="space-y-3.5">
                  {account.balances.map((balance) => {
                    const stale = source.phase !== 'ready' || !source.data?.owner_available || balance.stale || !balance.present_in_latest_snapshot
                    const m = computeBalanceMetrics(balance)
                    const cashSegments = [
                      { key: 'avail', fraction: m.cashAvailRatio, label: `Available: ${formatQuantityForDisplay(balance.available_cash)}`, colorClass: 'bg-emerald-500' },
                      { key: 'frozen', fraction: m.cashFrozenRatio, label: `In Orders: ${formatQuantityForDisplay(String(m.frozenCash))}`, colorClass: 'bg-emerald-600/60', hatched: true },
                    ]
                    const equitySegments = [
                      { key: 'availEq', fraction: m.eqAvailRatio, label: `Free Eq: ${formatQuantityForDisplay(balance.available_equity)}`, colorClass: 'bg-sky-500' },
                      { key: 'margin', fraction: m.eqMarginRatio, label: `Used Margin: ${formatQuantityForDisplay(String(m.usedMargin))}`, colorClass: 'bg-sky-600/60', hatched: true },
                    ]

                    return (
                      <div key={`${balance.connector}-${balance.currency}`} className="rounded-button border border-app-border bg-app-surface p-3.5 shadow-sm">
                        <div className="mb-2.5 flex flex-wrap items-center justify-between gap-2 border-b border-app-border/40 pb-2">
                          <div className="flex items-center gap-2">
                            <span className="font-mono text-sm font-bold text-app-text">{balance.currency}</span>
                            <span className="rounded-pill bg-app-surface-muted px-2 py-0.5 text-[10px] font-medium text-app-muted">{balance.connector}</span>
                            {stale && <Badge tone="warning" className="px-2 py-0.5 text-[10px]">Stale / unverified</Badge>}
                          </div>
                          {m.hasPnl && m.pnl !== null && Math.abs(m.pnl) > 0.00001 && (
                            <Badge tone={m.pnl >= 0 ? 'success' : 'danger'} className="gap-1 font-mono text-xs">
                              {m.pnl >= 0 ? <TrendingUp size={12} aria-hidden="true" /> : <TrendingDown size={12} aria-hidden="true" />}
                              {m.pnl >= 0 ? '+' : ''}{formatQuantityForDisplay(String(m.pnl))} PnL
                            </Badge>
                          )}
                        </div>

                        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                          {/* Cash Pool */}
                          <div className="rounded-button border border-app-border/60 bg-app-surface-muted/20 p-2.5">
                            <div className="mb-1 flex items-baseline justify-between text-xs">
                              <span className="font-semibold text-app-text">Cash Balance</span>
                              <span className="font-mono font-bold text-app-text">{formatQuantityForDisplay(balance.cash_balance) ?? '—'}</span>
                            </div>
                            {m.cashBal > 0 ? (
                              <AllocationBar
                                segments={cashSegments}
                                className="mt-1.5"
                                aria-label={`Cash allocation for ${balance.currency}`}
                              />
                            ) : (
                              <p className="mt-1 text-[11px] text-app-muted">No cash balance recorded</p>
                            )}
                          </div>

                          {/* Equity Pool */}
                          <div className="rounded-button border border-app-border/60 bg-app-surface-muted/20 p-2.5">
                            <div className="mb-1 flex items-baseline justify-between text-xs">
                              <span className="font-semibold text-app-text">Total Equity</span>
                              <span className="font-mono font-bold text-app-text">{formatQuantityForDisplay(balance.equity) ?? '—'}</span>
                            </div>
                            {m.eq > 0 ? (
                              <AllocationBar
                                segments={equitySegments}
                                className="mt-1.5"
                                aria-label={`Equity allocation for ${balance.currency}`}
                              />
                            ) : (
                              <p className="mt-1 text-[11px] text-app-muted">No equity / derivative balance recorded</p>
                            )}
                          </div>
                        </div>
                      </div>
                    )
                  })}
                </div>
              )}

              {/* TABLE VIEW */}
              {account.balances.length > 0 && viewMode === 'table' && (
                <div
                  className="overflow-x-auto rounded-button border border-app-border focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-app-action"
                  role="region"
                  aria-label={`Currency balances for ${account.account_id ?? 'unverified account'}`}
                >
                  <table className="w-full min-w-[760px] text-xs">
                    <caption className="sr-only">Balances in native currency units</caption>
                    <thead className="bg-app-surface-muted text-app-muted">
                      <tr>
                        {['Currency', 'Cash balance', 'Available cash', 'Equity', 'Available equity'].map((label, colIndex) => (
                          <th
                            key={label}
                            scope="col"
                            className={`whitespace-nowrap px-3 py-3 font-medium ${colIndex === 0 ? 'text-left' : 'text-right'}`}
                          >
                            {label}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {account.balances.map((balance) => {
                        const stale = source.phase !== 'ready' || !source.data?.owner_available || balance.stale || !balance.present_in_latest_snapshot
                        const metrics = computeBalanceMetrics(balance)

                        return (
                          <tr key={`${balance.connector}-${balance.currency}`} className="border-t border-app-border align-top">
                            <th scope="row" aria-label={balance.currency} className="px-3 py-4 text-left font-semibold">
                              <div className="flex items-center gap-1.5">
                                <span>{balance.currency}</span>
                                {metrics.hasPnl && metrics.pnl !== null && Math.abs(metrics.pnl) > 0.00001 && (
                                  <span
                                    className={cn(
                                      'rounded-pill px-1.5 py-0.5 text-[10px] font-mono font-medium',
                                      metrics.pnl >= 0 ? 'bg-app-success/15 text-app-success' : 'bg-app-danger/15 text-app-danger',
                                    )}
                                    title={`Unrealized PnL: ${metrics.pnl >= 0 ? '+' : ''}${metrics.pnl}`}
                                  >
                                    {metrics.pnl >= 0 ? '+' : ''}{formatQuantityForDisplay(String(metrics.pnl))} PnL
                                  </span>
                                )}
                              </div>
                              {stale && <span className="mt-1 block whitespace-nowrap text-[10px] font-normal text-app-muted">Stale / unverified</span>}
                            </th>
                            {[balance.cash_balance, balance.available_cash, balance.equity, balance.available_equity].map((value, cellIndex) => (
                              <td key={cellIndex} className="whitespace-nowrap px-3 py-4 text-right font-mono tabular-nums">
                                {formatQuantityForDisplay(value) ?? '—'}
                              </td>
                            ))}
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                </div>
              )}

              {/* OBSERVATION METADATA (Collapsible Details) */}
              <details className="group mt-3.5 rounded-button border border-app-border/70 bg-app-surface-muted/20 p-2.5 text-xs">
                <summary className="flex cursor-pointer list-none items-center justify-between font-medium text-app-muted hover:text-app-text [&::-webkit-details-marker]:hidden">
                  <span>Observation &amp; sync details ({account.balances.length} items)</span>
                  <ChevronDown size={14} className="text-app-muted transition-transform group-open:rotate-180" aria-hidden="true" />
                </summary>
                <ul className="mt-2.5 space-y-1.5 border-t border-app-border/40 pt-2 text-[11px] leading-relaxed text-app-muted" aria-label="Balance observation details">
                  {account.balances.map((balance) => (
                    <li key={`${balance.connector}-${balance.currency}`} className="break-words">
                      <span className="font-semibold text-app-text">{balance.currency}</span> · Observed at: {balance.observed_at ?? '—'} · Connector: {balance.connector ?? '—'} · Source: {balance.source ?? '—'} · In latest snapshot: {balance.present_in_latest_snapshot ? 'Yes' : 'No'}
                    </li>
                  ))}
                </ul>
              </details>
            </section>
          )
        })}
        <div className="mt-4 flex flex-wrap items-center justify-between gap-2 border-t border-app-border/80 pt-3 text-xs">
          <span className="text-app-muted">Track historical balance changes &amp; PnL over time:</span>
          <Link
            to="/statistics"
            onClick={onClose}
            className="inline-flex items-center gap-1 font-semibold text-app-action hover:underline focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-app-action rounded-xs"
            aria-label="Open Daily Balances Chart in Statistics"
          >
            <span>Daily Balances Chart</span>
            <ArrowUpRight size={12} />
          </Link>
        </div>
      </div>
    </Dialog>
  )
}
