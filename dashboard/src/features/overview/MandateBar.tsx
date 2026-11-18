import { cn } from '../../shared/utils/cn'
import { formatAssetAmount, formatUsdt } from '../../shared/utils/formatters'
import { Badge } from '../../shared/components/Badge'
import { Surface } from '../../shared/components/Surface'
import { SessionCountdownRing } from './SessionCountdownRing'
import type { GuardsSummary, MandateView } from './supplement'

/**
 * Q1 — "Bot được giao mục tiêu, vốn và giới hạn nào?"
 * Vốn phân bổ + risk limits từ supplement; profile là nút mở ProfileExplorerDialog.
 */
export function MandateBar({
  view,
  guards,
  onOpenProfileExplorer,
}: {
  view: MandateView
  guards: GuardsSummary
  onOpenProfileExplorer?: () => void
}) {
  const m = view
  const statusTone = m.operationalStatus === 'HALTED' ? 'danger'
    : ['STALE', 'STARTING', 'STOPPING'].includes(m.operationalStatus) ? 'warning'
      : m.operationalStatus === 'RUNNING' ? 'success' : 'neutral'
  return (
    <Surface className="p-4 sm:p-5">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <h2 className="text-base font-bold text-app-text">Assigned mandate</h2>
        <SessionCountdownRing
          remainingSeconds={m.durationMode === 'timed' ? m.timeRemainingSeconds : null}
          totalSeconds={m.totalSessionSeconds}
          observedAt={m.observedAt}
        />
        <Badge tone={statusTone} className="text-[11px]">
          <span className="mr-1 h-1.5 w-1.5 rounded-full bg-current" aria-hidden="true" />
          {m.operationalStatus}
        </Badge>
        <span className="ml-auto text-[11px] text-app-muted">
          {m.observedAt ? `Observed at ${new Date(m.observedAt).toLocaleTimeString('en-GB')}` : 'No observation yet'}
        </span>
      </div>

      <dl className="mt-3 grid grid-cols-2 gap-x-6 gap-y-3 text-xs sm:grid-cols-3 lg:grid-cols-6">
        <div>
          <dt className="text-[10px] font-medium uppercase tracking-wider text-app-muted">Profile</dt>
          <dd className="mt-0.5">
            {onOpenProfileExplorer && m.profileId ? (
              <button
                type="button"
                onClick={onOpenProfileExplorer}
                className="inline-flex min-h-[28px] items-center gap-1 rounded font-mono font-bold text-app-text underline decoration-app-border decoration-dotted underline-offset-4 hover:text-app-action focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-app-action"
                title="Open: how this profile prices the traded pair"
              >
                {m.profileId}
                <span className="font-sans font-normal text-app-muted">({m.selection}{m.randomStep ? <> · step <span className="font-mono">{m.randomStep}</span></> : null})</span>
              </button>
            ) : (
              <span className="font-mono font-bold text-app-text">{m.profileId ?? '—'}<span className="ml-1 font-sans font-normal text-app-muted">({m.selection}{m.randomStep ? <> · step <span className="font-mono">{m.randomStep}</span></> : null})</span></span>
            )}
          </dd>
        </div>
        <div>
          <dt className="text-[10px] font-medium uppercase tracking-wider text-app-muted">Market</dt>
          <dd className="mt-0.5 font-mono font-bold text-app-text">{m.marketMode}</dd>
        </div>
        <div className="min-w-0">
          <dt className="text-[10px] font-medium uppercase tracking-wider text-app-muted">Quote budget</dt>
          <dd className="mt-0.5 font-mono font-bold text-app-text">
            {m.quoteBudget?.available ? formatUsdt(m.quoteBudget.value.total_usdt) : '—'}
          </dd>
          <dd className="mt-1 text-[10px] text-app-muted">
            Configured order notional · allocated capital {m.capitalAllocated.available ? formatUsdt(m.capitalAllocated.value.usdt) : 'not configured'}
          </dd>
        </div>
        <div className="col-span-2 sm:col-span-3 lg:col-span-3">
          <dt className="text-[10px] font-medium uppercase tracking-wider text-app-muted">Risk limits (limit → current)</dt>
          <dd className="mt-1 flex flex-wrap gap-x-3 gap-y-1">
            {m.riskLimits.length === 0 && <span className="text-[11px] text-app-muted">No risk rules data</span>}
            {m.riskLimits.map((limit) => (
              <span key={`${limit.ruleId}:${limit.controllerId ?? 'global'}:${limit.market ?? 'all'}`} title={limit.unavailableReason ?? limit.controllerId ?? undefined} className={cn('font-mono text-[11px]', limit.status === 'violated' ? 'font-bold text-app-danger' : limit.status === 'unknown' ? 'text-app-warning' : 'text-app-text')}>
                {limit.market ? `${limit.market === 'spot' ? 'Spot' : 'Perp'} · ` : ''}{limit.ruleId}: <span className="text-app-muted" title={limit.limit ?? undefined}>{formatAssetAmount(limit.limit, limit.unit)}</span> → <span title={limit.current ?? undefined}>{formatAssetAmount(limit.current, limit.unit)}</span>
              </span>
            ))}
          </dd>
        </div>
      </dl>

      <div className="mt-3 flex flex-wrap items-center justify-between gap-2 border-t border-app-border/50 pt-3">
        <span className={cn('text-[11px] font-semibold', guards.status === 'ok' ? 'text-app-success' : guards.status === 'attention' ? 'text-app-warning' : 'text-app-danger')}>
          Guards: {guards.violationCount} breaches · {guards.unknownCount ?? 0} unverified / {guards.totalCount} limits
        </span>
      </div>
    </Surface>
  )
}
