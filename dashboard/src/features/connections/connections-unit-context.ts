import { createContext, useContext, useEffect } from 'react'
import type { useConnectionsController } from './useConnectionsController'

export type ConnectionsController = ReturnType<typeof useConnectionsController>
export type ConnectionsUnitContextValue = { controller: ConnectionsController; setActive: (active: boolean) => void }
export const ConnectionsUnitContext = createContext<ConnectionsUnitContextValue | null>(null)

export function useConnectionsUnitContext() {
  const value = useContext(ConnectionsUnitContext)
  if (!value) throw new Error('ConnectionsOperationProvider is required')
  return value
}

export function useConnectionsUnitActivation() {
  const { setActive } = useConnectionsUnitContext()
  useEffect(() => {
    setActive(true)
    return () => setActive(false)
  }, [setActive])
}
