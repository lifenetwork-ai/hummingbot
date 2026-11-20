import { useState } from 'react'
import type { ReadInput } from '../../core/api/admin'
import { ApiError } from '../../core/api/client'
import { useAdminQuery } from '../../core/api/hooks'
import { Button } from '../../shared/components/Button'
import { Input } from '../../shared/components/Input'

export type BalanceChangesScope = {
  accountId?: string
  environment?: string
  sessionId?: string
  revision?: string
}

const reasons = {
  account_identity_unavailable: 'Waiting for a verified trading account.',
  session_unavailable: 'No trading session has started.',
  opening_missing: 'Opening balance unavailable',
  closing_missing: 'Closing balance unavailable',
  closing_stale: 'Closing observation is stale',
}

function amount(value: string | null, currency: string, signed = false) {
  if (value === null) return '—'
  const [whole, fraction] = value.split('.')
  const sign = signed && !value.startsWith('-') && /[1-9]/.test(value) ? '+' : ''
  return `${sign}${whole.replace(/\B(?=(\d{3})+(?!\d))/g, ',')}${fraction ? `.${fraction}` : ''} ${currency}`
}

function observationTime(value: string, timeZone: string) {
  return new Intl.DateTimeFormat(undefined, {
    timeZone, dateStyle: 'medium', timeStyle: 'medium',
  }).format(new Date(value))
}

export function BalanceChangesTab({ scope }: { scope?: BalanceChangesScope }) {
  const [period, setPeriod] = useState<'days' | 'session'>('days')
  const [dates, setDates] = useState({ from: '', to: '' })
  const [applied, setApplied] = useState<typeof dates>()
  const input: ReadInput<'getBalanceChanges'> = { query: {
    period, ...(period === 'days' && applied ? applied : {}),
  } }
  const query = useAdminQuery('getBalanceChanges', input, { cacheScope: [
    scope?.accountId, scope?.environment, scope?.revision, period === 'session' ? scope?.sessionId : undefined,
  ] })
  const data = query.data
  const mismatch = data && (
    (data.account_id !== null && !!scope?.accountId && data.account_id !== scope.accountId) ||
    (data.environment !== null && !!scope?.environment && data.environment !== scope.environment) ||
    (data.session_id !== null && period === 'session' && !!scope?.sessionId && data.session_id !== scope.sessionId)
  )
  const current = !mismatch ? data : undefined
  const openingTimes = current?.rows.flatMap((row) => row.opening_observed_at ? [Date.parse(row.opening_observed_at)] : []) ?? []
  const firstOpening = openingTimes.length > 0 ? new Date(Math.min(...openingTimes)).toISOString() : null

  return (
    <section aria-label="Balance changes" className="mt-4 space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <Button variant={period === 'days' ? 'primary' : 'secondary'} aria-pressed={period === 'days'} onClick={() => setPeriod('days')}>Reporting dates</Button>
        <Button variant={period === 'session' ? 'primary' : 'secondary'} aria-pressed={period === 'session'} onClick={() => setPeriod('session')}>Current session</Button>
        <Button variant="secondary" disabled={query.isFetching} onClick={() => { void query.refetch() }}>Refresh changes</Button>
      </div>
      {period === 'days' && (
        <form className="flex flex-wrap items-end gap-2" onSubmit={(event) => {
          event.preventDefault()
          setApplied(dates)
        }}>
          <Input label="From date" type="date" required value={dates.from} onChange={(event) => setDates({ ...dates, from: event.target.value })} />
          <Input label="To date (exclusive)" type="date" required value={dates.to} onChange={(event) => setDates({ ...dates, to: event.target.value })} />
          <Button type="submit" variant="secondary">Apply dates</Button>
          <Button variant="secondary" onClick={() => { setApplied(undefined); setDates({ from: '', to: '' }) }}>Today</Button>
        </form>
      )}
      {query.isPending && <p role="status" className="text-sm text-app-muted">Loading balance changes…</p>}
      {query.isError && <p role="alert" className="text-sm text-app-danger">{
        query.error instanceof ApiError && query.error.status === 404 ? 'Balance changes are unavailable on this server.' :
          query.error instanceof ApiError && [400, 422].includes(query.error.status) ? 'Choose a valid reporting range of up to 90 days.' :
            'Unable to refresh balance changes. Try refreshing again.'
      }</p>}
      {mismatch && <p role="status" className="text-sm text-app-muted">Waiting for a comparison for the current account and session.</p>}
      {current && !query.isError && (
        <>
          <div className="space-y-1 text-xs text-app-muted">
            {current.start_day && <p>{current.start_day} to {current.end_day} (exclusive) · {current.reporting_timezone}</p>}
            {current.session_id && firstOpening && <p>Since first observed at {observationTime(firstOpening, current.reporting_timezone)} · {current.reporting_timezone}</p>}
            {current.account_id && <p className="break-all">Account {current.account_id} · {current.environment}</p>}
            <p>Deposits, withdrawals and resets are unverified. Changes reflect observed cash balances.</p>
          </div>
          {current.reason_code &&
            <p role="status" className="text-sm text-app-muted">{
              period === 'session' && current.reason_code === 'opening_missing' ? 'Session opening balance unavailable. Choose reporting dates to compare daily observations.' : reasons[current.reason_code]
            }</p>}
          {current.rows.length > 0 && (
            <div className="overflow-x-auto rounded-button border border-app-border">
              <table aria-label="Cash balance changes by asset" className="w-full text-left text-sm">
                <thead className="bg-app-surface-muted text-xs text-app-muted">
                  <tr>{['Asset', 'Opening cash', 'Closing cash', 'Change'].map((title) => <th key={title} scope="col" className="px-3 py-2">{title}</th>)}</tr>
                </thead>
                <tbody className="divide-y divide-app-border">
                  {current.rows.map((row) => (
                    <tr key={row.currency}>
                      <th scope="row" className="px-3 py-2">{row.currency}</th>
                      <td className="px-3 py-2">
                        <span className="whitespace-nowrap font-mono">{amount(row.opening_cash, row.currency)}</span>
                        {row.opening_observed_at && <time className="block text-xs text-app-muted" dateTime={row.opening_observed_at}>{observationTime(row.opening_observed_at, current.reporting_timezone)}</time>}
                      </td>
                      <td className="px-3 py-2">
                        <span className="whitespace-nowrap font-mono">{amount(row.closing_cash, row.currency)}</span>
                        {row.closing_observed_at && <time className="block text-xs text-app-muted" dateTime={row.closing_observed_at}>{observationTime(row.closing_observed_at, current.reporting_timezone)}</time>}
                      </td>
                      <td className="px-3 py-2">
                        <span className="whitespace-nowrap font-mono">{amount(row.delta_cash, row.currency, true)}</span>
                        {row.reason_code && <span className="block text-xs text-app-muted">{reasons[row.reason_code]}</span>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </section>
  )
}
