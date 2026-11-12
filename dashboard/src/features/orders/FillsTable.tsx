import { ArrowDownRight, ArrowUpRight } from 'lucide-react'
import type { components } from '../../core/api/schema'
import { Badge } from '../../shared/components/Badge'
import { CopyableId } from '../../shared/components/CopyableId'
import { formatLife, formatPrice } from '../../shared/utils/formatters'

export interface FillsTableProps {
  fills: components['schemas']['FillViewOutput'][]
  isLoading?: boolean
  nextCursor?: boolean
  isFetchingNextPage?: boolean
  error?: Error | null
  onNextPage?: () => void
}

export function FillsTable({ fills, isLoading, nextCursor, isFetchingNextPage, error, onNextPage }: FillsTableProps) {
  return (
    <div className="overflow-x-auto rounded-card border border-app-border bg-app-surface">
      <table className="w-full min-w-[700px] text-left text-xs">
        <thead>
          <tr className="border-b border-app-border bg-app-surface-muted/60 text-app-muted font-semibold">
            <th className="p-3">Time</th>
            <th className="p-3">Market</th>
            <th className="p-3">Pair</th>
            <th className="p-3">Side</th>
            <th className="p-3">Fill Price</th>
            <th className="p-3">Amount</th>
            <th className="p-3">Fee</th>
            <th className="p-3">Linked Order ID</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-app-border/60">
          {isLoading ? (
            <tr>
              <td colSpan={8} className="p-8 text-center text-app-muted">
                Loading fill history...
              </td>
            </tr>
          ) : error ? (
            <tr><td colSpan={8} className="p-8 text-center text-app-danger"><p role="alert">Failed to load recorded fills. Try reloading or check permissions.</p></td></tr>
          ) : fills.length === 0 ? (
            <tr>
              <td colSpan={8} className="p-8 text-center text-app-muted">
                No fills recorded in this session.
              </td>
            </tr>
          ) : (
            fills.map((f) => {
              const isBuy = f.side === 'BUY'
              const flatFees = f.fee?.flat_fees ?? []
              const feeText = flatFees.length > 0
                ? flatFees.map((fee) => `${fee.amount} ${fee.token}`).join(', ')
                : f.fee_in_quote !== null ? `${f.fee_in_quote} ${f.quote_currency}${f.conversion_status === 'legacy_ambiguous' ? ' (legacy)' : ''}` : '—'

              return (
                <tr key={f.id} className="hover:bg-app-surface-muted/30 transition-colors">
                  <td className="p-3 font-mono text-app-muted">
                    {f.timestamp ? new Date(f.timestamp).toLocaleString('en-US') : '—'}
                  </td>
                  <td className="p-3 font-semibold uppercase text-app-muted">{f.market}</td>
                  <td className="p-3 font-mono font-bold text-app-text">{f.pair}</td>
                  <td className="p-3">
                    <Badge tone={isBuy ? 'success' : 'danger'} className="gap-0.5 text-[10px] font-bold">
                      {isBuy ? <ArrowUpRight size={11} /> : <ArrowDownRight size={11} />}
                      <span>{f.side}</span>
                    </Badge>
                  </td>
                  <td className="p-3 font-mono font-bold text-app-text">{formatPrice(f.price)}</td>
                  <td className="p-3 font-mono text-app-text">{formatLife(f.amount)}</td>
                  <td className="p-3 font-mono text-app-muted text-[11px]">{feeText}</td>
                  <td className="p-3">
                    {f.order_id ? (
                      <CopyableId
                        id={f.order_id}
                        truncateLength={10}
                        copyLabel="Copy linked order ID"
                        codeClassName="text-[11px] text-app-muted"
                      />
                    ) : (
                      <span className="font-mono text-app-muted text-[11px]">—</span>
                    )}
                  </td>
                </tr>
              )
            })
          )}
        </tbody>
      </table>
      {nextCursor && onNextPage && (
        <div className="flex justify-center border-t border-app-border p-3">
          <button type="button" onClick={onNextPage} disabled={isFetchingNextPage}
            className="rounded-button border border-app-border px-3 py-2 text-xs font-semibold text-app-action disabled:opacity-50">
            {isFetchingNextPage ? 'Loading...' : 'Load more fills'}
          </button>
        </div>
      )}
    </div>
  )
}
