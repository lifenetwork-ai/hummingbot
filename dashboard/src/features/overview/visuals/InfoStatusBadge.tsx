import { Info } from 'lucide-react'
import { Badge } from '../../../shared/components/Badge'
import { cn } from '../../../shared/utils/cn'

export interface InfoStatusBadgeProps {
  /** Short label (PAUSED, NO DATA, SYNCING...) */
  label: string
  tone?: 'neutral' | 'brand' | 'success' | 'danger' | 'warning'
  /** Detailed description — only shown in tooltip */
  detail?: string
  className?: string
}

/**
 * Compact status badge replacing long inline diagnostic text.
 * Details are surfaced via native tooltip on hover.
 */
export function InfoStatusBadge({ label, tone = 'neutral', detail, className }: InfoStatusBadgeProps) {
  return (
    <span title={detail} className="inline-flex">
    <Badge
      tone={tone}
      className={cn('gap-1 text-[10px]', className)}
    >
      <span>{label}</span>
      {detail && <Info size={11} className="shrink-0 opacity-70" aria-hidden="true" />}
    </Badge>
    </span>
  )
}
