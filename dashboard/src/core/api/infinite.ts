import { infiniteQueryOptions, useInfiniteQuery, type FetchNextPageOptions } from '@tanstack/react-query'
import type { AdminApi, ReadInput, ReadResponse } from './admin'
import { useAdminApi } from './ApiContext'
import { readRetryDelay, retryRead } from './queryClient'

export type CursorOperation = 'listAdminAccounts' | 'getAdminAudit' | 'listCommands' |
  'listFills' | 'listOrders' | 'listSessions' | 'getSessionEvents'
// Cursor is owned by useInfiniteQuery and excluded from the filter identity.
export type CursorInput<K extends CursorOperation> = Omit<ReadInput<K>, 'query'> & {
  query?: Omit<NonNullable<ReadInput<K>['query']>, 'cursor'>
}
function nextCursor(page: unknown): string | undefined {
  if (!page || typeof page !== 'object' || !('next_cursor' in page)) return undefined
  return typeof page.next_cursor === 'string' ? page.next_cursor : undefined
}

export function adminInfiniteOptions<K extends CursorOperation>(api: AdminApi, operation: K, input: CursorInput<K>) {
  return infiniteQueryOptions({
    queryKey: ['admin', operation, 'infinite', input] as const,
    initialPageParam: null as string | null,
    queryFn: ({ pageParam, signal }): Promise<ReadResponse<K>> => api.read(operation, {
      ...input, query: { ...input.query, ...(pageParam === null ? {} : { cursor: pageParam }) },
    } as ReadInput<K>, signal),
    getNextPageParam: (lastPage: ReadResponse<K>, _pages: ReadResponse<K>[], _pageParam: string | null, pageParams: (string | null)[]) => {
      const next = nextCursor(lastPage)
      return typeof next === 'string' && next && !pageParams.includes(next) ? next : undefined
    },
    retry: retryRead, retryDelay: readRetryDelay,
    refetchOnWindowFocus: false, // History pages load on demand rather than polling every loaded page.
  })
}
export function useAdminInfiniteQuery<K extends CursorOperation>(operation: K, input: CursorInput<K>, options: { enabled?: boolean } = {}) {
  const api = useAdminApi()
  const query = useInfiniteQuery({ ...adminInfiniteOptions(api, operation, input), enabled: options.enabled })
  return { ...query, fetchNextPage: (next?: FetchNextPageOptions) =>
    query.fetchNextPage({ cancelRefetch: false, ...next }) }

}
