import type { ReadResponse } from '../../core/api/admin'
import { Dialog } from '../../shared/components/Dialog'
import type { Source } from './model'

type Props = {
  open: boolean
  onClose: () => void
  source: Source<ReadResponse<'getBalances'>>
  onRefresh: () => unknown
}

export function AccountBalancesPanel({ open, onClose, source, onRefresh }: Props) {
  const accounts = source.data?.accounts ?? []
  return <Dialog open={open} onClose={onClose} placement="right" className="sm:max-w-5xl" title="Account balances" description="Balances in each currency’s native units.">
    <div className="flex items-center justify-between gap-3 text-xs text-app-muted">
      <span>{source.data?.server_time ? `Snapshot: ${source.data.server_time}` : 'Snapshot time unavailable'}</span>
      <button type="button" disabled={source.refreshing} onClick={() => { void onRefresh() }} className="min-h-11 shrink-0 rounded-button px-3 text-app-action hover:bg-app-surface-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-app-action disabled:opacity-50">{source.refreshing ? 'Refreshing…' : 'Refresh'}</button>
    </div>
    {source.phase === 'loading' && !source.data && <p role="status" className="mt-4 text-sm text-app-muted">Loading balances…</p>}
    {source.phase === 'error' && <p role="alert" className="mt-4 text-sm text-app-danger">Unable to refresh balances. {source.data ? 'Showing the last snapshot.' : 'Try refreshing again.'}</p>}
    {source.data && !source.data.owner_available && <p role="status" className="mt-4 text-sm text-app-muted">Balance source unavailable. Snapshot is unverified.</p>}
    {source.phase === 'ready' && accounts.length === 0 && <p className="mt-4 text-sm text-app-muted">No account balances reported.</p>}
    <div className="mt-5 space-y-6">
      {accounts.map((account, index) => <section key={`${account.account_id ?? 'unknown'}-${index}`} aria-label={`Account ${account.account_id ?? 'unverified'}`}>
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2 text-xs">
          <h3 className="break-all font-semibold text-app-text">Account {account.account_id ?? 'unverified'}</h3>
          <span className="rounded-pill bg-app-surface-muted px-2 py-1 text-app-muted">{account.environment}</span>
        </div>
        {!account.identity_verified && <p className="mb-3 text-xs text-app-muted">Account identity unverified</p>}
        {account.balances.length === 0 && <p className="text-sm text-app-muted">No currencies reported.</p>}
        {/* Keyboard focus lets users scroll the wide balance table without a pointer. */}
        {/* eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex */}
        {account.balances.length > 0 && <div className="overflow-x-auto rounded-button border border-app-border focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-app-action" role="region" aria-label={`Currency balances for ${account.account_id ?? 'unverified account'}`} tabIndex={0}>
          <table className="w-full min-w-[760px] text-xs">
            <caption className="sr-only">Balances in native currency units</caption>
            <thead className="bg-app-surface-muted text-app-muted">
              <tr>{['Currency', 'Cash balance', 'Available cash', 'Equity', 'Available equity'].map((label, index) => <th key={label} scope="col" className={`whitespace-nowrap px-3 py-3 font-medium ${index === 0 ? 'text-left' : 'text-right'}`}>{label}</th>)}</tr>
            </thead>
            <tbody>
              {account.balances.map((balance) => {
                const stale = source.phase !== 'ready' || !source.data?.owner_available || balance.stale || !balance.present_in_latest_snapshot
                return <tr key={`${balance.connector}-${balance.currency}`} className="border-t border-app-border align-top">
                  <th scope="row" className="px-3 py-4 text-left font-semibold">{balance.currency}{stale && <span className="mt-1 block whitespace-nowrap text-[10px] font-normal text-app-muted">Stale / unverified</span>}</th>
                  {[balance.cash_balance, balance.available_cash, balance.equity, balance.available_equity].map((value, index) => <td key={index} className="whitespace-nowrap px-3 py-4 text-right font-mono tabular-nums">{value ?? '—'}</td>)}
                </tr>
              })}
            </tbody>
          </table>
        </div>}
        <ul className="mt-3 space-y-2 text-[11px] leading-relaxed text-app-muted" aria-label="Balance observation details">
          {account.balances.map((balance) => <li key={`${balance.connector}-${balance.currency}`} className="break-words">
            <span className="font-semibold text-app-text">{balance.currency}</span> · Observed at: {balance.observed_at ?? '—'} · Connector: {balance.connector ?? '—'} · Source: {balance.source ?? '—'} · In latest snapshot: {balance.present_in_latest_snapshot ? 'Yes' : 'No'}
          </li>)}
        </ul>
      </section>)}
    </div>
  </Dialog>
}
