import { ChevronRight, Plus, Shield, Users } from 'lucide-react'
import { useState } from 'react'
import type { components } from '../../core/api/schema'
import type { FormFeedbackModel } from '../../core/api/operation-feedback'
import { Badge } from '../../shared/components/Badge'
import { Button } from '../../shared/components/Button'
import { CopyableId } from '../../shared/components/CopyableId'
import { Input } from '../../shared/components/Input'
import { Select } from '../../shared/components/Select'
import { Surface } from '../../shared/components/Surface'
import { FeedbackDetailsView } from '../session/FeedbackDetailsView'
import { RoleChangeConfirmationDialog } from './RoleChangeConfirmationDialog'
import { AccountLockConfirmationDialog } from './AccountLockConfirmationDialog'

export interface AccountsManagementViewProps {
  accounts?: components['schemas']['AccountResponseOutput'][]
  accountsData?: components['schemas']['AccountsPageOutput'] | null
  bootstrap?: components['schemas']['BootstrapResponseOutput'] | null
  isLoading?: boolean
  isSubmitting?: boolean
  hasMore?: boolean
  onLoadMore?: () => void
  onAddAccount?: (email: string, role: 'admin' | 'write' | 'read') => Promise<unknown> | void
  onChangeAccount?: (accountId: string, body: components['schemas']['ChangeAccountRequest']) => Promise<unknown> | void
  feedbackModel?: FormFeedbackModel | null
  changeFeedback?: { accountId: string; model: FormFeedbackModel } | null
}

