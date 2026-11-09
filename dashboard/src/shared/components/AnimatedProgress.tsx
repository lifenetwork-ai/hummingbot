import { motion, useReducedMotion } from 'framer-motion'
import { cn } from '../utils/cn'

type AnimatedProgressProps = {
  value: number
  className?: string
  trackClassName?: string
  ariaLabel?: string
}

export function AnimatedProgress({
  value,
  className,
  trackClassName,
  ariaLabel = 'Progress',
}: AnimatedProgressProps) {
  const reducedMotion = useReducedMotion()
  const safeValue = Math.max(0, Math.min(100, value))

  return (
    <div
      className={cn(
        'h-2 overflow-hidden rounded-pill bg-primary100',
        trackClassName,
      )}
      role="progressbar"
      aria-label={ariaLabel}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={safeValue}
    >
      <motion.div
        className={cn(
          'h-full rounded-pill bg-gradient-to-r from-app-accent via-app-action to-app-action-hover',
          className,
        )}
        initial={reducedMotion ? false : { width: 0 }}
        animate={{ width: `${safeValue}%` }}
        transition={{
          duration: reducedMotion ? 0 : 0.85,
          ease: [0.16, 1, 0.3, 1],
        }}
      />
    </div>
  )
}
