import { createContext, type MutableRefObject } from 'react'

export type LockedAction = 'set' | 'disconnect' | 'retry' | 'repair'
export type DecisionLock = {
  key: string
  scopeToken: string
  action: LockedAction
  exchange: string
  connector: string
  credentialVersion: number
  controlVersion: number
  operationVersion: number | null
  idempotencyKey: string
  retryReviewed: boolean
  state: 'pending' | 'accepted' | 'unknown'
  receiptId: string | null
  retryAt?: number
}

export type RetryCandidate = { run: () => Promise<{ id?: string }> }
export type ConnectionsOperationState = {
  locksRef: MutableRefObject<Map<string, DecisionLock>>
  candidatesRef: MutableRefObject<Map<string, RetryCandidate>>
  locks: DecisionLock[]
  setLocks: (locks: DecisionLock[]) => void
}

export const connectionsOperationContext = createContext<ConnectionsOperationState | null>(null)
