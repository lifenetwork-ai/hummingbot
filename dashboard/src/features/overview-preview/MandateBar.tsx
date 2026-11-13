import { Link } from '@tanstack/react-router'
import { ArrowRight } from 'lucide-react'
import { Badge } from '../../shared/components/Badge'
import { Surface } from '../../shared/components/Surface'
import { cn } from '../../shared/utils/cn'
import { SessionCountdownRing } from './SessionCountdownRing'
import { capitalRiskMock, mandateMock } from './mock/overviewPreviewData'

/**
 * Q1 — "Bot được giao mục tiêu, vốn và giới hạn nào?"
 * Prototype mock: xem mock/overviewPreviewData.ts để thay dữ liệu thật.
 */
export function MandateBar() {
  const m = mandateMock
  const guards = capitalRiskMock.guardsSummary

  return (
    <Surface className="p-4 sm:p-5">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <h2 className="font-heavy text-base font-bold text-app-text">Nhiệm vụ được giao</h2>
        {/* Đếm ngược thời hạn phiên — số to, ngoại suy local (mock) */}
        <SessionCountdownRing
          remainingSeconds={m.durationMode === 'timed' ? m.timeRemainingSeconds : null}
          totalSeconds={m.totalSessionSeconds}
          observedAt={m.observedAt}
        />
        <Badge tone={m.running ? 'success' : 'neutral'} className="text-[11px]">
          <span className={cn('mr-1 h-1.5 w-1.5 rounded-full', m.running ? 'bg-emerald-500' : 'bg-app-muted')} />
          {m.running ? 'RUNNING' : 'STOPPED'}
        </Badge>
        <span className="ml-auto text-[11px] text-app-muted">
          Quan sát lúc {new Date(m.observedAt).toLocaleTimeString('vi-VN')} · TODO(api): timestamp thật
        </span>
      </div>

      <dl className="mt-3 grid grid-cols-2 gap-x-6 gap-y-3 text-xs sm:grid-cols-3 lg:grid-cols-6">
        <div>
          <dt className="text-[10px] font-medium uppercase tracking-wider text-app-muted">Profile</dt>
          <dd className="mt-0.5 font-mono font-bold text-app-text">{m.profileId}<span className="ml-1 font-sans font-normal text-app-muted">({m.selection})</span></dd>
        </div>
        <div>
          <dt className="text-[10px] font-medium uppercase tracking-wider text-app-muted">Thị trường</dt>
          <dd className="mt-0.5 font-mono font-bold text-app-text">{m.markets}</dd>
        </div>
        <div>
          <dt className="text-[10px] font-medium uppercase tracking-wider text-app-muted">Vốn phân bổ</dt>
          <dd className="mt-0.5 font-mono font-bold text-app-text">{m.capitalAllocated.usdt} USDT<span className="block text-[10px] font-normal text-app-muted">{m.capitalAllocated.life} LIFE</span></dd>
        </div>
        <div className="col-span-2">
          <dt className="text-[10px] font-medium uppercase tracking-wider text-app-muted">Giới hạn rủi ro (target → hiện tại)</dt>
          <dd className="mt-1 flex flex-wrap gap-x-3 gap-y-1">
            {m.riskLimits.map((limit) => (
              <span key={limit.name} className={cn('font-mono text-[11px]', limit.status === 'ok' ? 'text-app-text' : 'text-app-danger')}>
                {limit.name}: <span className="text-app-muted">{limit.limit}</span> → {limit.current}
              </span>
            ))}
          </dd>
        </div>
      </dl>

      <div className="mt-3 flex flex-wrap items-center justify-between gap-2 border-t border-app-border/50 pt-3">
        <Link to="/session" className="inline-flex min-h-[34px] items-center gap-1 text-xs font-semibold text-app-action hover:underline">
          Thay đổi cấu hình <ArrowRight size={12} />
        </Link>
        {/* Emergency stop: dùng nút floating toàn cục trong OperationalShell, không lặp ở đây */}
        <span className={cn('text-[11px] font-semibold', guards.status === 'ok' ? 'text-app-success' : 'text-app-warning')}>
          Guards: {guards.status === 'ok' ? 'Đang ổn' : 'Cần theo dõi'} ({guards.violationCount}/{guards.totalCount})
        </span>
      </div>
    </Surface>
  )
}
