import { useState, type PropsWithChildren } from 'react'
import { ConnectionsOperationStateProvider } from './operation-state'
import { useConnectionsController } from './useConnectionsController'
import { ConnectionsUnitContext } from './connections-unit-context'

function ConnectionsControllerOwner({ children, recoveryPending }: PropsWithChildren<{ recoveryPending?: boolean }>) {
  const [active, setActive] = useState(false)
  const controller = useConnectionsController({ active, recoveryPending })
  return <ConnectionsUnitContext.Provider value={{ controller, setActive }}>{children}</ConnectionsUnitContext.Provider>
}

/** Keeps only operation locks and retry candidates alive across tabs and protected routes. */
export function ConnectionsOperationProvider({ children, recoveryPending }: PropsWithChildren<{ recoveryPending?: boolean }>) {
  return <ConnectionsOperationStateProvider><ConnectionsControllerOwner recoveryPending={recoveryPending}>{children}</ConnectionsControllerOwner></ConnectionsOperationStateProvider>
}
