import { useContext, useEffect } from 'react'
import { OperationContext } from './operation-context'

export function useReconciliationOperation() {
  const value = useContext(OperationContext)
  if (!value) throw new Error('ReconciliationOperationProvider is required')
  return value.operation
}

export function useReconciliationReadLease(active: boolean) {
  const value = useContext(OperationContext)
  if (!value) throw new Error('ReconciliationOperationProvider is required')
  const acquire = value.acquireReadLease
  useEffect(() => active ? acquire() : undefined, [active, acquire])
}
