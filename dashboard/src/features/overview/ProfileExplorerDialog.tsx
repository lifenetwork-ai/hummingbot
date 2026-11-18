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
   * Session anchors from /status (first controller entry): traded pair mid and reference
   * prices captured at session start. Null when no session is running.
   */
  anchorLife?: number | null
  /**
   * Current spread target in bps (from spread policy) — only used to visualise where
   * quotes sit around the target mid. Null hides the quote band.
   */
  spreadBps?: number | null
  className?: string
}

const SLIDER_MIN = -30
const SLIDER_MAX = 30
const BAR_SCALE = 0.3 // ±30% frames the diverging bar

function formatPct(value: number, digits = 1): string {
  return `${value >= 0 ? '+' : ''}${(value * 100).toFixed(digits)}%`
}

function formatDisplayPrice(v: number): string {
  if (Math.abs(v) >= 10_000) {
    return v.toLocaleString('en-US', { maximumFractionDigits: 2 })
  }
  return v.toPrecision(4)
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
          contribution >= 0 ? 'bg-app-action' : 'bg-app-warning',
        )}
        style={contribution >= 0 ? { left: '50%', width } : { right: '50%', width }}
        aria-hidden="true"
      />
    </div>
  )
}

/**
 * Price map — the whole pricing story in one picture:
 * dashed anchor marker → weighted move → target mid at the centre of the quote band,
 * bid/ask at the band edges (± spread). Slider changes move the anchor & mid live.
 */
function PriceMap({ anchorLife, move, target, spreadBps }: { anchorLife: number | null; move: number; target: number | null; spreadBps: number | null }) {
  const fmt = formatDisplayPrice
  const bid = target !== null && spreadBps !== null ? target * (1 - spreadBps / 10_000) : null
  const ask = target !== null && spreadBps !== null ? target * (1 + spreadBps / 10_000) : null
  // Anchor sits left of the mid when the move is positive; clamped inside the axis.
  const anchorOffset = Math.max(-30, Math.min(30, -move * 100))

  return (
    <div data-testid="profile-explorer-chain" className="select-none" role="img"
      aria-label={`Anchor ${anchorLife ?? '—'} moves ${formatPct(move)} to target mid ${target !== null ? fmt(target) : '—'}; quotes ${bid !== null ? fmt(bid) : '—'} / ${ask !== null ? fmt(ask) : '—'} around it`}>
      {/* Mỗi nhãn một hàng riêng (move / target mid / anchor / band / bid-ask / spread)
          để không bao giờ đè nhau khi anchor tiến gần target mid. */}
      <div className="relative h-40">
        {/* hàng 1 — weighted move */}
        <span className="absolute left-1/2 top-0 -translate-x-1/2 whitespace-nowrap font-mono text-[10px] font-bold text-app-action">
          {formatPct(move)} weighted move
        </span>

        {/* hàng 2 — target mid */}
        <span className="absolute left-1/2 top-5 -translate-x-1/2 whitespace-nowrap rounded-card border border-app-action/40 bg-app-action/10 px-1.5 py-0.5 font-mono text-xs font-bold text-app-text">
          {target !== null ? fmt(target) : '—'}
          <span className="ml-1 text-[9px] font-normal uppercase tracking-wider text-app-muted">target mid</span>
        </span>

        {/* hàng 3 — anchor (dịch trái/phải theo move) */}
        {anchorLife != null && (
          <span
            className="absolute top-11 -translate-x-1/2 whitespace-nowrap font-mono text-xs text-app-muted"
            style={{ left: `calc(50% + ${anchorOffset}%)` }}
          >
            {fmt(anchorLife)}<span className="ml-1 text-[9px] uppercase tracking-wider">anchor</span>
          </span>
        )}

        {/* hàng 4 — dải quote + vạch anchor đứt nét + vạch giữa */}
        <div className="absolute left-[18%] right-[18%] top-[72px] h-10">
          <div className="absolute inset-y-0 left-0 w-1/2 rounded-l-card border border-app-success/40 bg-app-success/15" />
          <div className="absolute inset-y-0 right-0 w-1/2 rounded-r-card border border-app-danger/40 bg-app-danger/15" />
          <div className="absolute left-1/2 top-0 bottom-0 w-px bg-app-text/70" aria-hidden="true" />
          {anchorLife != null && (
            <div
              className="absolute -top-1 bottom-[-4px] border-l border-dashed border-app-border"
              style={{ left: `calc(50% + ${anchorOffset}%)` }}
              aria-hidden="true"
            />
          )}
        </div>

        {/* hàng 5 — bid / ask tại hai mép dải */}
        <span className="absolute top-[118px] -translate-x-1/2 whitespace-nowrap font-mono text-xs font-bold text-app-success" style={{ left: '18%' }}>
          {bid !== null ? fmt(bid) : '—'}<span className="ml-1 text-[9px] font-normal uppercase tracking-wider text-app-muted">bid</span>
        </span>
        <span className="absolute top-[118px] translate-x-1/2 whitespace-nowrap font-mono text-xs font-bold text-app-danger" style={{ right: '18%' }}>
          <span className="mr-1 text-[9px] font-normal uppercase tracking-wider text-app-muted">ask</span>{ask !== null ? fmt(ask) : '—'}
        </span>

        {/* hàng 6 — độ rộng spread */}
        <span className="absolute bottom-0 left-1/2 -translate-x-1/2 text-[9px] text-app-muted">
          ± {spreadBps ?? '—'} bps
        </span>
      </div>
    </div>
  )
}

