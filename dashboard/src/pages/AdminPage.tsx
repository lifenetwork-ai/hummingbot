import { useEffect, useState, type ReactElement } from 'react'
import { useNavigate, useSearch } from '@tanstack/react-router'
import { useOperationalContext } from '../features/operational-context/useOperationalContext'
import { FileText, KeyRound, ShieldAlert, Users, Wrench } from 'lucide-react'
import { notify } from '../core/feedback/notify'
import { useConnectionsController } from '../features/connections/useConnectionsController'
import { useRecovery } from '../features/recovery/useRecovery'
import { useAccountsController } from '../features/accounts/useAccountsController'
import { useHistoryController } from '../features/history/useHistoryController'
import { deriveFormFeedback, type FormFeedbackModel } from '../core/api/operation-feedback'
import { FeedbackDetailsView } from '../features/session/FeedbackDetailsView'
import { AccountsManagementView } from '../features/admin/AccountsManagementView'
import { AdminRecoveryView } from '../features/admin/AdminRecoveryView'
import { DisconnectConfirmationDialog } from '../features/admin/DisconnectConfirmationDialog'
import { ManagedCredentialsView } from '../features/admin/ManagedCredentialsView'
import { SecurityAuditView } from '../features/admin/SecurityAuditView'
import { SectionLabel } from '../shared/components/SectionLabel'
import { MotionPage } from '../shared/motion/MotionPage'

export type AdminTab = 'connections' | 'recovery' | 'accounts' | 'audit'
const newKey = () => crypto.randomUUID()
type CredentialSlot = ReturnType<typeof useConnectionsController>['slots'][number]

