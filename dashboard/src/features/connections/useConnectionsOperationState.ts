import { useContext } from 'react'
import { connectionsOperationContext } from './operation-state-context'

export function useConnectionsOperationState() {
  const state = useContext(connectionsOperationContext)
  if (!state) throw new Error('ConnectionsOperationStateProvider is required')
  return state
}
