import { useState } from 'react'
import { ArrowUpRight } from 'lucide-react'
import { Badge } from '../../shared/components/Badge'
import { Surface } from '../../shared/components/Surface'
import { cn } from '../../shared/utils/cn'
import { formatAssetAmount } from '../../shared/utils/formatters'
import { BackendGapChip } from './BackendGapChip'
import { parseAmount } from './parseAmount'
import { formatPriceForDisplay, formatUsdtForDisplay } from './formatNumbers'
import type { DetailMode } from './LiquidityVerdictCard'
import type { CapitalRiskView, MandateLimitRow } from './supplement'

/**
 * Q5 — "Vốn đang được dùng thế nào, chi phí phát sinh ở đâu, rủi ro còn trong giới hạn không?"
 * Portfolio/fees/benchmark từ supplement; thiếu → chip "chưa có dữ liệu" (không hiện số giả).
 */
export function CapitalRiskCard({ view, mode, riskLimits = [], onOpenRiskGuards }: { view: CapitalRiskView; mode: DetailMode; riskLimits?: MandateLimitRow[]; onOpenRiskGuards?: () => void }) {
  const c = view
  const [rulesOpen, setRulesOpen] = useState(false)

  return (
    <div id="capital-risk-card" className="grid scroll-mt-24 grid-cols-1 gap-3 lg:grid-cols-3">
      {/* Vị thế & tồn kho */}
      <Surface className="p-4">
        <h3 className="text-xs font-bold uppercase tracking-wider text-app-muted">Positions &amp; inventory</h3>
        {c.positions.length === 0 && (
          <p className="mt-2 text-xs text-app-muted">No perpetual positions — spot only carries inventory, not positions.</p>
        )}
        {c.positions.map((p) => {
          const entryNum = parseAmount(p.entry)
          const markNum = null // mark price chưa có trong positions contract — dùng PnL có sẵn của sàn
          const liqNum = parseAmount(p.liqPrice)
          const liqDistPct = entryNum !== null && liqNum !== null && entryNum > 0 ? (Math.abs(entryNum - liqNum) / entryNum) * 100 : null
          const entryDisplay = formatPriceForDisplay(p.entry)
          const liqDisplay = formatPriceForDisplay(p.liqPrice)
          const pnlDisplay = formatUsdtForDisplay(p.pnlUsdt, true)
          const marginDisplay = formatUsdtForDisplay(p.marginUsdt)
          const amountDisplay = formatAssetAmount(p.amount, p.pair.split('-')[0])
          const limitDisplay = formatAssetAmount(p.inventoryLimit, p.pair.split('-')[0])
          const amountNum = parseAmount(p.amount) ?? 0
          const limitNum = parseAmount(p.inventoryLimit) ?? 0
          const inventoryPct = limitNum > 0 ? Math.min(100, (amountNum / limitNum) * 100) : null
          return (
            <div key={`${p.market}-${p.pair}-${p.side}`} className="mt-2 text-xs">
              <div className="flex items-baseline justify-between gap-2">
                <span className="font-semibold text-app-text">{p.market} · <span className={p.side === 'LONG' ? 'text-emerald-500' : 'text-rose-500'}>{p.side}</span></span>
                <span className="font-mono text-base font-bold text-app-text">{amountDisplay ?? '—'}</span>
              </div>
              <div className="mt-1.5 space-y-0.5 font-mono text-[10px] text-app-muted">
                <p>
                  entry {entryDisplay ?? '—'}
                  {markNum !== null && <> → mark <span className="text-app-text">{markNum}</span></>}
                </p>
                <p>
                  PnL <span className={cn('font-bold', (parseAmount(p.pnlUsdt) ?? 0) >= 0 ? 'text-emerald-500' : 'text-app-danger')}>{pnlDisplay ?? '—'}</span>
                  {' '}· margin <span className="text-app-text">{marginDisplay ?? '—'}</span>
                </p>
                <p>
                  liquidation {liqDisplay !== null ? `~${liqDisplay}` : '—'}
                  {liqDistPct !== null && <span className="text-amber-400"> ({liqDistPct.toFixed(1)}% from entry)</span>}
                </p>
              </div>
              {/* Mức dùng hạn mức tồn kho */}
              <div className="mt-2">
                <div className="flex items-baseline justify-between text-[10px] text-app-muted">
                  <span>Position / side cap</span>
                  <span className="font-mono text-app-text">
                    {amountDisplay ?? '—'} / {limitDisplay ?? '—'}
                    {inventoryPct !== null ? ` (${Math.round(inventoryPct)}%)` : ''}
                  </span>
                </div>
                {inventoryPct !== null && (
                  <div className="mt-1 h-1.5 w-full overflow-hidden rounded-pill bg-app-surface-muted" aria-hidden="true">
                    <div
                      className={cn('h-full', inventoryPct > 80 ? 'bg-app-warning' : 'bg-app-action')}
                      style={{ width: `${inventoryPct}%` }}
                    />
                  </div>
                )}
              </div>
            </div>
          )
        })}
      </Surface>

      {/* Vốn */}
      <Surface className="p-4">
        <h3 className="text-xs font-bold uppercase tracking-wider text-app-muted">Free &amp; order-reserved capital</h3>
        {c.balances.length === 0 ? (
          <p className="mt-2 flex items-center gap-1.5 text-xs text-app-muted">
            Balance breakdown <BackendGapChip detail="Reserved balances data unavailable (OverviewSupplement.balances)." />
          </p>
        ) : (
          <ul className="mt-2 space-y-2 text-xs">
            {c.balances.map((b) => (
              <li key={b.asset}>
                <div className="flex items-baseline justify-between">
                  <span className="font-semibold text-app-text">{b.asset}</span>
                  <span className="font-mono font-bold tabular-nums text-app-text">{b.free ?? '—'}</span>
                </div>
                <div className="mt-0.5 flex items-center gap-1.5 text-[10px] text-app-muted">
                  <span>reserved for orders</span>
                  <div className="h-1.5 w-16 overflow-hidden rounded-pill bg-app-surface-muted" aria-hidden="true">
                    <div
                      className="h-full bg-app-action/70"
                      style={{
                        width: `${((parseAmount(b.reservedForOrders) ?? 0) / Math.max((parseAmount(b.free) ?? 0) + (parseAmount(b.reservedForOrders) ?? 0), 1)) * 100}%`,
                      }}
                    />
                  </div>
                  <span className="font-mono">{b.reservedForOrders ?? '—'}</span>
                </div>
              </li>
            ))}
          </ul>
        )}
        <div className="mt-3 space-y-1.5 border-t border-app-border/40 pt-2 text-[10px] text-app-muted">
          <p className="flex items-center justify-between gap-1.5">
            <span className="flex items-center gap-1.5">
              {c.fees.available
                ? `Session fees${c.fees.stale ? ' · stale' : ''}${c.fees.value.processing_state === 'catching_up' ? ' · catching up' : ''}${!c.fees.value.history_complete ? ' · history incomplete' : ''}`
                : 'Session fees'}
              {!c.fees.available && <BackendGapChip detail={c.fees.reason ?? 'Aggregate session fees unavailable (OverviewSupplement.session.fees).'} />}
            </span>
            <span className="font-mono font-bold text-app-text">{c.fees.available ? `${c.fees.value.total_usdt} USDT` : '—'}</span>
          </p>
          <p className="flex items-center justify-between gap-1.5">
            <span className="flex items-center gap-1.5">
              {c.holdBenchmark.available && c.holdBenchmark.stale ? 'Session vs hold (stale)' : 'Session vs hold'}
              {!c.holdBenchmark.available && <BackendGapChip detail={c.holdBenchmark.reason ?? 'Hold benchmark unavailable (OverviewSupplement.session.benchmark_hold).'} />}
            </span>
            <span className="font-mono font-bold text-app-text">
              {c.holdBenchmark.available ? (
                <>
                  {c.holdBenchmark.value.session_pnl_usdt} <span className="text-app-muted">vs</span> {c.holdBenchmark.value.hold_pnl_usdt}
                </>
              ) : (
                '—'
              )}
            </span>
          </p>
        </div>
      </Surface>

      {/* Guards */}
      <Surface className="p-4">
        <div className="flex items-center justify-between">
          <h3 className="text-xs font-bold uppercase tracking-wider text-app-muted">Risk limits</h3>
          <Badge tone={c.guardsSummary.status === 'ok' ? 'success' : c.guardsSummary.status === 'attention' ? 'warning' : 'danger'} className="text-[10px]">
            {c.guardsSummary.status === 'ok' ? 'STABLE' : c.guardsSummary.status === 'attention' ? 'WATCH' : 'INTERVENE'}
          </Badge>
        </div>
        <p className="mt-2 text-xs leading-relaxed text-app-text">{c.guardsSummary.headline}</p>
        <p className="mt-1 font-mono text-[10px] text-app-muted">{c.guardsSummary.violationCount} breaches · {c.guardsSummary.unknownCount ?? 0} unverified / {c.guardsSummary.totalCount} limits</p>
        <div className="mt-2 flex flex-wrap items-center gap-3">
          <button
            type="button"
            onClick={() => setRulesOpen((v) => !v)}
          aria-expanded={rulesOpen}
          className="mt-2 inline-flex min-h-[28px] items-center gap-1 text-[11px] font-semibold text-app-action hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-app-action"
        >
            {rulesOpen ? 'Hide rule details' : 'View rule details'} <ArrowUpRight size={12} className={cn('transition-transform', rulesOpen && 'rotate-90')} />
          </button>
          {onOpenRiskGuards && (
            <button
              type="button"
              onClick={onOpenRiskGuards}
              className="inline-flex min-h-[28px] items-center gap-1 text-[11px] font-semibold text-app-action hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-app-action"
            >
              Per-policy details <ArrowUpRight size={12} />
            </button>
          )}
        </div>
        {rulesOpen && (
          <ul className="mt-2 space-y-1 border-t border-app-border/40 pt-2 text-[10px]">
            {riskLimits.length === 0 && <li className="text-app-muted">No risk rules data</li>}
            {riskLimits.map((limit) => (
              <li key={`${limit.ruleId}:${limit.controllerId ?? 'global'}:${limit.market ?? 'all'}`} title={limit.unavailableReason ?? undefined} className="flex items-baseline justify-between gap-2">
                <span className="text-app-muted">{limit.market ? `${limit.market === 'spot' ? 'Spot' : 'Perp'} · ` : ''}{limit.ruleId}</span>
                <span className={cn('font-mono', limit.status === 'violated' ? 'font-bold text-app-danger' : 'text-app-text')}>
                  {formatAssetAmount(limit.current, limit.unit)} <span className="text-app-muted">/ {formatAssetAmount(limit.limit, limit.unit)}</span>
                </span>
              </li>
            ))}
          </ul>
        )}
        {mode === 'operator' && c.positions.length === 0 && (
          <p className="mt-2 text-[10px] text-app-muted">Mark/liquidation appear once positions are observed.</p>
        )}
      </Surface>
    </div>
  )
}
