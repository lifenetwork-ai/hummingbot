import { useCallback, useState, type PropsWithChildren } from 'react'
import { RecoveryContext } from './RecoveryOperationContext'
import { useRecoveryController } from './useRecoveryController'

export function RecoveryOperationProvider({ children }: PropsWithChildren) {
  const [activeUnits, setActiveUnits] = useState(0)
  const activate = useCallback(() => {
    let released = false
    setActiveUnits((count) => count + 1)
    return () => {
      if (released) return
      released = true
      setActiveUnits((count) => Math.max(0, count - 1))
    }
  }, [])
  const controller = useRecoveryController(activeUnits > 0, activate)
  return <RecoveryContext.Provider value={controller}>{children}</RecoveryContext.Provider>
}