export function AdminPage(): ReactElement {
  const search = useSearch({ strict: false })
  const navigate = useNavigate()
  const initialTab: AdminTab = search.tab === 'recovery' || search.tab === 'accounts' || search.tab === 'audit' ? search.tab : 'connections'
  const [activeTab, setActiveTab] = useState<AdminTab>(initialTab)
  useEffect(() => { setActiveTab(initialTab) }, [initialTab])
  const [disconnectSlot, setDisconnectSlot] = useState<(Pick<CredentialSlot, 'exchange' | 'connector'> & { idempotencyKey: string }) | null>(null)
  const [disconnectError, setDisconnectError] = useState<string | null>(null)
  const [checkError, setCheckError] = useState<string | undefined>()
  const [resetError, setResetError] = useState<string | undefined>()
  const { identity } = useOperationalContext()
  const connections = useConnectionsController()
  const recovery = useRecovery()
  const accounts = useAccountsController()
  const history = useHistoryController({ active: activeTab === 'audit' ? 'audit' : 'none' })
  const audit = history.audit
  const [credentialFeedback, setCredentialFeedback] = useState<Record<string, FormFeedbackModel | null>>({})
  const [accountAction, setAccountAction] = useState<{ kind: 'invite' | 'change'; id?: string }>({ kind: 'invite' })

  const canManageCredentials = identity?.permissions.manage_credentials === true
  const canManageAccounts = accounts.access.allowed
  const canReadAudit = audit.access.allowed

  const tabs: { id: AdminTab; label: string; icon: typeof KeyRound; visible: boolean }[] = [
    { id: 'connections', label: 'Exchange Connections', icon: KeyRound, visible: true },
    { id: 'recovery', label: 'Incident Recovery', icon: ShieldAlert, visible: identity?.permissions.read === true },
    { id: 'accounts', label: 'Google Accounts', icon: Users, visible: canManageAccounts },
    { id: 'audit', label: 'Security Audit', icon: FileText, visible: canReadAudit },
  ]

  const runCredential = async (exchange: string, connector: string, submit: () => Promise<unknown>) => {
    const key = `${exchange}:${connector}`
    setCredentialFeedback((previous) => ({ ...previous, [key]: null }))
    try { return await submit() }
    catch (error) {
      const names = connections.slots.find((slot) => slot.exchange === exchange && slot.connector === connector)?.fields.map((field) => field.name) ?? []
      setCredentialFeedback((previous) => ({ ...previous, [key]: deriveFormFeedback(error, 'credentials', names) }))
      throw error
    }
  }
  const confirmDisconnect = async () => {
    if (!disconnectSlot) return
    const selected = disconnectSlot
    setDisconnectError(null)
    notify.loading('Disconnecting API Key...', { id: 'conn-disconnect' })
    try {
      await runCredential(selected.exchange, selected.connector, () => connections.disconnect(selected))
      setDisconnectSlot(null)
      notify.info('Disconnect receipt received — cleanup not yet verified', { id: 'conn-disconnect' })
    } catch (error) {
      const msg = error instanceof Error ? error.message : 'Unable to disconnect this credential slot.'
      setDisconnectError(msg)
      notify.dismiss('conn-disconnect')
    }
  }

  const checkRecovery = async () => {
    setCheckError(undefined)
    notify.loading('Checking system recovery...', { id: 'admin-recovery' })
    try {
      await recovery.check()
      notify.info('Check receipt received — awaiting check result', { id: 'admin-recovery' })
    }
    catch (error) {
      const msg = error instanceof Error ? error.message : 'Recovery check failed.'
      setCheckError(msg)
      notify.dismiss('admin-recovery')
    }
  }
  const retryRecoveryCheck = async () => {
    setCheckError(undefined)
    notify.loading('Retrying recovery check...', { id: 'admin-recovery-retry' })
    try {
      await recovery.retryCheck()
      notify.info('Retry check receipt received — continuing to monitor', { id: 'admin-recovery-retry' })
    }
    catch (error) {
      const msg = error instanceof Error ? error.message : 'Recovery check retry failed.'
      setCheckError(msg)
      notify.dismiss('admin-recovery-retry')
    }
  }
  const resetRecovery = async () => {
    setResetError(undefined)
    notify.loading('Resetting recovery state...', { id: 'admin-recovery-reset' })
    try {
      await recovery.confirmReset()
      notify.info('Reset receipt received — recovered not yet verified', { id: 'admin-recovery-reset' })
    }
    catch (error) {
      const msg = error instanceof Error ? error.message : 'Recovery reset failed.'
      setResetError(msg)
      notify.dismiss('admin-recovery-reset')
      throw error
    }
  }
  const retryRecoveryReset = async () => {
    setResetError(undefined)
    notify.loading('Retrying recovery state reset...', { id: 'admin-recovery-reset-retry' })
    try {
      await recovery.retryReset()
      notify.info('Retry Reset receipt received — continuing to monitor', { id: 'admin-recovery-reset-retry' })
    }
    catch (error) {
      const msg = error instanceof Error ? error.message : 'Recovery reset retry failed.'
      setResetError(msg)
      notify.dismiss('admin-recovery-reset-retry')
    }
  }

  const auditItems = audit.data?.pages.flatMap((page) => page.items) ?? []
  const accountItems = accounts.accounts.data?.pages.flatMap((page) => page.items) ?? []

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

        {activeTab === 'connections' && (
          <ManagedCredentialsView
            catalogue={connections.catalogue.data}
            feedbackBySlot={credentialFeedback}
            slots={connections.slots}
            locks={connections.decisionLocks}
            canManage={canManageCredentials}
            statusError={connections.catalogue.error?.message ?? connections.runtime.error?.message}
            isLoading={connections.catalogue.phase === 'loading' || (connections.catalogue.data?.exchanges.length ?? 0) > 0 && connections.slots.length === 0}
            isSubmitting={recovery.lockSessionAndConnections || Object.values(connections.mutations).some((mutation) => mutation.isPending)}
            onRefresh={connections.refresh}
            onSetCredentials={(exchange, connector, values, idempotencyKey) =>
              runCredential(exchange, connector, () => connections.set({ exchange, connector, values, idempotencyKey })).then(() => undefined)}
            onRetryCleanup={(exchange, connector, idempotencyKey) => runCredential(exchange, connector, () => connections.retryCleanup({ exchange, connector, idempotencyKey }))}
            onRepairCleanup={(exchange, connector, values, idempotencyKey) => runCredential(exchange, connector, () => connections.repairCleanupKey({ exchange, connector, values, idempotencyKey }))}
            onReviewUnknown={connections.reviewUnknown}
            onOpenDisconnect={(exchange, connector, retryKey) => setDisconnectSlot({ exchange, connector, idempotencyKey: retryKey ?? newKey() })}
          />
        )}

        {activeTab === 'recovery' && (
          <AdminRecoveryView
            recoverySummary={recovery.overview.data}
            hasAccess={recovery.access.allowed}
            comparison={recovery.comparison}
            status={recovery.status.data}
            activeOperation={recovery.operation}
            checkDecision={recovery.checkDecision}
            reviewDecision={recovery.reviewDecision}
            canRetryCheck={recovery.canRetryCheck}
            canRetryReset={recovery.canRetryReset}
            isChecking={recovery.operationPending && recovery.operation?.action === 'recovery_check'}
            isResetting={recovery.operationPending && recovery.operation?.action === 'recovery_reset'}
            checkError={checkError}
            resetError={resetError}
            onCheckRecovery={checkRecovery}
            onRetryCheck={retryRecoveryCheck}
            onResetRecovery={resetRecovery}
            onRetryReset={retryRecoveryReset}
            onRefresh={recovery.refresh}
          />
        )}

        {activeTab === 'recovery' && <>
          <FeedbackDetailsView title="Recovery check feedback" feedback={recovery.feedback.check.feedback} globalFields={recovery.feedback.check.globalFields} onRetry={recovery.canRetryCheck ? () => { void retryRecoveryCheck() } : undefined} onRefresh={() => { void recovery.refresh() }} onReview={() => { void recovery.refresh() }} />
          <FeedbackDetailsView title="Recovery reset feedback" feedback={recovery.feedback.reset.feedback} globalFields={recovery.feedback.reset.globalFields} onRetry={recovery.canRetryReset ? () => { void retryRecoveryReset() } : undefined} onRefresh={() => { void recovery.refresh() }} onReview={() => { void recovery.refresh() }} />
        </>}
        {activeTab === 'accounts' && (accounts.accounts.error || accounts.bootstrap.error) && <p role="alert" className="text-sm text-app-danger">{accounts.accounts.error?.message ?? accounts.bootstrap.error?.message}</p>}
        {activeTab === 'accounts' && canManageAccounts && (
          <AccountsManagementView
            accounts={accountItems}
            feedbackModel={accountAction.kind === 'invite' ? accounts.feedback.invite : null}
            changeFeedback={accountAction.kind === 'change' && accountAction.id ? { accountId: accountAction.id, model: accounts.feedback.change } : null}
            bootstrap={accounts.bootstrap.data}
            isLoading={accounts.accounts.isLoading || accounts.bootstrap.isLoading}
            isSubmitting={accounts.inviteState.isPending || accounts.changeState.isPending}
            hasMore={accounts.accounts.hasNextPage}
            onLoadMore={() => accounts.accounts.fetchNextPage()}
            onAddAccount={(email, role) => { setAccountAction({ kind: 'invite' }); return accounts.invite({ email, role, active: true }) }}
            onChangeAccount={(accountId, body) => { setAccountAction({ kind: 'change', id: accountId }); return accounts.change(accountId, body) }}
          />
        )}

        {activeTab === 'accounts' && accountAction.kind === 'change' && <FeedbackDetailsView feedback={accounts.feedback.change.feedback} globalFields={accounts.feedback.change.globalFields} onRefresh={() => { void accounts.accounts.refetch() }} onReview={() => { void accounts.accounts.refetch() }} />}
        {activeTab === 'audit' && canReadAudit && (
          <SecurityAuditView
            entries={auditItems}
            filters={history.filters.audit}
            onFilterChange={history.changeAudit}
            validationErrors={history.validation.audit.errors}
            onInspectOperation={history.auditForOperation}
            isLoading={audit.isLoading}
            error={audit.error?.message}
            hasMore={audit.hasNextPage}
            onLoadMore={() => { void history.loadMoreAudit() }}
            accounts={accountItems}
            exchanges={connections.catalogue.data?.exchanges.map((item) => item.exchange)}
            connectors={connections.catalogue.data?.exchanges.flatMap((item) => item.connectors)}
          />
        )}

        {activeTab === 'accounts' && !canManageAccounts && (
          <p role="status" className="text-sm text-app-muted">Account administration requires manage_accounts permission.</p>
        )}
        {activeTab === 'audit' && !canReadAudit && (
          <p role="status" className="text-sm text-app-muted">Audit access requires read permission.</p>
        )}

        <DisconnectConfirmationDialog
          open={Boolean(disconnectSlot)}
          onOpenChange={(open) => { if (!open) { setDisconnectSlot(null); setDisconnectError(null) } }}
          connector={disconnectSlot?.connector}
          isSubmitting={connections.mutations.disconnect.isPending}
          error={disconnectError}
          onConfirmDisconnect={confirmDisconnect}
        />
      </div>
    </MotionPage>
  )
}
