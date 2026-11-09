import type { HTMLAttributes, PropsWithChildren } from 'react'
import { cn } from '../utils/cn'

type SurfaceProps = PropsWithChildren<HTMLAttributes<HTMLDivElement>>

export function Surface({ className, children, ...props }: SurfaceProps) {
  return (
    <div
      className={cn(
        'rounded-card border border-app-border bg-app-surface',
        className,
      )}
      {...props}
    >
      {children}
    </div>
  )
}
