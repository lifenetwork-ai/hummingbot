import { QueryClient } from '@tanstack/react-query'
import { ApiError } from './client'

export function retryRead(failures: number, error: Error): boolean {
  if (failures >= 2) return false
  if (error instanceof ApiError) {
    return error.status === 503 && (!error.retry || ['wait', 'same_request'].includes(error.retry))
  }
  return error instanceof TypeError // Fetch network errors; abort/parse errors are not retried.
}

export function readRetryDelay(attempt: number, error: Error): number {
  return error instanceof ApiError && error.retryAfterSeconds !== undefined
    ? Math.max(0, error.retryAfterSeconds * 1000)
    : Math.min(1000 * 2 ** attempt, 30_000)
}

export function createDashboardQueryClient() {
  return new QueryClient({ defaultOptions: {
    queries: { staleTime: 0, retry: retryRead, retryDelay: readRetryDelay,
      refetchOnWindowFocus: true, refetchOnReconnect: true },
    mutations: { retry: false, gcTime: 0, networkMode: 'always' },
  } })
}
