import { useEffect, useRef, useState } from 'react'
import { Activity, Clock, GitCompare, Terminal } from 'lucide-react'
import { notify } from '../core/feedback/notify'
import { useAdminQuery } from '../core/api/hooks'
import { useReconciliation } from '../features/reconciliation/useReconciliation'
import { useHistoryController } from '../features/history/useHistoryController'
import { PinnedSessionDetailView } from '../features/activity/PinnedSessionDetailView'
import { SecurityAuditView } from '../features/admin/SecurityAuditView'
import { Button } from '../shared/components/Button'
import { useOperationalContext } from '../features/operational-context/useOperationalContext'
import { CommandHistoryList } from '../features/activity/CommandHistoryList'
import { ReconciliationPanel } from '../features/activity/ReconciliationPanel'
import { SessionEventsTimeline } from '../features/activity/SessionEventsTimeline'
import { CopyableId } from '../shared/components/CopyableId'
import { SectionLabel } from '../shared/components/SectionLabel'
import { MotionPage } from '../shared/motion/MotionPage'

export type ActivityTab = 'reconciliation' | 'events' | 'commands' | 'audit'

export function ActivityPage() {
  const { status } = useOperationalContext()
  const [activeTab, setActiveTab] = useState<ActivityTab>('reconciliation')

  const currentSessionId = status.data?.observed_session?.id || status.data?.session?.id || ''

  const auditTabRef = useRef<HTMLButtonElement>(null)
  const focusAudit = useRef(false)
  useEffect(() => { if (activeTab === 'audit' && focusAudit.current) { auditTabRef.current?.focus(); focusAudit.current = false } }, [activeTab])
  const [checkError, setCheckError] = useState<string | null>(null)
  const reconciliation = useReconciliation()
  const exactCheck = useAdminQuery('getReconciliationById', reconciliation.receipt ? { path: { check_id: reconciliation.receipt.reference.id } } : undefined)
  const history = useHistoryController({ active: activeTab === 'reconciliation' ? 'none' : activeTab, currentSessionId: currentSessionId || undefined })
  const eventsQuery = history.events
  const commandsQuery = history.commands
  const auditForOperation = (id: string) => { focusAudit.current = true; history.auditForOperation(id); history.closeInspection(); setActiveTab('audit') }
  const handleRequestCheck = async (retry = false) => {
    setCheckError(null)
    notify.loading('Submitting reconciliation request...', { id: 'recon-check' })
    try {
      if (retry) await reconciliation.retryPending()
      else await reconciliation.check({ lookback_seconds: 3600, market: 'all', max_orders: 100, max_pages: 5, pair: null })
      notify.success('Reconciliation request accepted', {
        id: 'recon-check',
        description: 'Track the receipt to verify progress; accepted does not yet mean the data has matched.',
      })
    } catch (error) {
      const msg = error instanceof Error ? error.message : 'Reconciliation result unknown.'
      setCheckError(msg)
      notify.dismiss('recon-check')
    }
  }
  const events = eventsQuery.data?.pages.flatMap((page) => page.items) ?? []
  const commands = commandsQuery.data?.pages.flatMap((page) => page.items) ?? []

  return (
    <MotionPage>
      <div className="mx-auto w-full max-w-[1536px] px-4 pb-16 pt-4 sm:px-6 sm:pt-6 md:px-8 lg:px-10">
        {/* Header */}
        <div className="mb-6 flex flex-wrap items-center justify-between gap-3">
          <div>
            <SectionLabel tone="brand">Logs & Reconciliation</SectionLabel>
            <h1 className="mt-1 font-heavy text-2xl font-bold tracking-tight text-app-text md:text-3xl flex items-center gap-2.5">
              <Activity size={26} className="text-app-action" />
              <span>Session Logs & Exchange Reconciliation (Activity & Diagnosis)</span>
            </h1>
            <p className="mt-2 text-sm text-app-muted">
              Track session events, reconcile order fills with the OKX exchange, and look up control command history.
            </p>
          </div>
          {currentSessionId && (
            <div className="flex items-center gap-1.5 text-xs text-app-muted rounded-card border border-app-border bg-app-surface px-3 py-1.5">
              <span>Current session:</span>
              <CopyableId id={currentSessionId} truncateLength={10} copyLabel="Copy session ID" />
            </div>
          )}
        </div>

        {/* Tab Navigation */}
        <div className="mb-6 border-b border-app-border">
          <nav className="flex space-x-2" aria-label="Activity logs tabs">
            <button
              type="button"
              onClick={() => setActiveTab('reconciliation')}
              className={`inline-flex items-center gap-2 border-b-2 px-4 py-2.5 text-xs font-bold transition-colors ${
                activeTab === 'reconciliation'
                  ? 'border-app-action text-app-action'
                  : 'border-transparent text-app-muted hover:border-app-border hover:text-app-text'
              }`}
            >
              <GitCompare size={14} />
              <span>OKX Reconciliation</span>
            </button>

            <button
              type="button"
              onClick={() => setActiveTab('events')}
              className={`inline-flex items-center gap-2 border-b-2 px-4 py-2.5 text-xs font-bold transition-colors ${
                activeTab === 'events'
                  ? 'border-app-action text-app-action'
                  : 'border-transparent text-app-muted hover:border-app-border hover:text-app-text'
              }`}
            >
              <Clock size={14} />
              <span>Session Events</span>
            </button>

            <button
              type="button"
              onClick={() => setActiveTab('commands')}
              className={`inline-flex items-center gap-2 border-b-2 px-4 py-2.5 text-xs font-bold transition-colors ${
                activeTab === 'commands'
                  ? 'border-app-action text-app-action'
                  : 'border-transparent text-app-muted hover:border-app-border hover:text-app-text'
              }`}
            >
              <Terminal size={14} />
              <span>Commands</span>
            </button>
            {history.audit.access.allowed && <button type="button" ref={auditTabRef} onClick={() => setActiveTab('audit')} className={`inline-flex items-center gap-2 border-b-2 px-4 py-2.5 text-xs font-bold ${activeTab === 'audit' ? 'border-app-action text-app-action' : 'border-transparent text-app-muted'}`}>Audit / Actor Lookup</button>}
          </nav>
        </div>

        {(checkError || reconciliation.latest.error) && <p role="alert" className="mb-3 text-sm text-app-danger">{checkError ?? reconciliation.latest.error?.message}</p>}
        {reconciliation.pendingRetry && <Button variant="secondary" onClick={() => { void handleRequestCheck(true) }}>Retry check with same body/key</Button>}
        {reconciliation.feedback?.requestId && <p className="text-xs text-app-muted">Request ID: {reconciliation.feedback.requestId}</p>}
        {activeTab === 'events' && eventsQuery.error && <p role="alert">{eventsQuery.error.message}</p>}
        {activeTab === 'commands' && commandsQuery.error && <p role="alert">{commandsQuery.error.message}</p>}
        {/* Tab content */}
        {activeTab === 'reconciliation' && (
          <ReconciliationPanel
            reconciliation={reconciliation.latest.data}
            receipt={exactCheck.data}
            isChecking={reconciliation.submitting || reconciliation.receipt?.status === 'accepted' || reconciliation.receipt?.status === 'applying'}
            canRequest={reconciliation.access.allowed && !reconciliation.pendingRetry}
            onRequestCheck={() => { void handleRequestCheck() }}
          />
        )}

        {activeTab === 'events' && (
          <SessionEventsTimeline
            events={events}
            isLoading={eventsQuery.isLoading}
            filters={history.filters.events}
            onFilterChange={history.changeEvents}
            validationErrors={history.validation.events.errors}
            sessions={history.sessions.data?.pages.flatMap((page) => page.items)}
            selectedSessionId={history.sessionId}
            onSelectSession={history.selectSession}
            hasMore={eventsQuery.hasNextPage}
            onLoadMore={() => { void history.loadMoreEvents() }}
          />
        )}

        {activeTab === 'commands' && (
          <CommandHistoryList
            commands={commands}
            isLoading={commandsQuery.isLoading}
            filters={history.filters.commands}
            onFilterChange={history.changeCommands}
            validationErrors={history.validation.commands.errors}
            commandLinks={history.commandLinks}
            onInspectCommand={history.inspectCommand}
            onAuditForOperation={auditForOperation}
            selectedReceipt={history.selectedReceipt}
            inspection={history.inspection ?? undefined}
            onCloseInspection={history.closeInspection}
            hasMore={commandsQuery.hasNextPage}
            onLoadMore={() => { void history.loadMoreCommands() }}
          />
        )}
        {activeTab === 'events' && <>
          {history.sessions.hasNextPage && <Button variant="secondary" disabled={history.sessions.isFetchingNextPage} onClick={() => { void history.sessions.fetchNextPage() }}>Load more sessions</Button>}
          <PinnedSessionDetailView sessionId={history.sessionId} pinnedConfig={history.pinned.data} isLoading={history.pinned.isLoading} error={history.pinned.error?.message} />
        </>}
        {activeTab === 'audit' && <SecurityAuditView entries={history.audit.data?.pages.flatMap((page) => page.items)} filters={history.filters.audit} onFilterChange={history.changeAudit} validationErrors={history.validation.audit.errors} isLoading={history.audit.isLoading} error={history.audit.error?.message} hasMore={history.audit.hasNextPage} onLoadMore={() => { void history.loadMoreAudit() }} onInspectOperation={history.auditForOperation} />}

      </div>
    </MotionPage>
  )
}
