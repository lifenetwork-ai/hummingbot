import { useCallback, useMemo, useRef, useState, type PropsWithChildren } from 'react'
import { useReconciliation } from './useReconciliation'
import { OperationContext } from './operation-context'

/** Keeps unresolved reconciliation operation state alive across route and tab changes. */
export function ReconciliationOperationProvider({ children }: PropsWithChildren) {
  const leases = useRef(new Set<symbol>())
  const [activeUnits, setActiveUnits] = useState(0)
  const acquireReadLease = useCallback(() => {
    const token = Symbol()
    leases.current.add(token)
    setActiveUnits(leases.current.size)
    let released = false
    return () => {
      if (released) return
      released = true
      leases.current.delete(token)
      setActiveUnits(leases.current.size)
    }
  }, [])
  const operation = useReconciliation({ active: activeUnits > 0 })
  const value = useMemo(() => ({ operation, acquireReadLease }), [operation, acquireReadLease])
  return <OperationContext.Provider value={value}>{children}</OperationContext.Provider>
}
