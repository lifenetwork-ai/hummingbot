import { createContext, useContext, useMemo, useRef } from 'react'
import { useAuth } from '../../core/auth/auth-context'
import { useAdminMutation } from '../../core/api/hooks'

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

export function useAccountsOperationsController(scope: string) {
  const { identity } = useAuth()
  const allowed = identity?.permissions.manage_accounts === true
  const allowedRef = useRef(allowed)
  const scopeRef = useRef({ scope, generation: 0 })
  const inviteResultScope = useRef<number>()
  const changeResultScope = useRef<number>()
  allowedRef.current = allowed
  if (scopeRef.current.scope !== scope) {
    scopeRef.current = { scope, generation: scopeRef.current.generation + 1 }
  }
  const inviteMutation = useAdminMutation('addAdminAccount')
  const changeMutation = useAdminMutation('changeAdminAccount')

  return useMemo(() => ({
    invite: (body: Parameters<typeof inviteMutation.mutateAsync>[0]['body']) => {
      if (!allowedRef.current) throw new Error('This action requires the manage_accounts permission.')
      validateInvitation(body)
      inviteResultScope.current = scopeRef.current.generation
      return inviteMutation.mutateAsync({ body })
    },
    change: (accountId: string, body: Parameters<typeof changeMutation.mutateAsync>[0]['body']) => {
      if (!allowedRef.current) throw new Error('This action requires the manage_accounts permission.')
      validateChange(body)
      changeResultScope.current = scopeRef.current.generation
      return changeMutation.mutateAsync({ path: { account_id: accountId }, body })
    },
    inviteError: inviteResultScope.current === scopeRef.current.generation ? inviteMutation.error : null,
    changeError: changeResultScope.current === scopeRef.current.generation ? changeMutation.error : null,
    inviteState: { ...inviteMutation, isPending: inviteMutation.isPending && inviteResultScope.current === scopeRef.current.generation },
    changeState: { ...changeMutation, isPending: changeMutation.isPending && changeResultScope.current === scopeRef.current.generation },
  }), [inviteMutation, changeMutation])
}

type AccountsOperations = ReturnType<typeof useAccountsOperationsController>
export const AccountsOperationsContext = createContext<AccountsOperations | null>(null)

export function useAccountsOperations() {
  const operations = useContext(AccountsOperationsContext)
  if (!operations) throw new Error('AccountsOperationProvider is required')
  return operations
}
