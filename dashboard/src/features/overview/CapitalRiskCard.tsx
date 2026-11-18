import { ArrowUpRight } from 'lucide-react'
import { Badge } from '../../shared/components/Badge'
import { Surface } from '../../shared/components/Surface'
import { cn } from '../../shared/utils/cn'
import { formatAssetAmount } from '../../shared/utils/formatters'
import { BackendGapChip } from './BackendGapChip'
import { parseAmount } from './parseAmount'
import { formatPriceForDisplay, formatQuantityForDisplay, formatRiskValueForDisplay, formatUsdtForDisplay } from './formatNumbers'
import type { DetailMode } from './LiquidityVerdictCard'
import type { CapitalRiskView, MandateLimitRow } from './supplement'

function formatRuleIdLabel(ruleId: string): string {
  const map: Record<string, string> = {
    drawdown_global: 'Global Drawdown',
    drawdown_controller: 'Controller Drawdown',
    spot_outflow: 'Spot Outflow',
    quote_deviation: 'Quote Deviation',
    inventory_base: 'Base Inventory',
    inventory_long: 'Long Position Limit',
    inventory_short: 'Short Position Limit',
    inventory_gross: 'Gross Position Limit',
  }
  if (map[ruleId]) return map[ruleId]
  return ruleId
    .split('_')
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(' ')
}

/** Danh sách rule limit → current — dùng trong dialog Capital Protection (nguồn duy nhất sau khi gộp nút). */
export function RiskLimitsList({ limits }: { limits: MandateLimitRow[] }) {
  if (limits.length === 0) return <p className="text-xs text-app-muted">No risk rules data</p>
  return (
    <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
      {limits.map((limit, idx) => {
        const curNum = parseAmount(limit.current)
        const limNum = parseAmount(limit.limit)
        const hasRatio = curNum !== null && limNum !== null && limNum > 0
        const ratio = hasRatio ? Math.min(1, Math.max(0, curNum / limNum)) : 0
        const pct = hasRatio ? (ratio * 100).toFixed(1) : null
        const isDanger = limit.status === 'violated' || (hasRatio && ratio >= 0.95)
        const isWarn = !isDanger && hasRatio && ratio >= 0.7

        return (
          <div
            key={`${limit.ruleId}-${idx}`}
            className={cn(
              'rounded-md border p-2 transition-colors',
              isDanger
                ? 'border-app-danger/40 bg-app-danger/10'
                : isWarn
                  ? 'border-app-warning/40 bg-app-warning/10'
                  : 'border-app-border/40 bg-app-surface/50'
            )}
          >
            <div className="flex items-center justify-between gap-1.5">
              <div className="min-w-0">
                <p className="truncate text-xs font-semibold text-app-text">
                  {formatRuleIdLabel(limit.ruleId)}
                </p>
                <p className="truncate font-mono text-[10px] text-app-muted">
                  {limit.ruleId}
                </p>
              </div>
              <div className="shrink-0 text-right">
                {pct !== null ? (
                  <span
                    className={cn(
                      'inline-block rounded px-1.5 py-0.2 font-mono text-[10px] font-bold',
                      isDanger
                        ? 'bg-app-danger/20 text-app-danger'
                        : isWarn
                          ? 'bg-app-warning/20 text-app-warning'
                          : 'bg-app-surface-muted text-app-muted'
                    )}
                  >
                    {pct}%
                  </span>
                ) : (
                  <span className="font-mono text-[10px] text-app-muted">—</span>
                )}
              </div>
            </div>

            <div className="mt-1.5 flex items-baseline justify-between gap-2 font-mono text-[11px]">
              <span className={cn('font-bold', isDanger ? 'text-app-danger' : isWarn ? 'text-app-warning' : 'text-app-text')}>
                {formatRiskValueForDisplay(limit.current, limit.unit)}
              </span>
              <span className="text-[10px] text-app-muted">
                / {formatRiskValueForDisplay(limit.limit, limit.unit)} {limit.unit}
              </span>
            </div>

            {/* Mini Progress Bar */}
            <div className="mt-1.5 h-1.5 w-full overflow-hidden rounded-pill bg-app-border/30">
              <div
                className={cn(
                  'h-full rounded-pill transition-all duration-300',
                  isDanger
                    ? 'bg-app-danger'
                    : isWarn
                      ? 'bg-app-warning'
                      : hasRatio
                        ? 'bg-app-success'
                        : 'bg-transparent'
                )}
                style={{ width: hasRatio ? `${Math.max(2, ratio * 100)}%` : '0%' }}
              />
            </div>
          </div>
        )
      })}
    </div>
  )
}

