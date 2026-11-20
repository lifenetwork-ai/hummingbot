import type { PropsWithChildren } from 'react'
import { AccountsOperationProvider } from '../features/accounts/AccountsOperationProvider'
import { ConnectionsOperationProvider } from '../features/connections/ConnectionsOperationProvider'
import { RecoveryOperationProvider } from '../features/recovery/RecoveryOperationProvider'
import { useRecoveryOperationLock } from '../features/recovery/RecoveryOperationContext'
import { ReconciliationOperationProvider } from '../features/reconciliation/ReconciliationOperationProvider'

/** Feature operations outlive their views; each owner gates its own presentation reads. */
export function FeatureOperationsBoundary({ children }: PropsWithChildren) {
  return (
    <RecoveryOperationProvider>
      <ConnectionsWithRecoveryFence>
        <AccountsOperationProvider>
          <ReconciliationOperationProvider>{children}</ReconciliationOperationProvider>
        </AccountsOperationProvider>
      </ConnectionsWithRecoveryFence>
    </RecoveryOperationProvider>
  )
}

function ConnectionsWithRecoveryFence({ children }: PropsWithChildren) {
  const recoveryPending = useRecoveryOperationLock()
  return <ConnectionsOperationProvider recoveryPending={recoveryPending}>{children}</ConnectionsOperationProvider>
}
