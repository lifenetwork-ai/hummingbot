import { cn } from '../../shared/utils/cn'
import { Badge } from '../../shared/components/Badge'
import { Surface } from '../../shared/components/Surface'
import { SessionCountdownRing } from './SessionCountdownRing'
import type { GuardsSummary, MandateView } from './supplement'

/**
 * Vùng 2 — Nhiệm vụ hiện tại: profile, market, thời hạn và tóm tắt guards.
 * Profile là nút mở ProfileExplorerDialog.
 * Chi tiết risk limits (limit → current) chỉ hiển thị ở dialog Capital Protection để tránh trùng lặp.
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
  const guardLabel = guards.perpetualBuyPaused && guards.violationCount === 1
    ? 'Perp BUY paused · long inventory above limit'
    : guards.status === 'ok' ? 'Stable' : guards.status === 'attention' ? 'Watch' : 'Intervention needed'
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
      </dl>

      <div className="mt-3 flex flex-wrap items-center justify-between gap-2 border-t border-app-border/50 pt-3">
        <span className={cn('text-[11px] font-semibold', guards.status === 'ok' ? 'text-app-success' : guards.status === 'attention' ? 'text-app-warning' : 'text-app-danger')}>
          Guards: {guardLabel} ({guards.violationCount}/{guards.totalCount})
        </span>
      </div>
    </Surface>
  )
}
