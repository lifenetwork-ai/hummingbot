import { cn } from '../../../shared/utils/cn'

export type ThresholdLevel = 'ok' | 'warn' | 'breach' | 'unknown'

export interface ThresholdBarProps {
  /** Current value (same unit as max) */
  current: number | null
  /** Maximum allowable threshold */
  max: number | null
  /** Formatted current value label */
  currentLabel: string
  /** Formatted threshold label */
  maxLabel: string | null
  /** Tooltip note */
  note?: string
  className?: string
}

function levelColor(level: ThresholdLevel): { bar: string; led: string } {
  switch (level) {
    case 'ok': return { bar: 'bg-emerald-500', led: 'bg-emerald-500' }
    case 'warn': return { bar: 'bg-amber-400', led: 'bg-amber-400' }
    case 'breach': return { bar: 'bg-rose-500', led: 'bg-rose-500' }
    default: return { bar: 'bg-app-border', led: 'bg-app-muted/60' }
  }
}

/**
 * Bullet Threshold Bar with LED indicator for Risk Guards.
 * Current / Max threshold — LED levels: ok <60%, warn 60–90%, breach >90% or blocker present.
 */
export function ThresholdBar({ current, max, currentLabel, maxLabel, note, className }: ThresholdBarProps) {
  const hasValues = current !== null && max !== null && max > 0
  const ratio = hasValues ? Math.min(1, Math.max(0, (current as number) / (max as number))) : 0
  const level: ThresholdLevel = !hasValues
    ? 'unknown'
    : ratio > 1 || ratio >= 0.95 ? 'breach'
      : ratio >= 0.6 ? 'warn'
        : 'ok'
  const colors = levelColor(level)
  const pct = (ratio * 100).toFixed(1)

  return (
    <div className={cn('w-full min-w-0', className)}>
      <div
        className="flex min-w-0 items-center gap-2"
        role="img"
        aria-label={`Current ${currentLabel} / threshold ${maxLabel ?? 'unknown'} (${pct}%)`}
        title={note ?? `Current: ${currentLabel}${maxLabel ? ` · Threshold: ${maxLabel} (${pct}%)` : ' · Unknown threshold'}`}
      >
        <span className={cn('h-2 w-2 shrink-0 rounded-full', colors.led, level === 'breach' && 'animate-pulse')} aria-hidden="true" />
        <div className="h-2 flex-1 overflow-hidden rounded-pill bg-app-border/30">
          <div
            className={cn('h-full rounded-pill transition-all duration-500', colors.bar)}
            style={{ width: hasValues ? `${Math.max(2, ratio * 100)}%` : '0%' }}
          />
        </div>
        <span className="shrink-0 font-mono text-xs tabular-nums text-app-muted">
          {hasValues ? `${pct}%` : '—'}
        </span>
      </div>
      <div className="mt-1 flex min-w-0 items-baseline justify-between gap-3 text-xs">
        <span className="min-w-0 break-words font-mono text-sm font-semibold tabular-nums text-app-text">{currentLabel}</span>
        <span className="min-w-0 break-words text-right text-app-muted">/ {maxLabel ?? 'unknown threshold'}</span>
      </div>
    </div>
  )
}
