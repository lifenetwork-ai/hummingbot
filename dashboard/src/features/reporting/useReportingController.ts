import { buildChartSeries, summarizeReporting } from './series'
import { derivePerformancePanel } from './performance'
import { useAdminQuery } from '../../core/api/hooks'
import { deriveReportingModel, validateReportingRange, type ReportingRange } from './model'

/** Daily statistics and controller performance have independent query lifecycles. */
export function useReportingController(range: ReportingRange, options: { includePerformance?: boolean } = {}) {
  const validation = validateReportingRange(range)
  const dailyInput = validation.valid
    ? { query: { ...(range.from ? { from: range.from } : {}), ...(range.to ? { to: range.to } : {}) } }
    : undefined
  const daily = useAdminQuery('getDailyStatistics', dailyInput, { enabled: dailyInput !== undefined })
  const performance = useAdminQuery('getPerformance', {}, { enabled: options.includePerformance === true })
  const model = deriveReportingModel({
    range,
    daily: { phase: daily.isError ? 'error' : daily.isPending ? 'loading' : 'ready', data: daily.data, error: daily.error, refreshing: daily.isFetching },
    performance: { phase: performance.isError ? 'error' : performance.isPending ? 'loading' : 'ready', data: performance.data, error: performance.error, refreshing: performance.isFetching },
  })
  return { ...model, performancePanel: derivePerformancePanel(model.performance), summary: summarizeReporting(daily.data?.markets ?? []), series: buildChartSeries(model.timeline), refreshDaily: daily.refetch, refreshPerformance: performance.refetch }
}
