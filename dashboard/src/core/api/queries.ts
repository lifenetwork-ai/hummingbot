import { queryOptions, skipToken } from '@tanstack/react-query'
import type { AdminApi, ReadInput, ReadOperation, ReadResponse } from './admin'
import { readRetryDelay, retryRead } from './queryClient'

export const adminKeys = {
  all: ['admin'] as const,
  operation: (operation: ReadOperation) => ['admin', operation] as const,
  query: <K extends ReadOperation>(operation: K, input: ReadInput<K> | undefined) => ['admin', operation, input ?? null] as const,
}

const cadence: Partial<Record<ReadOperation, number>> = {
  getRuntimeStatus: 15_000, getLiquidity: 2000, getBalances: 5000, getPositions: 5000,
  getExchangeCredentialStatus: 5000,
  listOrders: 10_000, listFills: 10_000, getSessionEvents: 10_000,
  getPerformance: 30_000, getDailyStatistics: 30_000,
}
export const terminalStatuses = new Set(['succeeded', 'failed', 'superseded'])

function receiptStatus(data: unknown): string | undefined {
  if (data && typeof data === 'object' && 'status' in data && typeof data.status === 'string') return data.status
  return undefined
}

export function receiptFinished(operation: ReadOperation, data: unknown): boolean {
  if (!['getCommand', 'getCredentialOperation', 'getReconciliationById', 'getRecoveryOperation'].includes(operation)) return false
  const status = receiptStatus(data)
  return status !== undefined && terminalStatuses.has(status)
}

export function pollingInterval(operation: ReadOperation, data: unknown, error: Error | null, failures: number): number | false {
  if (receiptFinished(operation, data)) return false
  if (error && !retryRead(0, error)) return false
  const pendingReceipt = ['getCommand', 'getCredentialOperation', 'getReconciliationById', 'getRecoveryOperation'].includes(operation)
  const interval = pendingReceipt ? 1000 : cadence[operation]
  if (!interval) return false
  if (error) return Math.max(interval, readRetryDelay(Math.min(failures, 5), error))
  if (operation === 'getDailyStatistics' && data && typeof data === 'object' &&
      'freshness' in data && data.freshness === 'stale' &&
      'retry_after_seconds' in data && typeof data.retry_after_seconds === 'number') {
    return Math.max(1000, data.retry_after_seconds * 1000)
  }
  return interval
}

export function adminQueryOptions<K extends ReadOperation>(api: AdminApi, operation: K, input: ReadInput<K> | undefined) {
  return queryOptions<ReadResponse<K>, Error>({
    queryKey: adminKeys.query(operation, input),
    queryFn: input === undefined ? skipToken : ({ signal }) => api.read(operation, input, signal),
    retry: retryRead, retryDelay: readRetryDelay,
    refetchOnWindowFocus: true, refetchOnReconnect: true,
    refetchIntervalInBackground: false,
    refetchInterval: (query) => pollingInterval(operation, query.state.data, query.state.error, query.state.fetchFailureCount),
  })
}
