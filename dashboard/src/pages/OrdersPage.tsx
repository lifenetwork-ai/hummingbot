import { useState } from 'react'
import { BookOpen, CheckSquare, Layers, ListOrdered } from 'lucide-react'
import { useOrderDetail, useOrders, useFills } from '../features/history/useHistoryQueries'
import { FillsTable } from '../features/orders/FillsTable'
import { OrderDetailDrawer } from '../features/orders/OrderDetailDrawer'
import { OrderFiltersBar, type OrderFilters } from '../features/orders/OrderFiltersBar'
import { RecordedOrdersTable } from '../features/orders/RecordedOrdersTable'
import { WorkingOrdersTable } from '../features/orders/WorkingOrdersTable'
import { SectionLabel } from '../shared/components/SectionLabel'
import { MotionPage } from '../shared/motion/MotionPage'

export type OrdersTab = 'working' | 'recorded' | 'fills'

export function OrdersPage() {
  const [activeTab, setActiveTab] = useState<OrdersTab>('working')
  const [filters, setFilters] = useState<OrderFilters>({ market: 'all', state: 'all' })
  const [selectedOrderId, setSelectedOrderId] = useState<string | null>(null)

  const ordersQuery = useOrders({ query: {
    market: filters.market,
    state: filters.state,
    limit: 50,
  } }, activeTab === 'recorded')
  const workingOrdersQuery = useOrders({ query: { market: filters.market, state: 'open', limit: 50 } }, activeTab === 'working')

  const fillsQuery = useFills({ query: { market: filters.market, limit: 50 } }, activeTab === 'fills')
  const detailQuery = useOrderDetail(selectedOrderId ?? undefined, 50)
  const linkedFillsQuery = useFills({ query: { order_id: selectedOrderId ?? undefined, limit: 50 } }, Boolean(selectedOrderId))

  const orders = ordersQuery.data?.pages.flatMap((page) => page.items) ?? []
  const workingOrders = workingOrdersQuery.data?.pages.flatMap((page) => page.items) ?? []
  const fills = fillsQuery.data?.pages.flatMap((page) => page.items) ?? []
  const linkedFills = linkedFillsQuery.data?.pages.flatMap((page) => page.items) ?? []
  const selectedOrder = orders.find((order) => order.id === selectedOrderId) ?? workingOrders.find((order) => order.id === selectedOrderId) ?? null

  return (
    <MotionPage>
      <div className="mx-auto w-full max-w-[1536px] px-4 pb-16 pt-4 sm:px-6 sm:pt-6 md:px-8 lg:px-10">
        {/* Header */}
        <div className="mb-6">
          <SectionLabel tone="brand">Orders & Fills</SectionLabel>
          <h1 className="mt-1 font-heavy text-2xl font-bold tracking-tight text-app-text md:text-3xl flex items-center gap-2.5">
            <ListOrdered size={26} className="text-app-action" />
            <span>Order Book & Trade History (Orders & Fills)</span>
          </h1>
          <p className="mt-2 text-sm text-app-muted">
            Look up orders and fills recorded by the bot. An open status in history does not confirm the order is currently resting on the exchange.
          </p>
        </div>

        {/* Filters */}
        <div className="mb-4">
          <OrderFiltersBar filters={filters} onFiltersChange={(next) => { setSelectedOrderId(null); setFilters(next) }} />
        </div>

        {/* Tab selection */}
        <div className="mb-4 border-b border-app-border">
          <nav className="flex space-x-2" aria-label="Orders tabs">
            <button
              type="button"
              onClick={() => setActiveTab('working')}
              className={`inline-flex items-center gap-2 border-b-2 px-4 py-2.5 text-xs font-bold transition-colors ${
                activeTab === 'working'
                  ? 'border-app-action text-app-action'
                  : 'border-transparent text-app-muted hover:border-app-border hover:text-app-text'
              }`}
            >
              <Layers size={14} />
            <span>Open Orders in Bot Data</span>
            </button>

            <button
              type="button"
              onClick={() => setActiveTab('recorded')}
              className={`inline-flex items-center gap-2 border-b-2 px-4 py-2.5 text-xs font-bold transition-colors ${
                activeTab === 'recorded'
                  ? 'border-app-action text-app-action'
                  : 'border-transparent text-app-muted hover:border-app-border hover:text-app-text'
              }`}
            >
              <BookOpen size={14} />
            <span>Recorded Order History</span>
            </button>

            <button
              type="button"
              onClick={() => setActiveTab('fills')}
              className={`inline-flex items-center gap-2 border-b-2 px-4 py-2.5 text-xs font-bold transition-colors ${
                activeTab === 'fills'
                  ? 'border-app-action text-app-action'
                  : 'border-transparent text-app-muted hover:border-app-border hover:text-app-text'
              }`}
            >
              <CheckSquare size={14} />
              <span>Fill History</span>
            </button>
          </nav>
        </div>

        {/* Tab Views */}
        {activeTab === 'working' && (
          <>
            <p className="mb-3 text-xs text-app-muted">Orders with an open status in the bot order book; this API is not a snapshot of orders currently resting on the exchange.</p>
            <WorkingOrdersTable orders={workingOrders} isLoading={workingOrdersQuery.isLoading} error={workingOrdersQuery.error}
              nextCursor={workingOrdersQuery.hasNextPage} isFetchingNextPage={workingOrdersQuery.isFetchingNextPage}
              onNextPage={() => void workingOrdersQuery.fetchNextPage()} onSelectOrder={(order) => setSelectedOrderId(order.id)} />
          </>
        )}

        {activeTab === 'recorded' && (
          <RecordedOrdersTable
            orders={orders}
            nextCursor={ordersQuery.hasNextPage}
            isFetchingNextPage={ordersQuery.isFetchingNextPage}
            error={ordersQuery.error}
            isLoading={ordersQuery.isLoading}
            onNextPage={() => void ordersQuery.fetchNextPage()}
            onSelectOrder={(order) => setSelectedOrderId(order.id)}
          />
        )}

        {activeTab === 'fills' && (
          <FillsTable
            fills={fills}
            nextCursor={fillsQuery.hasNextPage}
            isFetchingNextPage={fillsQuery.isFetchingNextPage}
            error={fillsQuery.error}
            onNextPage={() => void fillsQuery.fetchNextPage()}
            isLoading={fillsQuery.isLoading}
          />
        )}

        {/* Order Detail Drawer */}
        <OrderDetailDrawer
          open={Boolean(selectedOrder)}
          onClose={() => setSelectedOrderId(null)}
          order={selectedOrder}
          detail={detailQuery.data}
          isDetailLoading={detailQuery.isLoading}
          detailError={detailQuery.error}
          fills={linkedFills}
          fillsLoading={linkedFillsQuery.isLoading}
          fillsError={linkedFillsQuery.error}
          fillsHasNextPage={linkedFillsQuery.hasNextPage}
          fillsFetchingNextPage={linkedFillsQuery.isFetchingNextPage}
          onNextFillsPage={() => void linkedFillsQuery.fetchNextPage()}
        />
      </div>
    </MotionPage>
  )
}
