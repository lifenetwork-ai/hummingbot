import { useEffect, useRef, useState } from 'react'
import { useNavigate, useSearch } from '@tanstack/react-router'
import { Activity, Clock, GitCompare, Terminal } from 'lucide-react'
import { HistoryUnit } from '../features/history/HistoryUnit'
import { AuditUnit } from '../features/audit/AuditUnit'
import { useAuditState } from '../features/audit/useAuditState'
import { ReconciliationUnit } from '../features/reconciliation/ReconciliationUnit'
import { useOperationalContext } from '../features/operational-context/useOperationalContext'
import { CopyableId } from '../shared/components/CopyableId'
import { SectionLabel } from '../shared/components/SectionLabel'
import { MotionPage } from '../shared/motion/MotionPage'

export type ActivityTab = 'reconciliation' | 'events' | 'commands' | 'audit'

export function ActivityPage() {
  const { status, identity } = useOperationalContext()
  const search = useSearch({ strict: false })
  const navigate = useNavigate()
  const initialTab: ActivityTab = search.tab === 'events' || search.tab === 'commands' || search.tab === 'audit' ? search.tab : 'reconciliation'
  const [activeTab, setActiveTab] = useState<ActivityTab>(initialTab)
  useEffect(() => { setActiveTab(initialTab) }, [initialTab])
  const selectTab = (tab: ActivityTab) => {
    setActiveTab(tab)
    void navigate({ to: '/activity', search: { tab } })
  }

  const currentSessionId = status.data?.observed_session?.id || status.data?.session?.id || ''

  const auditTabRef = useRef<HTMLButtonElement>(null)
  const focusAudit = useRef(false)
  useEffect(() => { if (activeTab === 'audit' && focusAudit.current) { auditTabRef.current?.focus(); focusAudit.current = false } }, [activeTab])
  const auditState = useAuditState()
  const historyMode = activeTab === 'events' || activeTab === 'commands' ? activeTab : 'none'
  const auditForOperation = (id: string) => { focusAudit.current = true; auditState.requestLookup(id); selectTab('audit') }

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
              Track session events, reconcile order fills with the exchange, and look up control command history.
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
              onClick={() => selectTab('reconciliation')}
              className={`inline-flex items-center gap-2 border-b-2 px-4 py-2.5 text-xs font-bold transition-colors ${
                activeTab === 'reconciliation'
                  ? 'border-app-action text-app-action'
                  : 'border-transparent text-app-muted hover:border-app-border hover:text-app-text'
              }`}
            >
              <GitCompare size={14} />
              <span>Exchange Reconciliation</span>
            </button>

            <button
              type="button"
              onClick={() => selectTab('events')}
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
              onClick={() => selectTab('commands')}
              className={`inline-flex items-center gap-2 border-b-2 px-4 py-2.5 text-xs font-bold transition-colors ${
                activeTab === 'commands'
                  ? 'border-app-action text-app-action'
                  : 'border-transparent text-app-muted hover:border-app-border hover:text-app-text'
              }`}
            >
              <Terminal size={14} />
              <span>Commands</span>
            </button>
            {identity?.permissions.read === true && <button type="button" ref={auditTabRef} onClick={() => selectTab('audit')} className={`inline-flex items-center gap-2 border-b-2 px-4 py-2.5 text-xs font-bold ${activeTab === 'audit' ? 'border-app-action text-app-action' : 'border-transparent text-app-muted'}`}>Audit / Actor Lookup</button>}
          </nav>
        </div>

        <HistoryUnit active={historyMode} currentSessionId={currentSessionId || undefined} onAuditForOperation={auditForOperation} />
        {/* Tab content */}
        <ReconciliationUnit active={activeTab === 'reconciliation'} />

        <AuditUnit active={activeTab === 'audit'} state={auditState} />

      </div>
    </MotionPage>
  )
}
