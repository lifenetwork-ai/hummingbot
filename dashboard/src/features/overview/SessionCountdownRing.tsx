import { useEffect, useState } from 'react'
import { cn } from '../../shared/utils/cn'
import { formatDuration } from '../../shared/utils/formatters'

export interface SessionCountdownRingProps {
  /** TODO(api): `status.time_remaining_seconds` tại thời điểm quan sát */
  remainingSeconds: number | null
  /** TODO(api): tổng thời hạn phiên — dùng làm mạch vòng */
  totalSeconds: number | null
  /** TODO(api): `status.observation.observed_at` — ngoại suy theo đồng hồ local */
  observedAt: string | null
  size?: number
  className?: string
}

function pad(n: number) {
  return n.toString().padStart(2, '0')
}

/**
 * Vòng đếm ngược thời hạn phiên (timed) — số to tường minh theo phong cách Overview cũ.
 * Ngoại suy từ đồng hồ local sau quan sát cuối; về 0 thì giữ 00:00 chờ backend xác nhận
 * (không tự suy phiên đã kết thúc). Until-stopped thì không hiển thị ring.
 */
export function SessionCountdownRing({ remainingSeconds, totalSeconds, observedAt, size = 68, className }: SessionCountdownRingProps) {
  const [nowMs, setNowMs] = useState(() => Date.now())

  const observedMs = observedAt ? Date.parse(observedAt) : NaN
  const canExtrapolate = Number.isFinite(observedMs) && observedMs > 0

  useEffect(() => {
    if (!canExtrapolate || remainingSeconds === null) return
    const id = window.setInterval(() => setNowMs(Date.now()), 1000)
    return () => window.clearInterval(id)
  }, [canExtrapolate, remainingSeconds])

  if (remainingSeconds === null) {
    return <span className="font-mono text-sm font-bold text-app-text">—</span>
  }

  const remaining = canExtrapolate
    ? Math.max(0, remainingSeconds - Math.max(0, (nowMs - observedMs) / 1000))
    : remainingSeconds

  const progress = totalSeconds && totalSeconds > 0 ? Math.min(1, Math.max(0, remaining / totalSeconds)) : 0
  const low = remaining <= 600

  const hours = Math.floor(remaining / 3600)
  const minutes = Math.floor((remaining % 3600) / 60)
  const seconds = Math.floor(remaining % 60)
  const center = hours > 0 ? `${hours}:${pad(minutes)}:${pad(seconds)}` : `${pad(minutes)}:${pad(seconds)}`

  const radius = 30
  const circumference = 2 * Math.PI * radius
  const dashOffset = circumference * (1 - progress)

  return (
    <div
      className={cn('relative shrink-0', className)}
      style={{ width: size, height: size }}
      role="img"
      aria-label={`Session time remaining ${formatDuration(remaining)}${totalSeconds ? ` of ${formatDuration(totalSeconds)}` : ''}`}
      title={`${formatDuration(remaining)} remaining${totalSeconds ? ` of ${formatDuration(totalSeconds)}` : ''}${remaining <= 0 ? ' · waiting for backend to confirm end' : ''}`}
    >
      <svg viewBox="0 0 68 68" className="h-full w-full -rotate-90">
        <circle cx="34" cy="34" r={radius} className="text-app-border" stroke="currentColor" strokeWidth="5" fill="transparent" />
        <circle
          cx="34" cy="34" r={radius}
          className={cn('transition-[stroke-dashoffset] duration-1000 ease-linear', low ? 'text-app-warning' : 'text-app-action')}
          stroke="currentColor" strokeWidth="5" fill="transparent"
          strokeDasharray={circumference}
          strokeDashoffset={dashOffset}
          strokeLinecap="round"
        />
      </svg>
      <div className="absolute inset-0 flex items-center justify-center">
        <span className={cn('font-mono font-bold tabular-nums', low ? 'text-app-warning' : 'text-app-text')} style={{ fontSize: center.length > 5 ? 12 : 14 }}>
          {center}
        </span>
      </div>
    </div>
  )
}
