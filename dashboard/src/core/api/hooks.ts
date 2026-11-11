import { useEffect, useRef } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import type { ReadInput, ReadOperation, WriteInput, WriteOperation } from './admin'
import { useAdminApi } from './ApiContext'
import { adminQueryOptions, receiptFinished } from './queries'
import { invalidateAfterMutation, invalidateAfterReceipt } from './invalidation'
import { useApiSessionScope } from './ApiSessionContext'
import { acceptedReceiptReference, rememberAcceptedReceipt, type ReceiptReference, type ReceiptScope } from './receipt-tracking'
import { trustedBotId } from './trustedBotId'
import { projectReceiptOutcome, type ReceiptOutcome } from './receipt-inspection'

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
  const session = useApiSessionScope()
  const requests = useRef(new Map<number, { input: WriteInput<K>; scope: ReceiptScope | undefined }>())
  const sequence = useRef(0)
  const busy = useRef(false)
  const mutation = useMutation({
    mutationKey: ['admin', operation],
    mutationFn: async (ticket: number) => {
      const request = requests.current.get(ticket)
      requests.current.delete(ticket)
      if (!request) throw new Error('Mutation request is no longer available')
      const response = await api.mutate(operation, request.input)
      const receipt = acceptedReceiptReference(operation, response)
      if (receipt) rememberAcceptedReceipt(request.scope, receipt)
      return response
    },
    retry: false, gcTime: 0, networkMode: 'always',
    onSuccess: () => { void invalidateAfterMutation(client, operation) },
  })
  const execute = async (input: WriteInput<K>) => {
    if (busy.current) throw new Error('A request is already pending; follow its receipt before submitting again')
    busy.current = true
    const ticket = ++sequence.current
    const botId = trustedBotId(client)
    const scope = session?.apiBaseUrl && session.principalId && botId
      ? { apiBaseUrl: session.apiBaseUrl, principalId: session.principalId, botId }
      : undefined
    requests.current.set(ticket, { input, scope })
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
export function useRecoveryReceipt(id: string | undefined) {
  return useAdminQuery('getRecoveryOperation', id ? { path: { operation_id: id } } : undefined)
}

export type TrackedReceiptInspection = {
  reference: ReceiptReference
  phase: 'idle' | 'loading' | 'ready' | 'error'
  status?: string
  detail?: string
  error: Error | null
  outcome?: ReceiptOutcome
}

export function useReceiptInspection(reference: ReceiptReference | null | undefined): TrackedReceiptInspection | null {
  const isCommand = reference?.kind === 'command'
  const isCredential = reference?.kind === 'credential'
  const isReconciliation = reference?.kind === 'reconciliation'
  const isRecovery = reference?.kind === 'recovery'

  const command = useCommandReceipt(isCommand ? reference.id : undefined)
  const credential = useCredentialReceipt(isCredential ? reference.id : undefined)
  const reconciliation = useReconciliationReceipt(isReconciliation ? reference.id : undefined)
  const recovery = useRecoveryReceipt(isRecovery ? reference.id : undefined)

  if (!reference) return null

  const active = isCommand ? command : isCredential ? credential : isReconciliation ? reconciliation : recovery
  const data = active.data as { status?: string; detail?: string; error?: string } | undefined
  const status = typeof data?.status === 'string' ? data.status : undefined
  const detail = typeof data?.detail === 'string' ? data.detail : typeof data?.error === 'string' ? data.error : undefined

  return {
    reference,
    phase: active.isLoading ? 'loading' : active.isError ? 'error' : active.data !== undefined ? 'ready' : 'idle',
    status,
    detail,
    error: active.error,
    outcome: active.data ? projectReceiptOutcome(reference, active.data) : undefined,
  }
}
