import { Activity, ChevronRight, Clock } from 'lucide-react'
import type { components } from '../../core/api/schema'
import type { EventFilters } from '../history/filters'
import { Badge } from '../../shared/components/Badge'
import { CursorPagination } from '../../shared/components/CursorPagination'
import { Surface } from '../../shared/components/Surface'
import { HistoryFiltersBar } from './HistoryFiltersBar'

export interface SessionEventsTimelineProps {
  events?: components['schemas']['MMEventViewOutput'][]
  isLoading?: boolean
  filters?: EventFilters
  validationErrors?: Record<string, string>
  onFilterChange?: (filters: EventFilters) => void
  hasMore?: boolean
  onLoadMore?: () => void
}

export function SessionEventsTimeline({
  events = [],
  isLoading,
  filters,
  onFilterChange,
  validationErrors,
  hasMore,
  onLoadMore,
}: SessionEventsTimelineProps) {
  return (
    <Surface className="p-5 md:p-6 space-y-4">
      <div className="border-b border-app-border/80 pb-3">
        <div className="flex items-center gap-2">
          <Activity size={18} className="text-app-action" />
          <h3 className="font-heavy text-lg font-bold text-app-text">
            Session Events Timeline
          </h3>
        </div>
        <p className="mt-1 text-xs text-app-muted">
          State transitions, skew alerts, and operational heartbeats recorded in real-time by the bot.
        </p>
      </div>

      {/* Filter Bar */}
      {filters && onFilterChange && (
        <HistoryFiltersBar
          mode="events"
          filters={filters}
          onChange={onFilterChange} validationErrors={validationErrors}
        />
      )}

      <div className="mt-2">
        {isLoading ? (
          <div className="p-8 text-center text-xs text-app-muted">
            Loading session events…
          </div>
        ) : events.length === 0 ? (
          <div className="p-8 text-center text-xs text-app-muted">
            No session events recorded yet.
          </div>
        ) : (
          <ol className="relative border-l border-app-border/80 ml-3 space-y-4 text-xs">
            {events.map((ev, idx) => (
              <li key={ev.id || idx} className="ml-4">
                <div className="absolute -left-1.5 mt-1.5 h-3 w-3 rounded-full border border-app-surface bg-app-action" />
                <div className="flex flex-wrap items-center gap-2">
                  <Badge tone="brand" className="text-[10px] font-mono">
                    {ev.event_type}
                  </Badge>
                  <span className="font-mono text-[11px] text-app-muted flex items-center gap-1">
                    <Clock size={11} />
                    <span>{ev.created_at ? new Date(ev.created_at).toLocaleString() : '—'}</span>
                  </span>
                  {ev.source && (
                    <span className="text-[10px] text-app-muted">
                      source: {ev.source}
                    </span>
                  )}
                </div>
                {ev.new_profile && (
                  <p className="mt-1 text-app-text leading-relaxed">
                    Switched to Profile: <strong className="font-mono">{ev.new_profile}</strong>
                  </p>
                )}
                {ev.payload && typeof ev.payload === 'object' && Object.keys(ev.payload).length > 0 && (
                  <details className="group mt-1">
                    <summary className="inline-flex cursor-pointer select-none list-none items-center gap-1 text-[10px] font-mono text-app-muted transition-colors hover:text-app-text [&::-webkit-details-marker]:hidden">
                      <ChevronRight size={11} className="transition-transform group-open:rotate-90" />
                      <span>payload ({Object.keys(ev.payload).length} keys)</span>
                    </summary>
                    <pre className="mt-1 max-h-64 max-w-full overflow-auto rounded-card border border-app-border/60 bg-app-surface px-3 py-2 text-[11px] leading-relaxed font-mono text-app-muted whitespace-pre-wrap break-all">
                      {JSON.stringify(ev.payload, null, 2)}
                    </pre>
                  </details>
                )}
              </li>
            ))}
          </ol>
        )}
      </div>

      {/* Pagination */}
      <CursorPagination
        itemLabel="event"
        hasMore={hasMore}
        isLoading={isLoading}
        onLoadMore={onLoadMore}
      />
    </Surface>
  )
}
