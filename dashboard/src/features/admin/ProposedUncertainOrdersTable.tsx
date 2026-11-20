import { Info } from 'lucide-react'
import type { components } from '../../core/api/schema'
import { Badge } from '../../shared/components/Badge'

export interface ProposedUncertainOrdersTableProps {
  orders: components['schemas']['RecoveryUncertainOrderRepairOutput'][]
}

export function ProposedUncertainOrdersTable({ orders }: ProposedUncertainOrdersTableProps) {
  if (orders.length === 0) return null

  return (
    <div className="mt-5 space-y-3">
      <div className="flex items-center justify-between border-b border-app-border/60 pb-2">
        <h4 className="font-heavy text-sm font-bold text-app-text">
          Proposed Uncertain Order Repairs ({orders.length})
        </h4>
        <span className="text-[10px] text-app-muted">Local ledger review</span>
      </div>

      <div className="overflow-x-auto rounded-card border border-app-border">
        <table className="w-full min-w-[640px] text-left text-xs">
          <thead>
            <tr className="border-b border-app-border bg-app-surface-muted/30 text-app-muted font-semibold">
              <th className="p-2.5">Order ID</th>
              <th className="p-2.5">Pair / Connector</th>
              <th className="p-2.5">Side</th>
              <th className="p-2.5">Amount</th>
              <th className="p-2.5">Price</th>
              <th className="p-2.5">Transition (Before → After)</th>
              <th className="p-2.5">Evidence & Deadline</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-app-border/60 font-mono">
            {orders.map((order) => (
              <tr key={order.id} className="hover:bg-app-surface-muted/20">
                <td className="p-2.5 font-bold text-app-text">{order.id}</td>
                <td className="p-2.5 text-app-muted">
                  <span className="text-app-text font-semibold">{order.pair}</span>{' '}
                  <span className="text-[10px] uppercase">({order.connector})</span>
                </td>
                <td className="p-2.5">
                  <Badge tone={order.side === 'BUY' ? 'success' : 'danger'} className="text-[10px]">
                    {order.side}
                  </Badge>
                </td>
                <td className="p-2.5 text-app-text">{order.amount}</td>
                <td className="p-2.5 text-app-muted">{order.price}</td>
                <td className="p-2.5">
                  <span className="text-app-muted">{order.before}</span>{' '}
                  <span className="text-app-action font-semibold">→</span>{' '}
                  <span className="font-bold text-app-action">{order.after}</span>
                </td>
                <td className="p-2.5 text-[11px] font-sans text-app-muted">
                  <div>{order.evidence}</div>
                  <div className="text-[10px] text-app-muted/80">
                    Deadline: {new Date(order.deadline_ms).toLocaleString('en-US')}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>

        <div className="bg-app-surface-muted/30 p-2.5 border-t border-app-border/60 text-[11px] text-app-muted space-y-1">
          <div className="flex items-start gap-1.5">
            <Info size={13} className="shrink-0 mt-0.5 text-app-action" />
            <p className="leading-relaxed">
              <strong>Local Reconciliation Note: </strong>
              This procedure resolves local ledger state after exchange reconciliation. It transitions local order state to OrderFailure; it does not submit, cancel, or sell orders on the exchange. Start remains a separate operation.
            </p>
          </div>
        </div>
      </div>
    </div>
  )
}