export function AccountsManagementView({
  accounts,
  accountsData,
  bootstrap,
  isLoading,
  isSubmitting = false,
  hasMore,
  onLoadMore,
  onAddAccount,
  onChangeAccount,
  feedbackModel,
  changeFeedback,
}: AccountsManagementViewProps) {
  const [showAddForm, setShowAddForm] = useState(false)
  const [email, setEmail] = useState('')
  const [role, setRole] = useState<'admin' | 'write' | 'read'>('read')
  const [error, setError] = useState<string | null>(null)
  const [pendingRoleChange, setPendingRoleChange] = useState<{
    account: components['schemas']['AccountResponseOutput']
    targetRole: 'admin' | 'write' | 'read'
  } | null>(null)
  const [dialogError, setDialogError] = useState<string | null>(null)
  const [pendingStatusChange, setPendingStatusChange] = useState<{
    account: components['schemas']['AccountResponseOutput']
    targetActive: boolean
  } | null>(null)
  const [statusDialogError, setStatusDialogError] = useState<string | null>(null)

  const items = accounts ?? accountsData?.items ?? []
  const adminCount = items.filter((a) => a.role === 'admin' && a.active && a.bound).length

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!email.trim() || !email.includes('@')) {
      setError('Please enter a valid Google email address')
      return
    }
    setError(null)
    try {
      if (onAddAccount) {
        await onAddAccount(email.trim(), role)
        setEmail('')
        setShowAddForm(false)
      }
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Error adding account')
    }
  }

  const changeAccount = async (account: (typeof items)[number], change: { role: 'admin' | 'write' | 'read' | null; active: boolean | null }) => {
    setError(null)
    try {
      await onChangeAccount?.(account.id, { ...change, expected_version: account.version })
    } catch (failure) {
      const msg = failure instanceof Error ? failure.message : 'Failed to update account; refresh and review.'
      setError(msg)
      throw failure
    }
  }

  const handleRequestRoleChange = (
    account: (typeof items)[number],
    targetRole: 'admin' | 'write' | 'read'
  ) => {
    if (targetRole === account.role) return
    setDialogError(null)
    setPendingRoleChange({ account, targetRole })
  }

  const handleConfirmRoleChange = async () => {
    if (!pendingRoleChange) return
    const { account, targetRole } = pendingRoleChange
    setDialogError(null)
    try {
      await changeAccount(account, { role: targetRole, active: null })
      setPendingRoleChange(null)
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Failed to update role.'
      setDialogError(msg)
    }
  }

  const handleRequestStatusChange = (
    account: (typeof items)[number],
    targetActive: boolean
  ) => {
    setStatusDialogError(null)
    setPendingStatusChange({ account, targetActive })
  }

  const handleConfirmStatusChange = async () => {
    if (!pendingStatusChange) return
    const { account, targetActive } = pendingStatusChange
    setStatusDialogError(null)
    try {
      await changeAccount(account, { role: null, active: targetActive })
      setPendingStatusChange(null)
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Failed to update account status.'
      setStatusDialogError(msg)
    }
  }
  return (
    <Surface className="p-5 md:p-6">
      <div className="flex flex-col justify-between gap-3 border-b border-app-border/80 pb-4 sm:flex-row sm:items-center">
        <div>
          <div className="flex items-center gap-2">
            <Users size={20} className="text-app-action" />
            <h3 className="font-heavy text-lg font-bold text-app-text">
              Google Accounts Management
            </h3>
          </div>
          <p className="mt-1 text-xs text-app-muted">
            Access control via Google OAuth. Only Administrators have permission to access and modify this list.
          </p>
        </div>

        <Button
          variant="primary"
          onClick={() => setShowAddForm((s) => !s)}
          className="h-8 px-3 text-xs gap-1.5 font-bold self-start sm:self-auto"
        >
          <Plus size={14} />
          <span>Add Google Account</span>
        </Button>
      </div>

      {/* Last bound Admin protection notice */}
      <div className="mt-4 rounded-card border border-app-border/80 bg-app-surface-muted/40 p-3 text-xs text-app-muted flex items-start gap-2.5">
        <Shield size={16} className="text-app-action mt-0.5 shrink-0" />
        <div>
          <strong className="text-app-text">Last Bound Admin Protection: </strong>
          The system currently has <span className="font-bold text-app-action">{bootstrap?.active_google_admins ?? adminCount} (bootstrap count or loaded page count)</span> active Administrator account(s). Deleting or demoting the last Administrator is blocked to prevent lockouts.
        </div>
      </div>

      {/* Add form */}
      {showAddForm && (
        <form onSubmit={handleSubmit} className="mt-5 rounded-card border border-app-action/30 bg-app-action/5 p-4 space-y-3">
          <div className="flex items-center justify-between border-b border-app-border/60 pb-2">
            <h4 className="text-xs font-bold uppercase tracking-wider text-app-text">
              Authorize New Google Account
            </h4>
            <button
              type="button"
              onClick={() => setShowAddForm(false)}
              className="text-xs text-app-muted hover:text-app-text"
            >
              Close
            </button>
          </div>

          {error && (
            <p className="text-xs text-app-danger">{error}</p>
          )}

          <div className="grid gap-3 sm:grid-cols-2">
            <div>
              <Input
                label="Google Email"
                type="email"
                placeholder="e.g. operator@genetica.asia"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                error={feedbackModel?.inlineErrors.email?.[0] || (error && error.includes('email') ? error : undefined)}
                required
              />
            </div>
            <div>
              <Select
                id="account-role-select"
                label="Role"
                sizeVariant="md"
                value={role}
                onChange={(e) => setRole(e.target.value as 'admin' | 'write' | 'read')}
                error={feedbackModel?.inlineErrors.role?.[0]}
                options={[
                  { value: 'read', label: 'Viewer (Read)' },
                  { value: 'write', label: 'Operator (Write)' },
                  { value: 'admin', label: 'Administrator (Admin)' },
                ]}
              />
            </div>
          </div>

          {(feedbackModel?.feedback || (feedbackModel?.globalFields && feedbackModel.globalFields.length > 0)) && (
            <FeedbackDetailsView
              feedback={feedbackModel.feedback}
              globalFields={feedbackModel.globalFields}
            />
          )}

          <div className="flex justify-end gap-2 pt-2">
            <Button variant="secondary" type="button" onClick={() => setShowAddForm(false)} className="h-8 px-3 text-xs">
              Cancel
            </Button>
            <Button variant="primary" type="submit" disabled={isSubmitting} className="h-8 px-3 text-xs font-bold">
              {isSubmitting ? 'Adding...' : 'Confirm Authorization'}
            </Button>
          </div>
        </form>
      )}

      {error && !Object.keys(feedbackModel?.inlineErrors ?? changeFeedback?.model.inlineErrors ?? {}).length && <p role="alert" className="mt-3 text-xs text-app-danger">{error}</p>}
      {/* Accounts Table */}
      <div className="mt-5 overflow-hidden rounded-card border border-app-border">
        <table className="w-full text-left text-xs">
          <thead>
            <tr className="border-b border-app-border bg-app-surface-muted/60 text-app-muted font-semibold">
              <th className="p-3">Email Account</th>
              <th className="p-3">Role</th>
              <th className="p-3">Status</th>
              <th className="p-3">Suspend</th>
              <th className="p-3">Created At</th>
              <th className="p-3">Updated At</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-app-border/60">
            {isLoading ? (
              <tr>
                <td colSpan={6} className="p-6 text-center text-app-muted">
                  Loading accounts list...
                </td>
              </tr>
            ) : items.length === 0 ? (
              <tr>
                <td colSpan={6} className="p-6 text-center text-app-muted">
                  No accounts configured in this list.
                </td>
              </tr>
            ) : (
              items.map((acc) => (
                <tr key={acc.id}>
                  <td className="p-3 font-semibold text-app-text">
                    {acc.email ? (
                      acc.email
                    ) : acc.id ? (
                      <CopyableId id={acc.id} truncateLength={10} copyLabel="Copy account ID" />
                    ) : (
                      '—'
                    )}
                  </td>
                  <td className="p-3">
                    <div className="w-32">
                      <Select
                        aria-label={`Role for ${acc.email ?? acc.id ?? ''}`}
                        sizeVariant="sm"
                        value={acc.role}
                        disabled={!onChangeAccount || isSubmitting}
                        options={[
                          { value: 'read', label: 'Viewer' },
                          { value: 'write', label: 'Operator' },
                          { value: 'admin', label: 'Admin' },
                        ]}
                        onChange={(event) => {
                          const val = event.target.value
                          if (val === 'read' || val === 'write' || val === 'admin') {
                            handleRequestRoleChange(acc, val)
                          }
                        }}
                        aria-invalid={
                          changeFeedback?.accountId === acc.id &&
                          Boolean(changeFeedback.model.inlineErrors.role)
                        }
                        aria-describedby={
                          changeFeedback?.accountId === acc.id &&
                          changeFeedback.model.inlineErrors.role
                            ? `role-error-${acc.id}`
                            : undefined
                        }
                      />
                    </div>
                    {changeFeedback?.accountId === acc.id &&
                      changeFeedback.model.inlineErrors.role && (
                        <p
                          id={`role-error-${acc.id}`}
                          role="alert"
                          className="mt-1 text-app-danger"
                        >
                          {changeFeedback.model.inlineErrors.role.join(' · ')}
                        </p>
                      )}
                  </td>
                  <td className="p-3">
                    <Badge tone={acc.active ? 'success' : 'danger'} className="text-[10px]">
                      {acc.active ? 'Active' : 'Suspended'} · {acc.bound ? 'Bound to Google' : 'Invitation'}
                    </Badge>
                  </td>
                  <td className="p-3">
                    {onChangeAccount ? (
                      <Button
                        variant={acc.active ? 'secondary' : 'primary'}
                        className="h-7 px-2.5 text-xs font-medium"
                        disabled={isSubmitting}
                        onClick={() => handleRequestStatusChange(acc, !acc.active)}
                      >
                        {acc.active ? 'Suspend' : 'Reactivate'}
                      </Button>
                    ) : (
                      <span className="text-app-muted">—</span>
                    )}
                    {changeFeedback?.accountId === acc.id &&
                      changeFeedback.model.inlineErrors.active && (
                        <p role="alert" className="mt-1 text-xs text-app-danger">
                          {changeFeedback.model.inlineErrors.active.join(' · ')}
                        </p>
                      )}
                  </td>
                  <td className="p-3 font-mono text-app-muted">
                    {acc.created_at ? new Date(acc.created_at).toLocaleDateString('en-US') : '—'}
                  </td>
                  <td className="p-3 font-mono text-app-muted">
                    {acc.updated_at ? new Date(acc.updated_at).toLocaleDateString('en-US') : '—'}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {hasMore && onLoadMore && (
        <div className="mt-4 flex justify-center">
          <Button variant="secondary" onClick={onLoadMore} disabled={isLoading} className="h-8 px-3 text-xs gap-1">
            <span>{isLoading ? 'Loading...' : 'Load more accounts'}</span>
            <ChevronRight size={14} />
          </Button>
        </div>
      )}

      <RoleChangeConfirmationDialog
        open={Boolean(pendingRoleChange)}
        onOpenChange={(open) => {
          if (!open) {
            setPendingRoleChange(null)
            setDialogError(null)
          }
        }}
        account={pendingRoleChange?.account ?? null}
        targetRole={pendingRoleChange?.targetRole ?? null}
        onConfirm={handleConfirmRoleChange}
        isSubmitting={isSubmitting}
        error={dialogError}
      />

      <AccountLockConfirmationDialog
        open={Boolean(pendingStatusChange)}
        onOpenChange={(open) => {
          if (!open) {
            setPendingStatusChange(null)
            setStatusDialogError(null)
          }
        }}
        account={pendingStatusChange?.account ?? null}
        targetActive={pendingStatusChange?.targetActive ?? null}
        onConfirm={handleConfirmStatusChange}
        isSubmitting={isSubmitting}
        error={statusDialogError}
      />
    </Surface>
  )
}
