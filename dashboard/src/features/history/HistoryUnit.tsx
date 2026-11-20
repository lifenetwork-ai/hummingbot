import { useHistoryController } from './useHistoryController'
import { CommandHistoryList } from '../activity/CommandHistoryList'
import { PinnedSessionDetailView } from '../activity/PinnedSessionDetailView'
import { SessionEventsTimeline } from '../activity/SessionEventsTimeline'

export type HistoryUnitMode = 'events' | 'commands' | 'none'

/** Owns Activity history reads, filters and command receipt inspection for one route. */
export function HistoryUnit({ active, currentSessionId, onAuditForOperation }: {
  active: HistoryUnitMode
  currentSessionId?: string
  onAuditForOperation: (operationId: string) => void
}) {
  const history = useHistoryController({ active, currentSessionId })
  const events = history.events.data?.pages.flatMap((page) => page.items) ?? []
  const commands = history.commands.data?.pages.flatMap((page) => page.items) ?? []

  return (
    <>
      {active === 'events' && history.events.error && <p role="alert">{history.events.error.message}</p>}
      {active === 'commands' && history.commands.error && <p role="alert">{history.commands.error.message}</p>}
      {active === 'events' && (
        <>
          <SessionEventsTimeline events={events} isLoading={history.events.isLoading}
            filters={history.filters.events} onFilterChange={history.changeEvents}
            validationErrors={history.validation.events.errors} hasMore={history.events.hasNextPage}
            onLoadMore={() => { void history.loadMoreEvents() }} />
          <PinnedSessionDetailView className="mt-6" sessionId={history.sessionId} pinnedConfig={history.pinned.data}
            isLoading={history.pinned.isLoading} error={history.pinned.error?.message} />
        </>
      )}
      {active === 'commands' && (
        <CommandHistoryList commands={commands} isLoading={history.commands.isLoading}
          filters={history.filters.commands} onFilterChange={history.changeCommands}
          validationErrors={history.validation.commands.errors} commandLinks={history.commandLinks}
          onInspectCommand={history.inspectCommand} onAuditForOperation={(operationId) => { history.closeInspection(); onAuditForOperation(operationId) }}
          selectedReceipt={history.selectedReceipt} inspection={history.inspection ?? undefined}
          onCloseInspection={history.closeInspection} hasMore={history.commands.hasNextPage}
          onLoadMore={() => { void history.loadMoreCommands() }} />
      )}
    </>
  )
}
