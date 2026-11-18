import { useState } from 'react'
import { BarChart, Calendar, Clock, Globe } from 'lucide-react'
import { useReportingController } from '../features/reporting/useReportingController'
import { REPORTING_PRESETS, reportingRangeForPreset, type ReportingPreset } from '../features/reporting/rangePresets'
import { ControllerPerformanceView } from '../features/statistics/ControllerPerformanceView'
import { DailyBalancesChart } from '../features/statistics/DailyBalancesChart'
import { DailyFundingChart } from '../features/statistics/DailyFundingChart'
import { DailySummaryMetrics } from '../features/statistics/DailySummaryMetrics'
import { DailyVolumeChart } from '../features/statistics/DailyVolumeChart'
import { Badge } from '../shared/components/Badge'
import { SectionLabel } from '../shared/components/SectionLabel'
import { MotionPage } from '../shared/motion/MotionPage'
import { cn } from '../shared/utils/cn'

export function DailyStatisticsPage() {
  // Default preset (7 days) sends no range so the server's reporting-timezone default stays authoritative.
  const [preset, setPreset] = useState<ReportingPreset>(7)
  const [fromDay, setFromDay] = useState('')
  const [toDay, setToDay] = useState('')
  // Presets above seven days are computed in the server's reporting timezone once observed.
  const [reportingTimezone, setReportingTimezone] = useState<string | null>(null)
  const customRange = { ...(fromDay ? { from: fromDay } : {}), ...(toDay ? { to: toDay } : {}) }
  const report = useReportingController(reportingRangeForPreset(preset, customRange, reportingTimezone), { includePerformance: true })
  if (report.reportingTimezone !== null && report.reportingTimezone !== reportingTimezone) {
    setReportingTimezone(report.reportingTimezone)
  }
  const data = report.daily.data
  const balances = data?.balances ?? []
  const isStale = report.presentation === 'updating'

  return (
    <MotionPage>
      <div className="mx-auto w-full max-w-[1536px] px-4 pb-16 pt-4 sm:px-6 sm:pt-6 md:px-8 lg:px-10">
        {/* Header */}
        <div className="mb-6 flex flex-col justify-between gap-3 sm:flex-row sm:items-center">
          <div>
            <SectionLabel tone="brand">Statistics & Performance</SectionLabel>
            <h1 className="mt-1 font-heavy text-2xl font-bold tracking-tight text-app-text md:text-3xl flex items-center gap-2.5">
              <BarChart size={26} className="text-app-action" />
              <span>Daily Operational Reporting (Daily Statistics)</span>
            </h1>
            <p className="mt-2 text-sm text-app-muted">
              Aggregates daily trading volume, transaction fees, funding rates, and asset balance changes.
            </p>
          </div>

          <div className="flex items-center gap-2">
            <Badge tone="brand" className="gap-1 text-xs">
              <Globe size={13} />
              <span>Reporting timezone: {report.reportingTimezone ?? 'Unverified'}</span>
            </Badge>
          </div>
        </div>

        {!report.validation.valid && <p role="alert" className="mb-4 text-sm text-app-danger">Invalid date range: {report.validation.reason}</p>}
        {report.daily.error && <p role="alert" className="mb-4 text-sm text-app-danger">Could not load the report. {report.daily.error.message}</p>}
        <p className="mb-3 text-xs text-app-muted">Observed: {report.observedAt ?? 'None yet'} · retry after {report.retryAfterSeconds ?? '—'} seconds · {report.presentation}</p>
        {/* Stale notice if updating */}
        {isStale && (
          <div className="mb-6 rounded-card border border-app-warning/30 bg-app-warning/10 p-3.5 text-xs text-app-warning flex items-start gap-2.5">
            <Clock size={16} className="mt-0.5 shrink-0" />
            <div>
              <strong>Statistics updating:</strong>
              <p className="mt-0.5 text-app-text">
                The server is re-aggregating historical data from the database. The data shown below is the latest valid cached copy.
              </p>
            </div>
          </div>
        )}

        {/* Date Filter Bar */}
        <div className="mb-6 flex flex-wrap items-center justify-between gap-3 rounded-card border border-app-border bg-app-surface p-3 text-xs">
          <div className="flex flex-wrap items-center gap-3">
            <span className="flex items-center gap-1.5 font-semibold text-app-muted">
              <Calendar size={14} />
              <span>Time range:</span>
            </span>

            <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label="Quick range presets">
              {REPORTING_PRESETS.map(({ days, label }) => (
                <button
                  key={days}
                  type="button"
                  aria-pressed={preset === days}
                  onClick={() => { setPreset(days); setFromDay(''); setToDay('') }}
                  className={cn(
                    'rounded-button border px-2 py-0.5 text-[11px] transition-colors',
                    preset === days
                      ? 'border-app-action bg-app-action/10 font-semibold text-app-action'
                      : 'border-app-border bg-app-surface text-app-muted hover:border-app-action/50 hover:text-app-text',
                  )}
                >
                  {label}
                </button>
              ))}
              <button
                type="button"
                aria-pressed={preset === 'custom'}
                onClick={() => setPreset('custom')}
                className={cn(
                  'rounded-button border px-2 py-0.5 text-[11px] transition-colors',
                  preset === 'custom'
                    ? 'border-app-action bg-app-action/10 font-semibold text-app-action'
                    : 'border-app-border bg-app-surface text-app-muted hover:border-app-action/50 hover:text-app-text',
                )}
              >
                Custom
              </button>
            </div>

            <div className="flex items-center gap-2">
              <label htmlFor="from-day" className="text-app-muted">From day (Inclusive):</label>
              <input
                id="from-day"
                type="date"
                value={fromDay}
                onChange={(e) => { setFromDay(e.target.value); setPreset('custom') }}
                className="rounded-button border border-app-border bg-app-surface px-2.5 py-1 text-xs text-app-text focus:border-app-action focus:outline-none"
              />
            </div>

            <div className="flex items-center gap-2">
              <label htmlFor="to-day" className="text-app-muted">To day (Exclusive):</label>
              <input
                id="to-day"
                type="date"
                value={toDay}
                onChange={(e) => { setToDay(e.target.value); setPreset('custom') }}
                className="rounded-button border border-app-border bg-app-surface px-2.5 py-1 text-xs text-app-text focus:border-app-action focus:outline-none"
              />
            </div>
          </div>

          <div className="text-[11px] text-app-muted font-mono">
            {report.daily.refreshing ? 'Updating statistics...' : report.presentation === 'available' ? 'Data is ready' : 'Data unverified'}
          </div>
        </div>

        {/* 1. Summary Metrics */}
        <DailySummaryMetrics summary={report.summary} isLoading={report.daily.phase === 'loading'} />

        {/* 2. Charts */}
        <div className="mt-6 space-y-6">
          <DailyVolumeChart series={report.series} isLoading={report.daily.phase === 'loading'} />
          <DailyFundingChart series={report.series} isLoading={report.daily.phase === 'loading'} />
          <ControllerPerformanceView model={report.performancePanel} onRefresh={() => { void report.refreshPerformance() }} />
          <DailyBalancesChart balances={balances} isLoading={report.daily.phase === 'loading'} />
        </div>
      </div>
    </MotionPage>
  )
}
