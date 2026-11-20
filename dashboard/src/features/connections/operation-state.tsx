import { useRef, useState, type PropsWithChildren } from 'react'
import { connectionsOperationContext, type DecisionLock, type RetryCandidate } from './operation-state-context'

export function ConnectionsOperationStateProvider({ children }: PropsWithChildren) {
  const locksRef = useRef(new Map<string, DecisionLock>())
  const candidatesRef = useRef(new Map<string, RetryCandidate>())
  const [locks, setLocks] = useState<DecisionLock[]>([])
  return <connectionsOperationContext.Provider value={{ locksRef, candidatesRef, locks, setLocks }}>{children}</connectionsOperationContext.Provider>
}
