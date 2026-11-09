import type { PropsWithChildren } from 'react'
import { cn } from '../utils/cn'

type BadgeProps = PropsWithChildren<{
  tone?: 'neutral' | 'brand' | 'success' | 'danger'
  className?: string
}>

const styles = {
  neutral: 'border-app-border bg-app-surface-muted text-app-muted',
  brand: 'border-app-action/25 bg-app-action/10 text-app-action',
  success: 'border-app-success/25 bg-app-success/10 text-app-success',
  danger: 'border-app-danger/25 bg-app-danger/10 text-app-danger',
} as const

export function Badge({ tone = 'neutral', className, children }: BadgeProps) {
  return (
    <span className={cn('inline-flex items-center rounded-pill border px-3 py-1 text-xs font-semibold', styles[tone], className)}>
      {children}
    </span>
  )
}