/** Long-form explanation, collapsed by default — the picture carries the story first. */
function HowItWorksDetails({ scenarioBody }: { scenarioBody?: string }) {
  return (
    <details className="rounded-card bg-app-surface-muted/60 px-3 py-2">
      <summary className="cursor-pointer list-none text-[11px] font-semibold text-app-action focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-app-action">
        How it works — 3 steps
      </summary>
      {scenarioBody && <p className="mt-1.5 text-xs leading-relaxed text-app-muted">{scenarioBody}</p>}
      <ol className="mt-1.5 space-y-1.5 text-xs leading-relaxed text-app-muted" data-testid="profile-explorer-steps">
        <li className="flex gap-2">
          <span className="font-mono font-bold text-app-action">1</span>
          <span><span className="font-semibold text-app-text">Anchor capture:</span> At profile start (or step rotation), the bot captures the baseline anchor — the outside traded pair mid (excluding own orders) and each reference pair's mid price.</span>
        </li>
        <li className="flex gap-2">
          <span className="font-mono font-bold text-app-action">2</span>
          <span><span className="font-semibold text-app-text">Every refresh:</span> It measures how far each reference pair moved since its anchor (Δᵢ = Pᵢ/Pᵢ₀ − 1) and calculates the weighted move (Σ wᵢ × Δᵢ).</span>
        </li>
        <li className="flex gap-2">
          <span className="font-mono font-bold text-app-action">3</span>
          <span><span className="font-semibold text-app-text">Target &amp; Quotes:</span> Target mid = anchor × (1 + weighted move). Quote levels (bids/asks) are deployed around this target according to spread &amp; volatility policy, protected by deviation guards.</span>
        </li>
      </ol>
    </details>
  )
}

export function ProfileExplorerDialog({ open, onClose, activeProfileId, anchorLife, spreadBps = null, className }: ProfileExplorerDialogProps) {
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
      title="How this profile prices the traded pair"
      description="A profile answers one question: where is the centre that quotes hug? target mid = baseline anchor × (1 + Σ weightᵢ × pair moveᵢ) — multi-level quotes and spread policies then deploy around it."
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

            <p className="text-sm font-semibold text-app-text">{copy?.title}</p>

            <PriceMap anchorLife={anchorLife ?? null} move={move} target={target} spreadBps={spreadBps} />
            <p className="text-center text-[10px] text-app-muted">
              Visual model shows a simplified single-level symmetric band. Live execution places multi-level orders with dynamic volatility spreads and inventory skewing.
            </p>

            {references.length === 0 ? (
              <p className="rounded-card bg-app-surface-muted px-3 py-2 text-xs text-app-muted" data-testid="profile-explorer-no-references">
                No reference pairs — the weighted move is always 0% and the target mid stays on the anchor.
              </p>
            ) : (
              <div className="flex flex-col gap-2.5" data-testid="profile-explorer-references">
                <p className="flex items-baseline justify-between gap-2 text-[10px] text-app-muted">
                  <span>
                    Simulate a reference move since the anchor to see how the target mid responds —{' '}
                    <span className="font-semibold">this is a what-if scenario, not live market data.</span>
                  </span>
                  <button
                    type="button"
                    onClick={() => setDeltas({})}
                    className="shrink-0 font-semibold text-app-action hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-app-action"
                    data-testid="profile-explorer-reset"
                  >
                    Reset
                  </button>
                </p>
                {contributions.map(({ pair, weight, delta, contribution }) => (
                  <div key={pair} className="flex flex-col gap-1">
                    {/* dòng 1 — tên pair + trọng số, mỗi bên một mép, không chung hàng với số khác */}
                    <div className="flex items-baseline justify-between gap-2 text-xs">
                      <span className="font-mono font-semibold text-app-text">{pair}</span>
                      <span className="text-app-muted">weight <span className="font-mono text-app-text">{weight.toFixed(2)}</span></span>
                    </div>
                    {/* dòng 2 — slider mô phỏng + thanh đóng góp */}
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
                    {/* dòng 3 — readout move → đóng góp, căn phải dưới thanh */}
                    <p className="text-right font-mono text-[10px] text-app-muted">
                      move {formatPct(delta)} → contribution {formatPct(contribution)}
                    </p>
                  </div>
                ))}
              </div>
            )}

            {/* Compact numeric readout (the picture above carries the story) */}
            <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 rounded-card border border-app-border/70 bg-app-surface-muted px-3 py-2 text-xs">
              <span className="text-[10px] font-medium uppercase tracking-wider text-app-muted">
                Weighted move <span className="ml-1 font-mono text-sm font-bold tabular-nums text-app-text" data-testid="profile-explorer-move">{formatPct(move)}</span>
              </span>
              <span className="text-[10px] font-medium uppercase tracking-wider text-app-muted">
                Target mid <span className="ml-1 font-mono text-sm font-bold tabular-nums text-app-text" data-testid="profile-explorer-target">{target !== null ? formatDisplayPrice(target) : `anchor ${formatPct(move)}`}</span>
              </span>
              <span className="flex w-full items-start gap-1 text-[11px] leading-relaxed text-app-muted">
                <Flame size={12} className="mt-0.5 shrink-0" aria-hidden="true" />
                {anchorLife != null
                  ? `Anchor ${anchorLife} from active session's recorded anchors (captured at session start or step rotation).`
                  : 'No running session anchor — start a session to see absolute prices; the weighted move is scenario math only.'}
              </span>
            </div>

            <HowItWorksDetails scenarioBody={copy?.body} />
          </>
        )}
      </div>
    </Dialog>
  )
}
