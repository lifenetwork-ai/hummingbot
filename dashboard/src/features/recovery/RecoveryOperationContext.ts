import { createContext, useContext, useEffect } from 'react'
import { useRecoveryController } from './useRecoveryController'

type RecoveryController = ReturnType<typeof useRecoveryController>
export const RecoveryContext = createContext<RecoveryController | null>(null)

export function useRecoveryOperationLock() {
  const controller = useContext(RecoveryContext)
  if (!controller) throw new Error('useRecoveryOperationLock must be used within RecoveryOperationProvider')
  return controller.lockSessionAndConnections
}

export function useRecoveryOperationContext() {
  const controller = useContext(RecoveryContext)
  if (!controller) throw new Error('useRecoveryOperationContext must be used within RecoveryOperationProvider')
  const activate = controller.activate
  useEffect(() => activate(), [activate])
  return controller
}