/**
 * Vùng 4 — Vốn và rủi ro: tồn kho/vị thế, guards tóm tắt.
 * "Vốn khả dụng / dành cho lệnh" hiện xem qua dialog chi tiết (Account balances)
 * cho đến khi backend cung cấp reserved_for_open_orders thật thì dựng lại tóm tắt.
 * Portfolio/fees từ supplement; thiếu → chip "chưa có dữ liệu" (không hiện số giả).
 * Hold benchmark ngoài phase — xem backlog, không placeholder thường trực.
 */
export function CapitalRiskCard({ view, mode, onOpenRiskGuards }: { view: CapitalRiskView; mode: DetailMode; onOpenRiskGuards?: () => void }) {
  const c = view

  return (
    <div id="capital-risk-card" className="grid scroll-mt-24 grid-cols-1 gap-3 lg:grid-cols-2">
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
                <span className="font-semibold text-app-text">{p.market} · <span className={p.side === 'LONG' ? 'text-app-success' : 'text-app-danger'}>{p.side}</span></span>
                <span className="font-mono text-base font-bold text-app-text">{amountDisplay ?? '—'} {p.pair.split('-')[0]}</span>
              </div>
              <div className="mt-1.5 space-y-0.5 font-mono text-[10px] text-app-muted">
                <p>
                  entry {entryDisplay ?? '—'}
                  {markNum !== null && <> → mark <span className="text-app-text">{markNum}</span></>}
                </p>
                <p>
                  PnL <span className={cn('font-bold', (parseAmount(p.pnlUsdt) ?? 0) >= 0 ? 'text-app-success' : 'text-app-danger')}>{pnlDisplay ?? '—'}</span>
                  {' '}· margin <span className="text-app-text">{marginDisplay ?? '—'}</span>
                </p>
                <p>
                  liquidation {liqDisplay !== null ? `~${liqDisplay}` : '—'}
                  {liqDistPct !== null && <span className="text-app-warning"> ({liqDistPct.toFixed(1)}% from entry)</span>}
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
        <div className="mt-3 space-y-1.5 border-t border-app-border/40 pt-2 text-[10px] text-app-muted">
          <p className="flex items-center justify-between gap-1.5">
            <span className="flex items-center gap-1.5">
              {c.fees.available
                ? `Session fees${c.fees.stale ? ' · stale' : ''}${c.fees.value.processing_state === 'catching_up' ? ' · catching up' : ''}${!c.fees.value.history_complete ? ' · history incomplete' : ''}`
                : 'Session fees'}
              {!c.fees.available && <BackendGapChip detail={c.fees.reason ?? 'Aggregate session fees unavailable (OverviewSupplement.session.fees).'} />}
            </span>
            <span className="font-mono font-bold text-app-text">{c.fees.available ? `${formatUsdtForDisplay(c.fees.value.total_usdt) ?? '—'} USDT` : '—'}</span>
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
        <p className="mt-1 font-mono text-[10px] text-app-muted">{c.guardsSummary.violationCount}/{c.guardsSummary.totalCount} limits touched</p>
        {onOpenRiskGuards && (
          <button
            type="button"
            onClick={onOpenRiskGuards}
            className="mt-2 inline-flex min-h-[28px] items-center gap-1 text-[11px] font-semibold text-app-action hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-app-action"
          >
            Rule &amp; policy details <ArrowUpRight size={12} />
          </button>
        )}
        {mode === 'operator' && c.positions.length === 0 && (
          <p className="mt-2 text-[10px] text-app-muted">Mark/liquidation appear once positions are observed.</p>
        )}
      </Surface>
    </div>
  )
}
