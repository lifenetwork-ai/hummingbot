import { Link } from '@tanstack/react-router'
import { ArrowRight } from 'lucide-react'
import { Badge } from '../../shared/components/Badge'
import { Surface } from '../../shared/components/Surface'
import { cn } from '../../shared/utils/cn'
import type { DecisionSummary } from './deriveDecision'

const verdictTone = {
  met: { tone: 'success' as const, label: 'MET' },
  partial: { tone: 'warning' as const, label: 'PARTIAL' },
  not_met: { tone: 'danger' as const, label: 'NOT MET' },
  unknown: { tone: 'neutral' as const, label: 'NO DATA' },
}

const overallTone = {
  met: { tone: 'success' as const, label: 'SAFE TO CONTINUE' },
  attention: { tone: 'warning' as const, label: 'CONSIDER ADJUSTING' },
  not_met: { tone: 'danger' as const, label: 'ADJUST / STOP' },
}

/**
 * Q6 — "Cần tiếp tục, điều chỉnh hay dừng?"
 * Verdict suy diễn từ deriveDecision (liquidity + capital/risk) — luôn khớp Q3–Q5.
 */
export function DecisionCard({ decision }: { decision: DecisionSummary }) {
  const overall = overallTone[decision.overall]
  return (
    <Surface className="p-4 sm:p-5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-base font-bold text-app-text">Session verdict &amp; action</h2>
        <Badge tone={overall.tone}>{overall.label}</Badge>
      </div>

      <ul className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-2 xl:grid-cols-5">
        {decision.rows.map((row) => {
          const v = verdictTone[row.verdict]
          return (
            <li key={row.goal} className="rounded-card bg-app-surface-muted/30 p-2.5">
              <div className="flex items-center justify-between gap-2">
                <span className="text-[11px] font-semibold text-app-text">{row.goal}</span>
                <Badge tone={v.tone} className="text-[9px]">{v.label}</Badge>
              </div>
              <p className={cn('mt-1 text-[10px] leading-relaxed text-app-muted', row.verdict === 'unknown' && 'italic')}>{row.note}</p>
            </li>
          )
        })}
      </ul>

      <div className="mt-3 flex flex-wrap items-center gap-3 border-t border-app-border/50 pt-3 text-xs">
        <span className="text-[11px] text-app-muted">Action:</span>
        {decision.overall !== 'met' && (
          <Link to="/session" className="inline-flex items-center gap-1 font-semibold text-app-action hover:underline">
            Adjust configuration <ArrowRight size={12} />
          </Link>
        )}
        <Link to="/statistics" className="inline-flex items-center gap-1 font-semibold text-app-action hover:underline">
          Daily cost breakdown <ArrowRight size={12} />
        </Link>
      </div>
    </Surface>
  )
}
