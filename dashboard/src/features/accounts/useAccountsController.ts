import { deriveFormFeedback } from '../../core/api/operation-feedback'
import { useAuth } from '../../core/auth/auth-context'
import { useAdminInfiniteQuery } from '../../core/api/infinite'
import { useAdminQuery } from '../../core/api/hooks'
import { useAccountsOperations } from './useAccountsOperations'

export function useAccountsController(options: { active?: boolean } = {}) {
  const { identity } = useAuth()
  const canManage = identity?.permissions.manage_accounts === true
  const active = options.active !== false
  const accounts = useAdminInfiniteQuery('listAdminAccounts', {}, { enabled: canManage && active })
  const bootstrap = useAdminQuery('getAdminBootstrap', {}, { enabled: canManage && active, poll: false })
  const operations = useAccountsOperations()

  return {
    accounts, bootstrap,
    access: { allowed: canManage, reasonCode: canManage ? null : 'permission_required' as const,
      detail: canManage ? null : 'This action requires the manage_accounts permission.' },
    invite: (body: Parameters<typeof operations.invite>[0]) => operations.invite(body),
    change: (accountId: string, body: Parameters<typeof operations.change>[1]) => operations.change(accountId, body),
    feedback: { invite: deriveFormFeedback(operations.inviteError, 'account'), change: deriveFormFeedback(operations.changeError, 'account') },
    inviteState: operations.inviteState,
    changeState: operations.changeState,
    /** A 409/last-admin response is surfaced unchanged so callers can refresh and review. */
  }
}
