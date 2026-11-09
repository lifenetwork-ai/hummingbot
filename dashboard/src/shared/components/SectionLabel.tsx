import type { PropsWithChildren } from 'react'
import { cn } from '../utils/cn'

type SectionLabelProps = PropsWithChildren<{
  className?: string
  tone?: 'brand' | 'muted'
}>

export function SectionLabel({
  children,
  className,
  tone = 'muted',
}: SectionLabelProps) {
  return (
    <p
      className={cn(
        'text-xs font-semibold uppercase tracking-[0.18em]',
        tone === 'brand' ? 'text-app-action' : 'text-app-muted',
        className,
      )}
    >
      {children}
    </p>
  )
}
