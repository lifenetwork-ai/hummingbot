import { useState } from 'react'
import { Link } from '@tanstack/react-router'
import { ArrowRight, LineChart, SlidersHorizontal } from 'lucide-react'
import { ProfileExplorerDialog } from './ProfileExplorerDialog'
import type { ObservationState, OverviewModel } from './model'
import { CountdownRing } from './visuals/CountdownRing'
import { Badge } from '../../shared/components/Badge'
import { ProfileSignatureChip } from '../../shared/components/ProfileSignatureChip'
import { Surface } from '../../shared/components/Surface'
import { cn } from '../../shared/utils/cn'
import { formatDuration, localizePhase } from '../../shared/utils/formatters'

export interface OverviewControlBarProps {
  overview: OverviewModel
  observation: ObservationState
  isLoading?: boolean
  className?: string
}

export function OverviewControlBar({ overview, observation, isLoading, className }: OverviewControlBarProps) {
  const status = overview.status.data
  const [explorerOpen, setExplorerOpen] = useState(false)

  const request = status?.session?.request
  const selection = request?.selection
  const profileId = status?.observed_session?.profile_id ?? status?.session?.profile_id ?? overview.liquidity.data?.profile_id ?? null

  const configuredMarkets = status?.markets ?? []
  const pairs = configuredMarkets.map((m) => m.pair).filter((pair, index, all) => all.indexOf(pair) === index)
  const marketSummary = configuredMarkets.some((m) => m.market === 'spot') && configuredMarkets.some((m) => m.market === 'perpetual')
    ? 'Spot + Perp'
    : configuredMarkets.some((m) => m.market === 'spot') ? 'Spot'
      : configuredMarkets.some((m) => m.market === 'perpetual') ? 'Perp'
        : '—'

  const rawPhase = status?.phase?.toLowerCase()
  const isRunning = status?.running === true || rawPhase === 'running'
  const isStarting = rawPhase === 'starting'
  const isStopping = rawPhase === 'stopping'
  const phaseLabel = localizePhase(status?.phase) || (isLoading ? 'Loading…' : 'Unknown')

  let statusTone: 'success' | 'brand' | 'warning' | 'neutral' = 'neutral'
  let statusLabel = 'STOPPED'
  if (observation !== 'fresh') {
    statusLabel = 'NO DATA'
  } else if (isStarting) {
    statusTone = 'brand'
    statusLabel = 'STARTING'
  } else if (isStopping) {
    statusTone = 'warning'
    statusLabel = 'STOPPING'
  } else if (isRunning) {
    statusTone = 'success'
    statusLabel = 'RUNNING'
  }

  const randomCycle = overview.task.randomCycle
  const reconcileAttention = observation !== 'fresh' || Boolean(status?.recovery?.halted)
  const timeRemaining = overview.task.timeRemainingSeconds

  return (
    <Surface className={cn('flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-2.5', className)}>
      {/* Strategy + trading pair */}
      <div className="flex min-w-0 items-center gap-3">
        <ProfileSignatureChip profileId={profileId ?? 'Not configured'} selection={selection} size="sm" />
        <button
          type="button"
          onClick={() => setExplorerOpen(true)}
          className="inline-flex min-h-[30px] items-center gap-1 rounded-button bg-app-surface-muted px-2 py-1 text-xs font-semibold text-app-action hover:bg-app-accent/10 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-app-action"
          title="See how this profile prices LIFE"
          aria-label="Open profile pricing explorer"
        >
          <LineChart size={12} />
        </button>
        <div className="hidden items-center gap-2 border-l border-app-border/60 pl-3 sm:flex">
          <span className="font-mono text-sm font-bold text-app-text">{pairs[0] ?? '—'}</span>
          <span className="text-[10px] font-medium uppercase tracking-wider text-app-muted">{marketSummary}</span>
        </div>
      </div>

      {/* Status */}
      <Badge tone={statusTone} className="gap-1.5 text-[11px]" title={`Session phase: ${phaseLabel}`}>
        <span className={cn(
          'h-1.5 w-1.5 rounded-full',
          statusTone === 'success' ? 'bg-emerald-500 animate-ping' : statusTone === 'brand' ? 'bg-app-action animate-ping' : statusTone === 'warning' ? 'bg-amber-400' : 'bg-app-muted',
        )} />
        <span>{statusLabel}</span>
      </Badge>

      {/* Random Cycle: countdown ring */}
      {randomCycle ? (
        <div className="flex items-center gap-2">
          <CountdownRing
            centerLabel={randomCycle.stepIndex !== null && randomCycle.stepIndex !== undefined ? `#${randomCycle.stepIndex}` : '—'}
            durationSeconds={randomCycle.stepDurationSeconds}
            remainingSeconds={randomCycle.remainingAtObservationSeconds}
            observedAt={randomCycle.observedAt ?? status?.observation?.observed_at ?? null}
            expiresAt={randomCycle.stepExpiresAt}
            attention={reconcileAttention}
          />
        </div>
      ) : (
        <span className="text-[10px] text-app-muted" title="Fixed — Random cycle not applied">
          Fixed
        </span>
      )}

      {/* Session duration */}
      <div className="hidden items-center gap-1.5 md:flex" title="Remaining session duration per latest observation">
        <span className="text-[10px] uppercase tracking-wider text-app-muted">Session</span>
        <span className="font-mono text-xs font-semibold text-app-text">
          {timeRemaining !== null && timeRemaining !== undefined
            ? formatDuration(timeRemaining)
            : request?.duration_mode === 'until_stopped' ? 'Until stopped' : '—'}
        </span>
      </div>

      <div className="ml-auto flex flex-wrap items-center gap-2">
        <Link
          to="/session"
          className="inline-flex min-h-[30px] items-center gap-1 rounded-button bg-app-surface-muted px-2.5 py-1 text-xs font-semibold text-app-action hover:bg-app-accent/10 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-app-action"
        >
          <SlidersHorizontal size={12} />
          <span className="hidden sm:inline">Change configuration</span>
          <ArrowRight size={12} />
        </Link>
      </div>

      <ProfileExplorerDialog
        open={explorerOpen}
        onClose={() => setExplorerOpen(false)}
        activeProfileId={profileId}
        anchorLife={(() => {
          const anchors = status?.observed_session?.anchors ?? status?.session?.anchors ?? null
          const first = anchors ? Object.values(anchors)[0] : undefined
          const life = first ? Number(first.life) : null
          return life !== null && Number.isFinite(life) && life > 0 ? life : null
        })()}
      />
    </Surface>
  )
}
