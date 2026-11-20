import { createContext } from 'react'
import type { useReconciliation } from './useReconciliation'

export type ReconciliationOperation = ReturnType<typeof useReconciliation>
export type OperationContextValue = { operation: ReconciliationOperation; acquireReadLease: () => () => void }
export const OperationContext = createContext<OperationContextValue | null>(null)
