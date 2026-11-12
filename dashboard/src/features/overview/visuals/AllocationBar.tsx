import { cn } from '../../../shared/utils/cn'

export interface AllocationSegment {
  key: string
  /** Fraction 0..1 */
  fraction: number
  label: string
  /** Segment color (e.g. 'bg-emerald-500') */
  colorClass: string
  /** Locked in order/margin — striped hatched pattern */
  hatched?: boolean
}

export interface AllocationBarProps {
  segments: AllocationSegment[]
  /** Header caption above bar */
  caption?: string
  className?: string
  'aria-label'?: string
}

/**
 * Stacked Allocation Bar.
 * Hatched region = funds in orders / margin.
 */
export function AllocationBar({ segments, caption, className, ...rest }: AllocationBarProps) {
  const total = segments.reduce((sum, segment) => sum + Math.max(0, segment.fraction), 0)
  const normalized = total > 0 ? segments.map((segment) => ({ ...segment, pct: (Math.max(0, segment.fraction) / total) * 100 })) : []

  return (
    <div className={cn('w-full', className)} {...rest}>
      {caption && <div className="mb-1 text-[10px] text-app-muted">{caption}</div>}
      <div
        className="flex h-4 w-full overflow-hidden rounded-pill bg-app-border/30"
        role="img"
        aria-label={rest['aria-label'] ?? 'Asset allocation bar'}
      >
        {normalized.map((segment) => (
          <div
            key={segment.key}
            className={cn(
              'h-full min-w-[2px] transition-all duration-500',
              segment.colorClass,
              segment.hatched && '[background-image:repeating-linear-gradient(45deg,rgba(0,0,0,0.28)_0_4px,transparent_4px_8px)]',
            )}
            style={{ width: `${segment.pct}%` }}
            title={`${segment.label} · ${segment.pct.toFixed(1)}%`}
          />
        ))}
      </div>
      <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-1">
        {normalized.map((segment) => (
          <span key={`${segment.key}-legend`} className="flex items-center gap-1 text-[10px] text-app-muted">
            <span className={cn('h-2 w-2 rounded-full shrink-0', segment.colorClass)} />
            <span className="truncate">{segment.label}</span>
          </span>
        ))}
      </div>
    </div>
  )
}
