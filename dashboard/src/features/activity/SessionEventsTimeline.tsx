import { Activity, ChevronRight, Clock } from 'lucide-react'
import type { components } from '../../core/api/schema'
import type { EventFilters } from '../history/filters'
import { Badge } from '../../shared/components/Badge'
import { Button } from '../../shared/components/Button'
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
  sessions?: Pick<components['schemas']['SessionSummaryOutput'], 'id' | 'profile_id' | 'status'>[]
  selectedSessionId?: string
  onSelectSession?: (id: string | undefined) => void
}

export function SessionEventsTimeline({
  events = [],
  isLoading,
  filters,
  onFilterChange,
  validationErrors,
  hasMore,
  onLoadMore,
  sessions,
  selectedSessionId,
  onSelectSession,
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
          sessions={sessions}
          selectedSessionId={selectedSessionId}
          onSelectSession={onSelectSession}
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
                  <p className="mt-1 text-app-muted text-[11px] leading-relaxed font-mono">
                    {JSON.stringify(ev.payload)}
                  </p>
                )}
              </li>
            ))}
          </ol>
        )}
      </div>

      {/* Pagination */}
      {hasMore && onLoadMore && (
        <div className="flex justify-center pt-2">
          <Button
            variant="secondary"
            onClick={onLoadMore}
            disabled={isLoading}
            className="h-8 px-4 text-xs gap-1"
          >
            <span>{isLoading ? 'Loading more…' : 'Load more events'}</span>
            <ChevronRight size={14} />
          </Button>
        </div>
      )}
    </Surface>
  )
}
