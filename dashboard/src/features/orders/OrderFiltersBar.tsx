import { Filter } from 'lucide-react'
import { Select } from '../../shared/components/Select'

export interface OrderFilters {
  market: 'all' | 'spot' | 'perpetual'
  state: 'all' | 'open' | 'closed'
}

export interface OrderFiltersBarProps {
  filters: OrderFilters
  onFiltersChange: (nextFilters: OrderFilters) => void
}

export function OrderFiltersBar({ filters, onFiltersChange }: OrderFiltersBarProps) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 rounded-card border border-app-border bg-app-surface p-3 text-xs">
      <div className="flex flex-wrap items-center gap-2">
        <span className="flex items-center gap-1 font-semibold text-app-muted">
          <Filter size={14} />
          <span>Market:</span>
        </span>
        <div className="inline-flex rounded-button bg-app-surface-muted p-0.5 border border-app-border/60">
          {[
            { id: 'all' as const, label: 'All' },
            { id: 'spot' as const, label: 'Spot' },
            { id: 'perpetual' as const, label: 'Perpetual' },
          ].map((m) => (
            <button
              key={m.id}
              type="button"
              onClick={() => onFiltersChange({ ...filters, market: m.id })}
              className={`rounded-button px-2.5 py-1 text-xs font-semibold transition-all ${
                filters.market === m.id
                  ? 'bg-app-surface text-app-action shadow-xs'
                  : 'text-app-muted hover:text-app-text'
              }`}
            >
              {m.label}
            </button>
          ))}
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <span className="font-semibold text-app-muted">State:</span>
        <Select
          sizeVariant="sm"
          aria-label="State filter"
          value={filters.state}
          onChange={(e) =>
            onFiltersChange({
              ...filters,
              state: e.target.value as OrderFilters['state'],
            })
          }
          options={[
            { value: 'all', label: 'All states' },
            { value: 'open', label: 'Open in bot data' },
            { value: 'closed', label: 'Closed in bot data' },
          ]}
          containerClassName="w-auto"
        />
      </div>
    </div>
  )
}
