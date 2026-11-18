/**
 * Tổng hợp vấn đề và mức đủ dữ liệu cho vùng 5 (Đánh giá & hành động) — hàm thuần, không state.
 * Tham chiếu kết quả từ các vùng trên mà không lặp lại nguyên xi số liệu KPI (spread bps, coverage %)
 * theo UI-STRUCTURE-PLAN mục 3.2 quy tắc 2 ("Summary tham chiếu kết quả và hành động, không dựng bảng chấm điểm trùng").
 */
import { parseAmount } from './parseAmount'
import type { CapitalRiskView, LiquidityMarketView } from './supplement'

export type DecisionIssueKind = 'problem' | 'data_gap'

export interface DecisionIssue {
  area: string
  detail: string
  kind: DecisionIssueKind
}

export interface DecisionSummary {
  issues: DecisionIssue[]
  dataGapCount: number
  hasProblems: boolean
}

function marketLabel(market: string) {
  return market === 'spot' ? 'Spot' : 'Perp'
}

export function deriveDecision(liquidity: LiquidityMarketView[], capital: CapitalRiskView): DecisionSummary {
  const issues: DecisionIssue[] = []

  // Vùng thanh khoản — tổng hợp mức đạt mandate ở cấp vận hành; không lặp lại nguyên xi
  // các metric số học (bps spread, % coverage) đã có tại KPI strip và LiquidityVerdictCard
  const unmetMandateMarkets: string[] = []
  for (const m of liquidity) {
    const spreadExceeded =
      m.spreadActualBps !== null && m.spreadTargetBps !== null && m.spreadActualBps > m.spreadTargetBps

    const coverageKnown =
      m.coverageFraction !== null &&
      m.coverageTargetFraction !== null &&
      m.rollingCoverageAvailable &&
      m.measurementReady &&
      m.eligibleSeconds > 0 &&
      m.fullWindow === true

    const coverageFailed = coverageKnown && (m.coverageFraction ?? 0) < (m.coverageTargetFraction ?? 0)

    if (spreadExceeded || coverageFailed) {
      unmetMandateMarkets.push(marketLabel(m.market))
    }

    if (!m.twoSided) {
      issues.push({
        area: `${marketLabel(m.market)} quoting`,
        detail: 'one-sided market — missing bid or ask side',
        kind: 'problem',
      })
    }
  }

  if (unmetMandateMarkets.length > 0) {
    const marketText = unmetMandateMarkets.length > 1 ? `${unmetMandateMarkets.join(' and ')} markets` : `${unmetMandateMarkets[0]} market`
    issues.push({
      area: 'Liquidity mandate',
      detail: `${marketText} below target mandate — consider adjusting configuration`,
      kind: 'problem',
    })
  }

  // Vùng vốn & rủi ro — guards + tồn kho vs hạn mức
  const inventoryBreaches = capital.positions.filter((p) => {
    const amount = parseAmount(p.amount)
    const limit = parseAmount(p.inventoryLimit)
    return amount !== null && limit !== null && limit > 0 && amount / limit > 1
  })
  if (inventoryBreaches.length > 0) {
    issues.push({
      area: 'Inventory',
      detail: `${inventoryBreaches.length} position${inventoryBreaches.length > 1 ? 's' : ''} above limit`,
      kind: 'problem',
    })
  }
  if (capital.guardsSummary.status === 'stop' || capital.guardsSummary.status === 'attention') {
    issues.push({ area: 'Risk guards', detail: capital.guardsSummary.headline, kind: 'problem' })
  } else if (!capital.guardsSummary.available) {
    issues.push({ area: 'Risk guards', detail: capital.guardsSummary.headline, kind: 'data_gap' })
  }

  // Mức đủ dữ liệu — phí trong phiên (đã hiển thị chi tiết ở vùng vốn & rủi ro)
  const feesKnown = capital.fees.available && !capital.fees.stale && capital.fees.value.processing_state === 'ready' && capital.fees.value.history_complete
  if (!feesKnown) {
    issues.push({ area: 'Session fees', detail: 'aggregate not ready for this session yet', kind: 'data_gap' })
  }

  const dataGapCount = issues.filter((issue) => issue.kind === 'data_gap').length
  return { issues, dataGapCount, hasProblems: issues.some((issue) => issue.kind === 'problem') }
}
