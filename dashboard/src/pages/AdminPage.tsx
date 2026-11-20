import { useEffect, useState, type ReactElement } from 'react'
import { useNavigate, useSearch } from '@tanstack/react-router'
import { useOperationalContext } from '../features/operational-context/useOperationalContext'
import { FileText, KeyRound, ShieldAlert, Users, Wrench } from 'lucide-react'
import { ConnectionsUnit } from '../features/connections/ConnectionsUnit'
import { RecoveryUnit } from '../features/recovery/RecoveryUnit'
import { AccountsUnit } from '../features/accounts/AccountsUnit'
import { AuditUnit } from '../features/audit/AuditUnit'
import { useAuditState } from '../features/audit/useAuditState'
import { SectionLabel } from '../shared/components/SectionLabel'
import { MotionPage } from '../shared/motion/MotionPage'

export type AdminTab = 'connections' | 'recovery' | 'accounts' | 'audit'

export function AdminPage(): ReactElement {
  const search = useSearch({ strict: false })
  const navigate = useNavigate()
  const initialTab: AdminTab = search.tab === 'recovery' || search.tab === 'accounts' || search.tab === 'audit' ? search.tab : 'connections'
  const [activeTab, setActiveTab] = useState<AdminTab>(initialTab)
  useEffect(() => { setActiveTab(initialTab) }, [initialTab])
  const { identity } = useOperationalContext()
  const auditState = useAuditState()
  const canManageAccounts = identity?.permissions.manage_accounts === true
  const canReadAudit = identity?.permissions.read === true

  const tabs: { id: AdminTab; label: string; icon: typeof KeyRound; visible: boolean }[] = [
    { id: 'connections', label: 'Exchange Connections', icon: KeyRound, visible: true },
    { id: 'recovery', label: 'Incident Recovery', icon: ShieldAlert, visible: identity?.permissions.read === true },
    { id: 'accounts', label: 'Google Accounts', icon: Users, visible: canManageAccounts },
    { id: 'audit', label: 'Security Audit', icon: FileText, visible: canReadAudit },
  ]

  return (
    <MotionPage>
      <div className="mx-auto w-full max-w-[1536px] px-4 pb-16 pt-4 sm:px-6 sm:pt-6 md:px-8 lg:px-10">
        <div className="mb-6">
          <SectionLabel tone="brand">System & Security</SectionLabel>
          <h1 className="mt-1 font-heavy text-2xl font-bold tracking-tight text-app-text md:text-3xl flex items-center gap-2.5">
            <Wrench size={26} className="text-app-action" />
            <span>Administration</span>
          </h1>
          <p className="mt-2 text-sm text-app-muted">
            Manage exchange connections, recovery checks, accounts, and audit logs according to your granted permissions.
          </p>
        </div>

        <div className="mb-6 border-b border-app-border">
          <nav className="flex space-x-2 overflow-x-auto pb-1" aria-label="Admin tabs">
            {tabs.filter((tab) => tab.visible).map((tab) => {
              const Icon = tab.icon
              const selected = activeTab === tab.id
              return (
                <button key={tab.id} type="button" onClick={() => { setActiveTab(tab.id); void navigate({ to: '/admin', search: { tab: tab.id } }) }}
                  aria-current={selected ? 'page' : undefined}
                  className={`inline-flex items-center gap-2 border-b-2 px-4 py-2.5 text-xs font-bold transition-colors whitespace-nowrap ${selected ? 'border-app-action text-app-action' : 'border-transparent text-app-muted hover:border-app-border hover:text-app-text'}`}>
                  <Icon size={15} /><span>{tab.label}</span>
                </button>
              )
            })}
          </nav>
        </div>

        {activeTab === 'connections' && <ConnectionsUnit />}
        {activeTab === 'recovery' && <RecoveryUnit />}
        {activeTab === 'accounts' && <AccountsUnit />}
        <AuditUnit active={activeTab === 'audit'} state={auditState} />
      </div>
    </MotionPage>
  )
}
