import { useEffect, useRef } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import type { ReadInput, ReadOperation, WriteInput, WriteOperation } from './admin'
import { useAdminApi } from './ApiContext'
import { adminQueryOptions, receiptFinished } from './queries'
import { invalidateAfterMutation, invalidateAfterReceipt } from './invalidation'

export function useAdminQuery<K extends ReadOperation>(operation: K, input: ReadInput<K> | undefined, options: { enabled?: boolean; poll?: boolean } = {}) {
  const api = useAdminApi()
  const client = useQueryClient()
  const query = useQuery({ ...adminQueryOptions(api, operation, input),
    enabled: options.enabled, ...(options.poll === false ? { refetchInterval: false as const } : {}) })
  const observed = useRef<unknown>()
  useEffect(() => {
    if (query.data && observed.current !== query.data && receiptFinished(operation, query.data)) {
      observed.current = query.data
      void invalidateAfterReceipt(client, operation)
    }
  }, [client, operation, query.data])
  return query
}

/** Request bodies (including credentials) never enter React Query's mutation state. */
export function useAdminMutation<K extends WriteOperation>(operation: K) {
  const api = useAdminApi()
  const client = useQueryClient()
  const requests = useRef(new Map<number, WriteInput<K>>())
  const sequence = useRef(0)
  const busy = useRef(false)
  const mutation = useMutation({
    mutationKey: ['admin', operation],
    mutationFn: async (ticket: number) => {
      const input = requests.current.get(ticket)
      requests.current.delete(ticket)
      if (!input) throw new Error('Mutation request is no longer available')
      return api.mutate(operation, input)
    },
    retry: false, gcTime: 0, networkMode: 'always',
    onSuccess: () => { void invalidateAfterMutation(client, operation) },
  })
  const execute = async (input: WriteInput<K>) => {
    if (busy.current) throw new Error('A request is already pending; follow its receipt before submitting again')
    busy.current = true
    const ticket = ++sequence.current
    requests.current.set(ticket, input)
    try { return await mutation.mutateAsync(ticket) }
    finally { requests.current.delete(ticket); busy.current = false }
  }
  // Exclude the internal ticket and raw mutate functions from the consumer API.
  const { variables: _variables, mutate: _mutate, mutateAsync: _mutateAsync, ...result } = mutation
  return { ...result, mutateAsync: execute }
}

export function useCommandReceipt(id: string | undefined) {
  return useAdminQuery('getCommand', id ? { path: { command_id: id } } : undefined)
}
export function useCredentialReceipt(id: string | undefined) {
  return useAdminQuery('getCredentialOperation', id ? { path: { operation_id: id } } : undefined)
}
export function useReconciliationReceipt(id: string | undefined) {
  return useAdminQuery('getReconciliationById', id ? { path: { check_id: id } } : undefined)
}
