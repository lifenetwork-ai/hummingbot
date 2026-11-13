import { useMemo, useState } from 'react'
import { Flame } from 'lucide-react'
import { useAdminQuery } from '../../core/api/hooks'
import { Dialog } from '../../shared/components/Dialog'
import { cn } from '../../shared/utils/cn'
import {
  profileReferences,
  referenceContributions,
  SCENARIO_COPY,
  targetPrice,
  weightedReferenceMove,
  type MMProfile,
  type ReferenceDelta,
} from './profile-reference-math'

export interface ProfileExplorerDialogProps {
  open: boolean
  onClose: () => void
  /** Profile currently quoted on Overview, preselected when the dialog opens. */
  activeProfileId?: string | null
  /**
   * Session anchors from /status (first controller entry): LIFE mid and reference
   * prices captured at session start. Null when no session is running.
   */
  anchorLife?: number | null
  className?: string
}

const SLIDER_MIN = -30
const SLIDER_MAX = 30
const BAR_SCALE = 0.3 // ±30% frames the diverging bar

function formatPct(value: number, digits = 1): string {
  return `${value >= 0 ? '+' : ''}${(value * 100).toFixed(digits)}%`
}

/** One diverging bar: contribution left (negative) or right (positive) of a centre axis. */
function ContributionBar({ contribution, label }: { contribution: number; label: string }) {
  const clamped = Math.max(-BAR_SCALE, Math.min(BAR_SCALE, contribution))
  const width = `${(Math.abs(clamped) / BAR_SCALE) * 50}%`
  return (
    <div
      className="relative h-2 min-w-0 flex-1 overflow-hidden rounded-pill bg-app-border/30"
      role="img"
      aria-label={`${label}: ${formatPct(contribution)}`}
    >
      <span className="absolute inset-y-0 left-1/2 w-px bg-app-border" aria-hidden="true" />
      <span
        className={cn(
          'absolute inset-y-0 rounded-pill transition-all duration-200',
          contribution >= 0 ? 'bg-app-action' : 'bg-amber-400',
        )}
        style={contribution >= 0 ? { left: '50%', width } : { right: '50%', width }}
        aria-hidden="true"
      />
    </div>
  )
}

export function ProfileExplorerDialog({ open, onClose, activeProfileId, anchorLife, className }: ProfileExplorerDialogProps) {
  const { data, isPending, isError } = useAdminQuery('listProfiles', {}, { enabled: open })
  const profiles = useMemo(() => Object.entries(data?.catalogue.profiles ?? {}) as Array<[string, MMProfile]>, [data])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [deltas, setDeltas] = useState<Partial<Record<string, ReferenceDelta>>>({})

  const currentId = selectedId && profiles.some(([id]) => id === selectedId)
    ? selectedId
    : (activeProfileId && profiles.some(([id]) => id === activeProfileId) ? activeProfileId : profiles[0]?.[0]) ?? null
  const profile = profiles.find(([id]) => id === currentId)?.[1] ?? null
  const references = profile ? profileReferences(profile) : []
  const contributions = profile ? referenceContributions(profile, deltas) : []
  const move = profile ? weightedReferenceMove(profile, deltas) : 0
  const target = targetPrice(anchorLife ?? null, move)
  const copy = profile ? SCENARIO_COPY[profile.scenario] : null

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="How this profile prices LIFE"
      description="Each profile differs only in how it computes the target mid that quotes are placed around: target = session anchor × (1 + Σ weightᵢ × pair moveᵢ)."
      className={cn('max-w-xl', className)}
    >
      <div className="flex flex-col gap-4">
        {isPending ? (
          <p className="text-sm text-app-muted" data-testid="profile-explorer-loading">Loading catalogue…</p>
        ) : isError || !profile ? (
          <p className="text-sm text-app-muted" data-testid="profile-explorer-error">
            Profile catalogue is unavailable. Check the bot connection and reopen this panel.
          </p>
        ) : (
          <>
            {/* Profile tabs — real catalogue entries from /profiles */}
            <div className="flex flex-wrap gap-1.5" role="tablist" aria-label="Strategy profiles">
              {profiles.map(([id]) => (
                <button
                  key={id}
                  type="button"
                  role="tab"
                  aria-selected={id === currentId}
                  onClick={() => setSelectedId(id)}
                  className={cn(
                    'rounded-button px-2.5 py-1 font-mono text-xs font-semibold transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-app-action',
                    id === currentId
                      ? 'bg-app-action text-app-action-foreground'
                      : 'bg-app-surface-muted text-app-muted hover:text-app-text',
                  )}
                >
                  {id}
                  {id === activeProfileId ? ' •' : ''}
                </button>
              ))}
            </div>

            <div>
              <p className="text-sm font-semibold text-app-text">{copy?.title}</p>
              <p className="mt-0.5 text-xs leading-relaxed text-app-muted">{copy?.body}</p>
            </div>

            {references.length === 0 ? (
              <p className="rounded-card bg-app-surface-muted px-3 py-2 text-xs text-app-muted" data-testid="profile-explorer-no-references">
                No reference pairs — the weighted move is always 0% and the target mid stays on the anchor.
              </p>
            ) : (
              <div className="flex flex-col gap-2.5" data-testid="profile-explorer-references">
                {contributions.map(({ pair, weight, delta, contribution }) => (
                  <div key={pair} className="flex flex-col gap-1">
                    <div className="flex items-baseline justify-between gap-2 text-xs">
                      <span className="font-mono font-semibold text-app-text">{pair}</span>
                      <span className="text-app-muted">weight {weight.toFixed(2)} · move {formatPct(delta)} → {formatPct(contribution)}</span>
                    </div>
                    <div className="flex items-center gap-2">
                      <input
                        type="range"
                        min={SLIDER_MIN}
                        max={SLIDER_MAX}
                        step={0.5}
                        value={delta * 100}
                        onChange={(e) => setDeltas((prev) => ({ ...prev, [pair]: Number(e.target.value) / 100 }))}
                        className="h-1.5 w-36 shrink-0 accent-app-action"
                        aria-label={`${pair} move since session anchor, percent`}
                      />
                      <ContributionBar contribution={contribution} label={`${pair} contribution`} />
                    </div>
                  </div>
                ))}
              </div>
            )}

            <div className="rounded-card border border-app-border/70 bg-app-surface-muted px-3 py-2.5">
              <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
                <span className="text-[10px] font-medium uppercase tracking-wider text-app-muted">Weighted move</span>
                <span className="font-mono text-sm font-bold tabular-nums text-app-text" data-testid="profile-explorer-move">
                  {formatPct(move)}
                </span>
              </div>
              <div className="mt-1.5 flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
                <span className="text-[10px] font-medium uppercase tracking-wider text-app-muted">Target mid</span>
                <span className="flex items-baseline gap-1.5 font-mono text-sm font-bold tabular-nums text-app-text" data-testid="profile-explorer-target">
                  {target !== null ? target.toPrecision(4) : `anchor ${formatPct(move)}`}
                </span>
              </div>
              <p className="mt-1.5 flex items-start gap-1 text-[11px] leading-relaxed text-app-muted">
                <Flame size={12} className="mt-0.5 shrink-0" aria-hidden="true" />
                {anchorLife != null
                  ? `Anchor ${anchorLife} from the running session's recorded anchors.`
                  : 'No running session anchor — start a session to see absolute prices; the weighted move is scenario math only.'}
              </p>
            </div>
          </>
        )}
      </div>
    </Dialog>
  )
}
