import { Link } from '@tanstack/react-router'
import { ArrowRight } from 'lucide-react'
import { Surface } from '../../shared/components/Surface'
import type { DecisionSummary } from './deriveDecision'

/**
 * Vùng 5 — "Đánh giá & hành động": tổng hợp vấn đề đang mở và mức đủ dữ liệu,
 * kèm link điều tra/báo cáo/cấu hình. Không tự chấm điểm từng mục và không khuyến nghị
 * chiến lược (Continue/Adjust/Stop) — kết luận chính nằm tại vùng chuyên trách của nó
 * (verdict thanh khoản, risk guards) theo UI-STRUCTURE-PLAN mục 3.2.
 */
export function DecisionCard({ decision }: { decision: DecisionSummary }) {
  const problems = decision.issues.filter((issue) => issue.kind === 'problem')
  const dataGaps = decision.issues.filter((issue) => issue.kind === 'data_gap')

  return (
    <Surface className="p-4 sm:p-5">
      <h2 className="text-base font-bold text-app-text">Assessment &amp; actions</h2>

      {decision.issues.length === 0 ? (
        <p className="mt-2 text-xs text-app-muted">No open issues in the current observations.</p>
      ) : (
        <div className="mt-3 space-y-3">
          {problems.length > 0 && (
            <ul className="space-y-1.5 text-xs">
              {problems.map((issue) => (
                <li key={`${issue.area}-${issue.detail}`} className="flex flex-wrap items-baseline gap-1.5">
                  <span className="font-semibold text-app-text">{issue.area}:</span>
                  <span className="text-app-text">{issue.detail}</span>
                </li>
              ))}
            </ul>
          )}
          {dataGaps.length > 0 && (
            <p className="text-[11px] leading-relaxed text-app-muted">
              Data not sufficient yet for {dataGaps.map((issue) => issue.area).join(', ')} — see the affected section for basis and timestamps.
            </p>
          )}
        </div>
      )}

      <div className="mt-3 flex flex-wrap items-center gap-3 border-t border-app-border/50 pt-3 text-xs">
        <span className="text-[11px] text-app-muted">Action:</span>
        {decision.hasProblems && (
          <Link to="/session" className="inline-flex items-center gap-1 font-semibold text-app-action hover:underline">
            Adjust configuration <ArrowRight size={12} />
          </Link>
        )}
        <Link to="/activity" className="inline-flex items-center gap-1 font-semibold text-app-action hover:underline">
          Investigate events <ArrowRight size={12} />
        </Link>
        <Link to="/statistics" className="inline-flex items-center gap-1 font-semibold text-app-action hover:underline">
          Daily cost breakdown <ArrowRight size={12} />
        </Link>
      </div>
    </Surface>
  )
}
