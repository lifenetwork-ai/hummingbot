import { deriveFormFeedback } from '../../core/api/operation-feedback'
import { useAuth } from '../../core/auth/auth-context'
import { useAdminInfiniteQuery } from '../../core/api/infinite'
import { useAdminMutation, useAdminQuery } from '../../core/api/hooks'

const accountRoles = ['admin', 'write', 'read'] as const
const isAccountRole = (role: string): role is typeof accountRoles[number] => (accountRoles as readonly string[]).includes(role)
function validateInvitation(body: { email: string; role: string; active: boolean }) {
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(body.email.trim())) throw new Error('Enter a valid account email.')
  if (!isAccountRole(body.role)) throw new Error('Choose a valid account role.')
}
function validateChange(body: { active: boolean | null; role: string | null; expected_version: number }) {
  if (!Number.isSafeInteger(body.expected_version) || body.expected_version < 0) throw new Error('Account version must be a non-negative integer.')
  if (body.role !== null && !isAccountRole(body.role)) throw new Error('Choose a valid account role.')
}

export function useAccountsController() {
  const { identity } = useAuth()
  const canManage = identity?.permissions.manage_accounts === true
  const accounts = useAdminInfiniteQuery('listAdminAccounts', {}, { enabled: canManage })
  const bootstrap = useAdminQuery('getAdminBootstrap', {}, { enabled: canManage, poll: false })
  const invite = useAdminMutation('addAdminAccount')
  const change = useAdminMutation('changeAdminAccount')

  return {
    accounts, bootstrap,
    access: { allowed: canManage, reasonCode: canManage ? null : 'permission_required' as const,
      detail: canManage ? null : 'This action requires the manage_accounts permission.' },
    invite: (body: Parameters<typeof invite.mutateAsync>[0]['body']) => {
      if (!canManage) throw new Error('This action requires the manage_accounts permission.')
      validateInvitation(body)
      return invite.mutateAsync({ body })
    },
    change: (accountId: string, body: Parameters<typeof change.mutateAsync>[0]['body']) => {
      if (!canManage) throw new Error('This action requires the manage_accounts permission.')
      validateChange(body)
      return change.mutateAsync({ path: { account_id: accountId }, body })
    },
    feedback: { invite: deriveFormFeedback(invite.error, 'account'), change: deriveFormFeedback(change.error, 'account') },
    inviteState: invite,
    changeState: change,
    /** A 409/last-admin response is surfaced unchanged so callers can refresh and review. */
  }
}
