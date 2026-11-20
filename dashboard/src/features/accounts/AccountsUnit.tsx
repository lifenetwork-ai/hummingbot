import { useState } from 'react'
import type { components } from '../../core/api/schema'
import { FeedbackDetailsView } from '../session/FeedbackDetailsView'
import { AccountsManagementView } from '../admin/AccountsManagementView'
import { useAccountsController } from './useAccountsController'

export function AccountsUnit({ active = true }: { active?: boolean }) {
  const accounts = useAccountsController({ active })
  const [accountAction, setAccountAction] = useState<{ kind: 'invite' | 'change'; id?: string }>({ kind: 'invite' })

  if (!accounts.access.allowed) {
    return <p role="status" className="text-sm text-app-muted">Account administration requires manage_accounts permission.</p>
  }

  return <>
    {(accounts.accounts.error || accounts.bootstrap.error) && (
      <p role="alert" className="text-sm text-app-danger">{accounts.accounts.error?.message ?? accounts.bootstrap.error?.message}</p>
    )}
    <AccountsManagementView
      accounts={accounts.accounts.data?.pages.flatMap((page) => page.items)}
      feedbackModel={accountAction.kind === 'invite' ? accounts.feedback.invite : null}
      changeFeedback={accountAction.kind === 'change' && accountAction.id && accounts.feedback.change
        ? { accountId: accountAction.id, model: accounts.feedback.change } : null}
      bootstrap={accounts.bootstrap.data}
      isLoading={accounts.accounts.isLoading || accounts.bootstrap.isLoading}
      isSubmitting={accounts.inviteState.isPending || accounts.changeState.isPending}
      hasMore={accounts.accounts.hasNextPage}
      onLoadMore={() => { void accounts.accounts.fetchNextPage() }}
      onAddAccount={(email, role) => {
        setAccountAction({ kind: 'invite' })
        return accounts.invite({ email, role, active: true })
      }}
      onChangeAccount={(accountId, body: components['schemas']['ChangeAccountRequest']) => {
        setAccountAction({ kind: 'change', id: accountId })
        return accounts.change(accountId, body)
      }}
    />
    {accountAction.kind === 'change' && accounts.feedback.change && (
      <FeedbackDetailsView
        feedback={accounts.feedback.change.feedback}
        globalFields={accounts.feedback.change.globalFields}
        onRefresh={() => { void accounts.accounts.refetch() }}
        onReview={() => { void accounts.accounts.refetch() }}
      />
    )}
  </>
}
