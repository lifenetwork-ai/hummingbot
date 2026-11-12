import { ArrowDownRight, ArrowUpRight, Eye } from 'lucide-react'
import type { components } from '../../core/api/schema'
import { Badge } from '../../shared/components/Badge'
import { Button } from '../../shared/components/Button'
import { CopyableId } from '../../shared/components/CopyableId'
import { formatLife, formatPrice } from '../../shared/utils/formatters'

export interface WorkingOrdersTableProps {
  orders: components['schemas']['OrderViewOutput'][]
  isLoading?: boolean
  nextCursor?: boolean
  isFetchingNextPage?: boolean
  error?: Error | null
  onNextPage?: () => void
  onSelectOrder: (order: components['schemas']['OrderViewOutput']) => void
}

export function WorkingOrdersTable({ orders, isLoading, nextCursor, isFetchingNextPage, error, onNextPage, onSelectOrder }: WorkingOrdersTableProps) {
  return (
    <div className="overflow-x-auto rounded-card border border-app-border bg-app-surface">
      <table className="w-full min-w-[700px] text-left text-xs">
        <thead>
          <tr className="border-b border-app-border bg-app-surface-muted/60 text-app-muted font-semibold">
            <th className="p-3">Order ID</th>
            <th className="p-3">Placed At</th>
            <th className="p-3">Market</th>
            <th className="p-3">Pair</th>
            <th className="p-3">Side</th>
            <th className="p-3">Order Price</th>
            <th className="p-3">Original Amount</th>
            <th className="p-3">Recorded Status</th>
            <th className="p-3 text-right">Details</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-app-border/60">
          {isLoading ? (
            <tr>
              <td colSpan={9} className="p-8 text-center text-app-muted">
                Loading open orders in bot data...
              </td>
            </tr>
          ) : error ? (
            <tr><td colSpan={9} className="p-8 text-center text-app-danger"><span role="alert">Failed to load open orders in bot data. Try reloading or check permissions.</span></td></tr>
          ) : orders.length === 0 ? (
            <tr>
              <td colSpan={9} className="p-8 text-center text-app-muted">
                No open orders found on this results page.
              </td>
            </tr>
          ) : (
            orders.map((o) => {
              const isBuy = o.side === 'BUY'
              return (
                <tr key={o.id} className="hover:bg-app-surface-muted/30 transition-colors">
                  <td className="p-3">
                    <CopyableId
                      id={o.id}
                      truncateLength={8}
                      copyLabel="Copy order ID"
                      codeClassName="text-[11px] text-app-muted"
                    />
                  </td>
                  <td className="p-3 font-mono text-app-muted">
                    {new Date(o.created_at).toLocaleString()}
                  </td>
                  <td className="p-3 font-semibold uppercase text-app-muted">{o.market}</td>
                  <td className="p-3 font-mono font-bold text-app-text">{o.pair}</td>
                  <td className="p-3">
                    <Badge tone={isBuy ? 'success' : 'danger'} className="gap-0.5 text-[10px] font-bold">
                      {isBuy ? <ArrowUpRight size={11} /> : <ArrowDownRight size={11} />}
                      <span>{o.side}</span>
                    </Badge>
                  </td>
                  <td className="p-3 font-mono font-bold text-app-text">{formatPrice(o.price)}</td>
                  <td className="p-3 font-mono text-app-text">{formatLife(o.amount)}</td>
                  <td className="p-3"><Badge tone="neutral" className="text-[10px]">{o.status}</Badge></td>

                  <td className="p-3 text-right">
                    <button
                      type="button"
                      onClick={() => onSelectOrder(o)}
                      className="inline-flex items-center gap-1 rounded-button bg-app-surface-muted px-2 py-1 text-[11px] font-semibold text-app-action hover:bg-app-accent/10 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-app-action"
                    >
                      <Eye size={12} />
                      <span>View</span>
                    </button>
                  </td>
                </tr>
              )
            })
          )}
        </tbody>
      </table>
      {nextCursor && onNextPage && (
        <div className="flex justify-center border-t border-app-border p-3">
          <Button variant="secondary" onClick={onNextPage} disabled={isFetchingNextPage} className="h-8 px-3 text-xs gap-1">
            <span>{isFetchingNextPage ? 'Loading...' : 'Load more recorded orders'}</span>
          </Button>
        </div>
      )}
    </div>
  )
}
