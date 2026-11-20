import type { PropsWithChildren } from 'react'
import { useAuth } from '../../core/auth/auth-context'
import { AccountsOperationsContext, useAccountsOperationsController } from './useAccountsOperations'

/** Keeps only in-flight account mutations mounted across route and tab changes. */
export function AccountsOperationProvider({ children }: PropsWithChildren) {
  const { apiSession, identity } = useAuth()
  const scope = JSON.stringify([
    apiSession?.baseUrl ?? null,
    apiSession?.principalId ?? null,
    identity?.id ?? null,
    identity?.permissions.manage_accounts === true,
  ])
  const operations = useAccountsOperationsController(scope)
  return <AccountsOperationsContext.Provider value={operations}>{children}</AccountsOperationsContext.Provider>
}
