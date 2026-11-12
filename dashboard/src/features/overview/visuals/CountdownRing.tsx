import { useEffect, useState } from 'react'
import { cn } from '../../../shared/utils/cn'

export interface CountdownRingProps {
  /** Label at ring center (e.g., #33) */
  centerLabel: string | null
  /** Total step duration (seconds) */
  durationSeconds: number | null
  /** Seconds remaining at latest observation */
  remainingSeconds: number | null
  /** Observation timestamp (ISO) for local clock extrapolation */
  observedAt: string | null
  /** Expiration timestamp of current step (ISO) */
  expiresAt: string | null
  /** Reconcile / awaiting sync state — turns ring amber */
  attention?: boolean
  /** No cycle data available */
  unavailable?: boolean
  size?: number
  className?: string
}

/**
 * Random Step cycle countdown ring.
 * Local clock extrapolated from latest observation; does not infer step
 * transitions when reaching 0 (awaits backend confirmation via new observation).
 */
export function CountdownRing({
  centerLabel,
  durationSeconds,
  remainingSeconds,
  observedAt,
  expiresAt,
  attention = false,
  unavailable = false,
  size = 44,
  className,
}: CountdownRingProps) {
  const [nowMs, setNowMs] = useState(() => Date.now())

  const observedMs = observedAt ? Date.parse(observedAt) : NaN
  const canExtrapolate = Number.isFinite(observedMs) && observedMs > 0

  useEffect(() => {
    if (!canExtrapolate || unavailable) return
    const id = window.setInterval(() => setNowMs(Date.now()), 1000)
    return () => window.clearInterval(id)
  }, [canExtrapolate, unavailable])

  let remaining: number | null = null
  if (remainingSeconds !== null && remainingSeconds !== undefined) {
    if (canExtrapolate) {
      const driftSeconds = (nowMs - observedMs) / 1000
      remaining = Math.max(0, remainingSeconds - Math.max(0, driftSeconds))
    } else {
      remaining = remainingSeconds
    }
  } else if (expiresAt && canExtrapolate) {
    const end = Date.parse(expiresAt)
    if (Number.isFinite(end)) remaining = Math.max(0, (end - nowMs) / 1000)
  }

  const duration = durationSeconds && durationSeconds > 0 ? durationSeconds : null
  const progress = remaining !== null && duration ? Math.min(1, Math.max(0, remaining / duration)) : 0

  const radius = 18
  const circumference = 2 * Math.PI * radius
  const dashOffset = circumference * (1 - progress)

  return (
    <div
      className={cn('relative shrink-0', className)}
      style={{ width: size, height: size }}
      role="img"
      aria-label={
        unavailable
          ? 'Random cycle: no data'
          : `Cycle ${centerLabel ?? ''}: ${remaining !== null ? Math.ceil(remaining) : '—'}s remaining / ${duration ?? '—'}s`
      }
      title={
        unavailable
          ? 'No random cycle observation'
          : `Step ${centerLabel ?? '—'} · ${remaining !== null ? Math.ceil(remaining) : '—'}s remaining${duration ? ` / ${duration}s` : ''}${attention ? ' · awaiting sync' : ''}`
      }
    >
      <svg viewBox="0 0 44 44" className="h-full w-full -rotate-90">
        <circle
          cx="22" cy="22" r={radius}
          className={attention ? 'text-amber-400/30' : 'text-app-border'}
          stroke="currentColor" strokeWidth="4" fill="transparent"
        />
        <circle
          cx="22" cy="22" r={radius}
          className={cn(
            'transition-[stroke-dashoffset] duration-1000 ease-linear',
            unavailable ? 'text-app-muted' : attention ? 'text-amber-400' : 'text-app-action',
          )}
          stroke="currentColor" strokeWidth="4" fill="transparent"
          strokeDasharray={circumference}
          strokeDashoffset={unavailable ? circumference : dashOffset}
          strokeLinecap="round"
        />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center leading-none">
        {unavailable ? (
          <span className="text-[9px] font-semibold text-app-muted">—</span>
        ) : (
          <>
            <span className="font-mono text-[11px] font-bold text-app-text">{centerLabel ?? '—'}</span>
            <span className="font-mono text-[9px] text-app-muted">{remaining !== null ? `${Math.ceil(remaining)}s` : '—'}</span>
          </>
        )}
      </div>
    </div>
  )
}
