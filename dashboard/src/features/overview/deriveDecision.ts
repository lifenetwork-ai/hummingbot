/**
 * Suy diễn đánh giá phiên (Q6) từ liquidity + capital/risk — hàm thuần, không state.
 * Verdict luôn khớp các block Q3–Q5 phía trên vì cùng nguồn dữ liệu.
 */
import { parseAmount } from './parseAmount'
import type { CapitalRiskView, LiquidityMarketView } from './supplement'

export type DecisionVerdict = 'met' | 'partial' | 'not_met' | 'unknown'

export interface DecisionRow {
  goal: string
  verdict: DecisionVerdict
  note: string
}

export interface DecisionSummary {
  overall: 'met' | 'attention' | 'not_met'
  rows: DecisionRow[]
}

function rollVerdicts(verdicts: DecisionVerdict[]): DecisionVerdict {
  if (verdicts.length === 0) return 'unknown'
  if (verdicts.includes('not_met')) return 'not_met'
  if (verdicts.includes('partial')) return 'partial'
  if (verdicts.includes('unknown')) return 'unknown'
  return 'met'
}

function marketLabel(market: string) {
  return market === 'spot' ? 'Spot' : 'Perp'
}

export function deriveDecision(liquidity: LiquidityMarketView[], capital: CapitalRiskView): DecisionSummary {
  const rows: DecisionRow[] = []

  // Mục tiêu 1 — giao dịch thuận lợi: spread thực vs target từng market
  const spreadNotes = liquidity.map((m) => {
    const met = m.spreadActualBps !== null && m.spreadTargetBps !== null && m.spreadActualBps <= m.spreadTargetBps
    return {
      met,
      note: `${marketLabel(m.market)} ${m.spreadActualBps ?? '—'}/${m.spreadTargetBps ?? '—'} bps`,
    }
  })
  rows.push({
    goal: 'Trade facilitation',
    verdict: rollVerdicts(spreadNotes.map((s) => (s.met ? 'met' : 'not_met'))),
    note:
      spreadNotes.map((s) => s.note).join(' · ') +
      (spreadNotes.some((s) => !s.met && !s.note.includes('/— bps')) ? ' — above target' : ''),
  })

  // Mục tiêu 2 — duy trì thanh khoản: coverage vs target + two-sided hiện tại
  const covNotes = liquidity.map((m) => {
    const fraction = m.coverageFraction
    const target = m.coverageTargetFraction
    const known = fraction !== null && target !== null && m.rollingCoverageAvailable && m.measurementReady && m.eligibleSeconds > 0 && m.fullWindow === true
    const met = known && fraction >= target
    return {
      known,
      met,
      note: `${marketLabel(m.market)} ${m.coverageFraction !== null ? `${(m.coverageFraction * 100).toFixed(1)}%` : '—'} / ${m.coverageTargetFraction === null ? '—' : `${Math.round(m.coverageTargetFraction * 100)}%`}`,
    }
  })
  const oneSided = liquidity.some((m) => !m.twoSided)
  rows.push({
    goal: 'Liquidity maintained',
    verdict: rollVerdicts([
      ...covNotes.map((c) => (!c.known ? 'unknown' : c.met ? 'met' : 'not_met')),
      oneSided ? ('partial' as DecisionVerdict) : ('met' as DecisionVerdict),
    ]),
    note: covNotes.map((c) => c.note).join(' · ') + (oneSided ? ' · one-sided market' : ''),
  })

  // Mục tiêu 3 — vốn & chi phí hợp lý: phí trong phiên (signed: dương = đã trả)
  const fees = capital.fees.available && !capital.fees.stale && capital.fees.value.processing_state === 'ready' && capital.fees.value.history_complete
    ? capital.fees.value.total_usdt : null
  rows.push({
    goal: 'Capital & costs reasonable',
    verdict: fees === null ? 'unknown' : 'met',
    note:
      fees === null
        ? 'Session fees data unavailable'
        : `Session fees ${fees} USDT`,
  })

  // Mục tiêu 4 — rủi ro trong giới hạn: guards + tồn kho vs hạn mức
  const inventoryBreaches = capital.positions.filter((p) => {
    const amount = parseAmount(p.amount)
    const limit = parseAmount(p.inventoryLimit)
    return amount !== null && limit !== null && amount / limit > 1
  })
  rows.push({
    goal: 'Risk within limits',
    verdict: inventoryBreaches.length > 0 || capital.guardsSummary.status === 'stop'
      ? 'not_met'
      : capital.guardsSummary.status === 'attention' || !capital.guardsSummary.available
        ? 'partial'
        : 'met',
    note:
      inventoryBreaches.length > 0
        ? 'Inventory above limit'
        : capital.guardsSummary.available
          ? capital.guardsSummary.headline
          : capital.guardsSummary.headline,
  })

  // Mục tiêu 5 — hiệu quả vs benchmark hold
  const bench = capital.holdBenchmark.available && !capital.holdBenchmark.stale ? capital.holdBenchmark.value : null
  const outperform = bench !== null ? parseAmount(bench.outperform_usdt) : null
  rows.push({
    goal: 'Performance understood',
    verdict: outperform === null ? 'unknown' : outperform > 0 ? 'met' : outperform < 0 ? 'not_met' : 'met',
    note:
      bench === null || outperform === null
        ? 'Hold benchmark data unavailable'
        : `Session ${bench.session_pnl_usdt} vs hold ${bench.hold_pnl_usdt} USDT (${outperform >= 0 ? 'beats' : 'behind'} benchmark by ${Math.abs(outperform).toFixed(2)})`,
  })

  const overall = rows.some((r) => r.verdict === 'not_met')
    ? 'not_met'
    : rows.some((r) => r.verdict === 'partial' || r.verdict === 'unknown')
      ? 'attention'
      : 'met'

  return { overall, rows }
}
