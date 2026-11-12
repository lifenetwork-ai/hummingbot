import { cn } from '../../../shared/utils/cn'

export type CoverageMinuteState = 'two_sided' | 'one_sided' | 'down'

export interface CoverageBarcodeProps {
  /**
   * Per-minute status in rolling window (newest on right).
   * Null when backend has not provided per-minute history — displays NO DATA placeholder.
   */
  minutes?: (CoverageMinuteState | null)[] | null
  /** Number of slots when no data is available (default: 30 minutes) */
  slotCount?: number
  className?: string
}

const STATE_STYLE: Record<Exclude<CoverageMinuteState, 'down'> | 'down', string> = {
  two_sided: 'bg-emerald-500',
  one_sided: 'bg-amber-400',
  down: 'bg-app-border',
}

/**
 * 30-Min Uptime Barcode.
 * Each bar = 1 rolling minute: Green = two-sided, Amber = one-sided, Gray = paused/down.
 * When per-minute data is absent, renders dashed NO DATA container — does not simulate.
 */
export function CoverageBarcode({ minutes, slotCount = 30, className }: CoverageBarcodeProps) {
  const hasData = Array.isArray(minutes) && minutes.length > 0
  const slots = hasData ? (minutes as (CoverageMinuteState | null)[]) : Array.from({ length: slotCount }, () => null)

  return (
    <div className={cn('w-full', className)}>
      <div
        className={cn(
          'flex h-6 items-stretch gap-[2px] rounded-card border px-1.5 py-1',
          hasData ? 'border-app-border/60 bg-app-surface-muted/30' : 'border-dashed border-app-border/70 bg-transparent',
        )}
        role="img"
        aria-label={
          hasData
            ? `${slots.length}-minute status barcode: two-sided / one-sided / down`
            : 'No rolling per-minute history provided by backend'
        }
      >
        {slots.map((state, index) => (
          <div
            key={index}
            className={cn(
              'flex-1 rounded-[1px]',
              state ? STATE_STYLE[state] : 'bg-app-border/30',
            )}
            title={
              hasData
                ? state === 'two_sided'
                  ? 'Two-sided coverage'
                  : state === 'one_sided'
                    ? 'One-sided coverage'
                    : state === 'down'
                      ? 'Paused / down'
                      : 'Unknown'
                : 'NO DATA — backend has not provided per-minute history'
            }
          />
        ))}
      </div>
      {!hasData && (
        <div className="mt-1 text-[10px] font-medium uppercase tracking-wider text-app-muted">NO DATA · per-minute rolling</div>
      )}
    </div>
  )
}
