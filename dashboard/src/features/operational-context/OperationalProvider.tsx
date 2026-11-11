import type { PropsWithChildren } from 'react'
import { OperationalContext } from './useOperationalContext'
import { useOperationalController } from './useOperationalController'

/** Mount under the authenticated route, within the auth/session API providers. */
export function OperationalProvider({ children }: PropsWithChildren) {
  const context = useOperationalController()
  return <OperationalContext.Provider value={context}>{children}</OperationalContext.Provider>
}
