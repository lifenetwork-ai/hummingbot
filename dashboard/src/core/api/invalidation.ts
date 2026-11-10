import type { ReadOperation, WriteOperation } from './admin'
import { adminKeys } from './queries'
import type { QueryClient } from '@tanstack/react-query'

const controls: ReadOperation[] = ['getRuntimeStatus', 'listCommands', 'listSessions',
  'getSessionConfiguration', 'getLiquidity', 'getBalances', 'getPositions', 'getPerformance', 'getExchangeCredentialStatus']
const credentials: ReadOperation[] = [...controls, 'listCredentialInputs']
const affected: Record<WriteOperation, ReadOperation[]> = {
  submitCommand: controls,
  setExchangeCredentials: credentials,
  disconnectExchangeCredentials: [...credentials, 'listOrders', 'listFills'],
  repairCleanupCredentials: credentials,
  retryCredentialCleanup: credentials,
  requestReconciliation: ['getReconciliation'],
  checkRecovery: ['getRecovery'],
  resetRecovery: [...controls, 'getRecovery', 'getReconciliation'],
  adminLogin: ['getAdminIdentity', 'getAdminBootstrap', 'listAdminAccounts'],
  addAdminAccount: ['listAdminAccounts', 'getAdminBootstrap'],
  changeAdminAccount: ['listAdminAccounts', 'getAdminIdentity'],
}
export function invalidateAfterMutation(client: QueryClient, operation: WriteOperation) {
  return Promise.all([...affected[operation], 'getAdminAudit' as const].map((read) =>
    client.invalidateQueries({ queryKey: adminKeys.operation(read) })))
}
export function invalidateAfterReceipt(client: QueryClient, operation: ReadOperation) {
  const targets = operation === 'getCommand' ? controls
    : operation === 'getRecoveryOperation' ? [...controls, 'getRecovery', 'getReconciliation'] as ReadOperation[]
    : operation === 'getCredentialOperation' ? [...credentials, 'listOrders', 'listFills'] as ReadOperation[]
    : operation === 'getReconciliationById' ? ['getReconciliation'] as ReadOperation[] : []
  return Promise.all([...targets, 'getAdminAudit' as const].map((read) =>
    client.invalidateQueries({ queryKey: adminKeys.operation(read) })))
}
