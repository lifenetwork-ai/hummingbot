import { CheckCircle2 } from 'lucide-react'
import type { components } from '../../core/api/schema'
import { Badge } from '../../shared/components/Badge'

export interface RepairedUncertainOrdersTableProps {
  orders: components['schemas']['RecoveryUncertainOrderRepairOutput'][]
}

export function RepairedUncertainOrdersTable({ orders }: RepairedUncertainOrdersTableProps) {
  if (orders.length === 0) return null

  return (
    <div className="mt-5 space-y-3">
      <div className="flex items-center justify-between border-b border-app-border/60 pb-2">
        <h4 className="flex items-center gap-1.5 font-heavy text-sm font-bold text-app-text">
          <CheckCircle2 size={16} className="text-app-success" />
          Repaired Uncertain Orders ({orders.length})
        </h4>
        <Badge tone="success" className="text-[10px]">
          Repaired
        </Badge>
      </div>

      <div className="overflow-x-auto rounded-card border border-app-success/30 bg-app-success/5">
        <table className="w-full min-w-[540px] text-left text-xs">
          <thead>
            <tr className="border-b border-app-border bg-app-surface-muted/30 text-app-muted font-semibold">
              <th className="p-2.5">Order ID</th>
              <th className="p-2.5">Pair</th>
              <th className="p-2.5">Side</th>
              <th className="p-2.5">Amount</th>
              <th className="p-2.5">Price</th>
              <th className="p-2.5">Result</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-app-border/60 font-mono">
            {orders.map((order) => (
              <tr key={order.id} className="hover:bg-app-surface-muted/20">
                <td className="p-2.5 font-bold text-app-text">{order.id}</td>
                <td className="p-2.5 text-app-text">{order.pair}</td>
                <td className="p-2.5">
                  <Badge tone={order.side === 'BUY' ? 'success' : 'danger'} className="text-[10px]">
                    {order.side}
                  </Badge>
                </td>
                <td className="p-2.5 text-app-text">{order.amount}</td>
                <td className="p-2.5 text-app-muted">{order.price}</td>
                <td className="p-2.5">
                  <span className="font-bold text-app-action">{order.after}</span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
