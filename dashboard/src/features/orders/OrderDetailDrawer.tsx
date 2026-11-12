import { AnimatePresence, motion, useReducedMotion } from 'framer-motion'
import { ArrowDownRight, ArrowUpRight, X } from 'lucide-react'
import { useEffect, useRef } from 'react'
import type { components } from '../../core/api/schema'
import { Badge } from '../../shared/components/Badge'
import { Button } from '../../shared/components/Button'
import { CopyableId } from '../../shared/components/CopyableId'
import { formatLife, formatPrice } from '../../shared/utils/formatters'

export interface OrderDetailDrawerProps {
  open: boolean
  onClose: () => void
  order: components['schemas']['OrderViewOutput'] | null
  detail?: components['schemas']['OrderDetailOutput']
  isDetailLoading?: boolean
  detailError?: Error | null
  fills?: components['schemas']['FillViewOutput'][]
  fillsLoading?: boolean
  fillsError?: Error | null
  fillsHasNextPage?: boolean
  fillsFetchingNextPage?: boolean
  onNextFillsPage?: () => void
}

export function OrderDetailDrawer({ open, onClose, order, detail, isDetailLoading, detailError, fills = [], fillsLoading, fillsError, fillsHasNextPage, fillsFetchingNextPage, onNextFillsPage }: OrderDetailDrawerProps) {
  const drawerRef = useRef<HTMLDivElement>(null)
  const reducedMotion = useReducedMotion()

  useEffect(() => {
    if (!open) return
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [open, onClose])

  if (!open || !order) return null

  const actual = detail?.order ?? order
  const isBuy = actual.side === 'BUY'

  return (
    <AnimatePresence>
      <div className="fixed inset-0 z-50 overflow-hidden bg-black/40 backdrop-blur-xs flex justify-end">
        {/* Backdrop click */}
        <div className="absolute inset-0" onClick={onClose} aria-hidden="true" />

        {/* Drawer panel */}
        <motion.div
          ref={drawerRef}
          role="dialog"
          aria-modal="true"
          aria-labelledby="order-drawer-title"
          initial={reducedMotion ? false : { x: '100%' }}
          animate={{ x: 0 }}
          exit={reducedMotion ? undefined : { x: '100%' }}
          transition={{ duration: reducedMotion ? 0 : 0.2 }}
          className="relative z-10 w-full max-w-md bg-app-surface border-l border-app-border h-full overflow-y-auto p-5 shadow-2xl flex flex-col justify-between"
        >
          <div>
            {/* Header */}
            <div className="flex items-center justify-between border-b border-app-border/80 pb-3">
              <div>
                <span className="text-xs font-semibold uppercase tracking-wider text-app-muted">
                  Order Details
                </span>
                <h3 id="order-drawer-title" className="font-heavy text-lg font-bold text-app-text">
                  {actual.pair}
                </h3>
              </div>
              <button
                type="button"
                onClick={onClose}
                aria-label="Close order details"
                className="grid h-8 w-8 place-items-center rounded-button text-app-muted hover:bg-app-surface-muted hover:text-app-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-app-action"
              >
                <X size={18} />
              </button>
            </div>

            {/* Quick badges */}
            <div className="mt-4 flex items-center gap-2">
              <Badge tone={isBuy ? 'success' : 'danger'} className="gap-1 font-bold">
                {isBuy ? <ArrowUpRight size={13} /> : <ArrowDownRight size={13} />}
                <span>{order.side}</span>
              </Badge>
              <Badge tone="neutral" className="uppercase text-[10px]">
                {actual.market}
              </Badge>
              <Badge tone={actual.status === 'FILLED' ? 'success' : actual.status === 'CANCELED' ? 'neutral' : 'warning'} className="text-[10px]">
                {actual.status}
              </Badge>
            </div>

            {/* Specs Grid */}
            <div className="mt-5 space-y-3 rounded-card border border-app-border/80 bg-app-surface-muted/30 p-3.5 text-xs">
              <div className="flex justify-between items-center gap-2">
                <span className="text-app-muted shrink-0">Internal Order ID:</span>
                <CopyableId id={actual.id} copyLabel="Copy internal order ID" />
              </div>
              <div className="flex justify-between items-center gap-2">
                <span className="text-app-muted shrink-0">Exchange Order ID:</span>
                {actual.exchange_order_id ? (
                  <CopyableId id={actual.exchange_order_id} copyLabel="Copy exchange order ID" />
                ) : (
                  <code className="font-mono text-app-muted">Not received</code>
                )}
              </div>
              <div className="flex justify-between border-t border-app-border/40 pt-2">
                <span className="text-app-muted">Order Price:</span>
                <span className="font-mono font-bold text-app-text">{formatPrice(actual.price)}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-app-muted">Original Amount:</span>
                <span className="font-mono font-bold text-app-text">{formatLife(actual.amount)}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-app-muted">Filled Amount:</span>
                <span className="font-mono text-app-text font-semibold">{formatLife(actual.recorded_filled_amount)}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-app-muted">Remaining Amount:</span>
                <span className="font-mono font-bold text-app-text">{formatLife(actual.recorded_remaining_amount)}</span>
              </div>
              <div className="flex justify-between border-t border-app-border/40 pt-2">
                <span className="text-app-muted">Created At:</span>
                <span className="font-mono text-app-muted">{new Date(actual.created_at).toLocaleString('en-US')}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-app-muted">Last Updated:</span>
                <span className="font-mono text-app-muted">{new Date(actual.updated_at).toLocaleString('en-US')}</span>
              </div>
            </div>

            {/* Progress Basis Explanation */}
            <div className="mt-4 rounded-button bg-app-surface-muted/60 p-3 text-xs text-app-muted">
              <strong>Progress Basis: </strong>
              <span className="font-mono">{actual.progress_basis || 'recorded_fills_only'}</span>
              <p className="mt-1">
                Remaining amount is original amount minus bot-recorded fills; this does not confirm open amount on the exchange and may remain positive after cancellation.
              </p>
            </div>
            {isDetailLoading && <p role="status" className="mt-3 text-xs text-app-muted">Loading detailed timeline...</p>}
            {detailError && <p role="alert" className="mt-3 text-xs text-app-danger">Failed to load timeline. Recorded order data above remains available as historical data.</p>}
            {detail && (
              <ol aria-label="Order state history" className="mt-3 space-y-2 text-xs text-app-muted">
                {detail.timeline.map((item) => <li key={item.id} className="flex justify-between gap-3"><span>{item.status}</span><time dateTime={item.timestamp}>{new Date(item.timestamp).toLocaleString('en-US')}</time></li>)}
                {detail.timeline_truncated && <li>Earlier timeline events exist beyond the page limit.</li>}
              </ol>
            )}
            <section aria-label="Linked recorded fills" className="mt-4 border-t border-app-border/60 pt-3">
              <h4 className="text-xs font-bold text-app-text">Recorded Fills</h4>
              {fillsLoading ? <p role="status" className="mt-2 text-xs text-app-muted">Loading linked fills...</p>
                : fillsError ? <p role="alert" className="mt-2 text-xs text-app-danger">Failed to load fills; fill counts in order list are preserved separately.</p>
                  : fills.length === 0 ? <p className="mt-2 text-xs text-app-muted">No recorded fills for this order.</p>
                    : <ul className="mt-2 space-y-1 text-xs">{fills.map((fill) => <li key={fill.id} className="flex justify-between gap-2"><time className="text-app-muted" dateTime={fill.timestamp}>{new Date(fill.timestamp).toLocaleString('en-US')}</time><span>{fill.side} · {formatLife(fill.amount)} @ {formatPrice(fill.price)}</span><span className="font-mono">{(fill.fee?.flat_fees ?? []).map((fee) => `${fee.amount} ${fee.token}`).join(', ') || (fill.fee_in_quote ?? '—')}</span></li>)}</ul>}
              {fillsHasNextPage && onNextFillsPage && <Button variant="secondary" className="mt-2 h-8 px-3 text-xs" disabled={fillsFetchingNextPage} onClick={onNextFillsPage}>{fillsFetchingNextPage ? 'Loading...' : 'Load more fills'}</Button>}
            </section>
          </div>

          <div className="pt-4 border-t border-app-border/80">
            <button
              type="button"
              onClick={onClose}
              className="w-full rounded-button border border-app-border bg-app-surface px-4 py-2 text-xs font-semibold text-app-text hover:bg-app-surface-muted"
            >
              Close
            </button>
          </div>
        </motion.div>
      </div>
    </AnimatePresence>
  )
}
