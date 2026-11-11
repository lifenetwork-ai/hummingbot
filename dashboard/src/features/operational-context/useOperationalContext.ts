import { createContext, useContext } from 'react'
import type { useOperationalController } from './useOperationalController'

export type OperationalContextValue = ReturnType<typeof useOperationalController>
export const OperationalContext = createContext<OperationalContextValue | null>(null)

/** Workspace consumers share the provider's controller rather than creating queries. */
export function useOperationalContext(): OperationalContextValue {
  const context = useContext(OperationalContext)
  if (!context) throw new Error('useOperationalContext must be used within OperationalProvider')
  return context
}
