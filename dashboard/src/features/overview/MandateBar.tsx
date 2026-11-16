import { cn } from '../../shared/utils/cn'
import { Badge } from '../../shared/components/Badge'
import { Surface } from '../../shared/components/Surface'
import { SessionCountdownRing } from './SessionCountdownRing'
import { BackendGapChip } from './BackendGapChip'
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
  return (
    <Surface className="p-4 sm:p-5">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <h2 className="text-base font-bold text-app-text">Assigned mandate</h2>
        <SessionCountdownRing
          remainingSeconds={m.durationMode === 'timed' ? m.timeRemainingSeconds : null}
          totalSeconds={m.totalSessionSeconds}
          observedAt={m.observedAt}
        />
        <Badge tone={m.running ? 'success' : 'neutral'} className="text-[11px]">
          <span className={cn('mr-1 h-1.5 w-1.5 rounded-full', m.running ? 'bg-emerald-500' : 'bg-app-muted')} />
          {m.running ? 'RUNNING' : 'STOPPED'}
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
                title="Open: how this profile prices LIFE"
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
        <div>
          <dt className="flex items-center gap-1 text-[10px] font-medium uppercase tracking-wider text-app-muted">
            Allocated capital
            {!m.capitalAllocated.available && (
              <BackendGapChip detail={m.capitalAllocated.reason ?? 'Allocated capital not provided yet (OverviewSupplement.session.capital_allocated).'} />
            )}
            {m.capitalAllocated.available && m.capitalAllocated.stale && <Badge tone="warning" className="text-[9px]">STALE</Badge>}
          </dt>
          <dd className="mt-0.5 font-mono font-bold text-app-text">
            {m.capitalAllocated.available ? (
              <>
                {m.capitalAllocated.value.usdt} USDT
                <span className="block text-[10px] font-normal text-app-muted">{m.capitalAllocated.value.life} LIFE</span>
              </>
            ) : (
              '—'
            )}
          </dd>
        </div>
        <div className="col-span-2 sm:col-span-3 lg:col-span-3">
          <dt className="text-[10px] font-medium uppercase tracking-wider text-app-muted">Risk limits (limit → current)</dt>
          <dd className="mt-1 flex flex-wrap gap-x-3 gap-y-1">
            {m.riskLimits.length === 0 && <span className="text-[11px] text-app-muted">No risk rules data</span>}
            {m.riskLimits.map((limit) => (
              <span key={limit.ruleId} className={cn('font-mono text-[11px]', limit.status === 'violated' ? 'font-bold text-app-danger' : limit.status === 'unknown' ? 'text-app-warning' : 'text-app-text')}>
                {limit.ruleId}: <span className="text-app-muted">{limit.limit ?? '—'}</span> → {limit.current ?? '—'} <span className="text-app-muted">{limit.unit}</span>
              </span>
            ))}
          </dd>
        </div>
      </dl>

      <div className="mt-3 flex flex-wrap items-center justify-between gap-2 border-t border-app-border/50 pt-3">
        <span className={cn('text-[11px] font-semibold', guards.status === 'ok' ? 'text-app-success' : guards.status === 'attention' ? 'text-app-warning' : 'text-app-danger')}>
          Guards: {guards.status === 'ok' ? 'Stable' : guards.status === 'attention' ? 'Watch' : 'Intervention needed'} ({guards.violationCount}/{guards.totalCount})
        </span>
      </div>
    </Surface>
  )
}
