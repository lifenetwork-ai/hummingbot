import type { ApiClient } from './client'
import type { operations } from './schema'
import type { ResponseBody } from './types'

/** JSON admin API operations from docs/api/openapi.json, excluding documentation assets. */
export const adminOperations = {
  listAdminAccounts: ['GET', '/access/accounts'],
  addAdminAccount: ['POST', '/access/accounts'],
  changeAdminAccount: ['POST', '/access/accounts/{account_id}'],
  getAdminBootstrap: ['GET', '/access/bootstrap'],
  adminLogin: ['POST', '/access/login'],
  getAdminIdentity: ['GET', '/access/me'],
  getAdminAudit: ['GET', '/audit'],
  getBalances: ['GET', '/balances'],
  listCommands: ['GET', '/commands'],
  submitCommand: ['POST', '/commands'],
  getCommand: ['GET', '/commands/{command_id}'],
  listCredentialInputs: ['GET', '/credentials'],
  getCredentialOperation: ['GET', '/credentials/operations/{operation_id}'],
  retryCredentialCleanup: ['POST', '/credentials/operations/{operation_id}/retry'],
  getExchangeCredentialStatus: ['GET', '/credentials/{exchange}'],
  setExchangeCredentials: ['POST', '/credentials/{exchange}/{connector}'],
  repairCleanupCredentials: ['POST', '/credentials/{exchange}/{connector}/cleanup-credentials'],
  disconnectExchangeCredentials: ['POST', '/credentials/{exchange}/{connector}/revoke'],
  listFills: ['GET', '/fills'],
  listOrders: ['GET', '/orders'],
  getOrder: ['GET', '/orders/{order_id}'],
  getPerformance: ['GET', '/performance'],
  getPositions: ['GET', '/positions'],
  listProfiles: ['GET', '/profiles'],
  getReconciliation: ['GET', '/reconciliation'],
  requestReconciliation: ['POST', '/reconciliation/check'],
  getReconciliationById: ['GET', '/reconciliation/{check_id}'],
  getRecovery: ['GET', '/recovery'],
  checkRecovery: ['POST', '/recovery/check'],
  getRecoveryOperation: ['GET', '/recovery/operations/{operation_id}'],
  resetRecovery: ['POST', '/recovery/reset'],
  listReferencePairs: ['GET', '/reference-pairs'],
  listSessions: ['GET', '/sessions'],
  getSessionConfiguration: ['GET', '/sessions/{session_id}/configuration'],
  getSessionEvents: ['GET', '/sessions/{session_id}/events'],
  getDailyStatistics: ['GET', '/statistics/daily'],
  getLiquidity: ['GET', '/statistics/liquidity'],
  getRuntimeStatus: ['GET', '/status'],
} as const satisfies Partial<Record<keyof operations, readonly ['GET' | 'POST', string]>>

export type OperationId = keyof typeof adminOperations
type Method<K extends OperationId> = (typeof adminOperations)[K][0]
export type ReadOperation = { [K in OperationId]: Method<K> extends 'GET' ? K : never }[OperationId]
export type WriteOperation = { [K in OperationId]: Method<K> extends 'POST' ? K : never }[OperationId]

type OperationParameters<K extends OperationId> = operations[K] extends { parameters: infer P } ? P : Record<never, never>
type PathParameters<K extends OperationId> = OperationParameters<K> extends { path: infer P } ? P : Record<never, never>
type QueryParameters<K extends OperationId> = OperationParameters<K> extends { query?: infer P } ? NonNullable<P> : Record<never, never>
type Request<K extends OperationId> = operations[K] extends { requestBody: { content: { 'application/json': infer B } } } ? B : never
type IsRequiredBody<K extends OperationId> = operations[K] extends { requestBody: { content: { 'application/json': unknown } } } ? true : false
type ReadInputParts<K extends ReadOperation> = (keyof PathParameters<K> extends never ? { path?: never } : { path: PathParameters<K> }) & (keyof QueryParameters<K> extends never ? { query?: never } : { query?: QueryParameters<K> })
type WriteInputParts<K extends WriteOperation> = (keyof PathParameters<K> extends never ? { path?: never } : { path: PathParameters<K> }) & (keyof QueryParameters<K> extends never ? { query?: never } : { query?: QueryParameters<K> }) & (Request<K> extends never ? { body?: never } : IsRequiredBody<K> extends true ? { body: Request<K> } : { body?: Request<K> }) & (OperationParameters<K> extends { header: { 'Idempotency-Key': unknown } } ? { idempotencyKey: string } : { idempotencyKey?: never })

export type ReadInput<K extends ReadOperation> = ReadInputParts<K>
export type WriteInput<K extends WriteOperation> = WriteInputParts<K>
export type ReadResponse<K extends ReadOperation> = ResponseBody<K>
export type WriteResponse<K extends WriteOperation> = ResponseBody<K, 200 | 201 | 202>

function buildPath(template: string, path: Record<string, unknown> = {}, query: Record<string, unknown> = {}): string {
  const route = template.replace(/\{([^}]+)\}/g, (_match, key: string) => {
    const value = path[key]
    if (value === undefined || value === null) throw new Error(`Missing path parameter: ${key}`)
    return encodeURIComponent(String(value))
  })
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null) continue
    if (Array.isArray(value)) value.forEach((item) => search.append(key, String(item)))
    else search.set(key, String(value))
  }
  const suffix = search.toString()
  return suffix ? `${route}?${suffix}` : route
}

export type AdminApi = {
  read<K extends ReadOperation>(operation: K, input: ReadInput<K>, signal?: AbortSignal): Promise<ReadResponse<K>>
  mutate<K extends WriteOperation>(operation: K, input: WriteInput<K>, signal?: AbortSignal): Promise<WriteResponse<K>>
}

export function createAdminApi(client: ApiClient): AdminApi {
  return {
    read<K extends ReadOperation>(operation: K, params: ReadInput<K>, signal?: AbortSignal): Promise<ReadResponse<K>> {
      const [method, route] = adminOperations[operation]
      if (method !== 'GET') throw new Error(`Operation is not a read: ${operation}`)
      const path = buildPath(route, params.path as Record<string, unknown>, params.query as Record<string, unknown>)
      return client.request<ReadResponse<K>>(path, { method, signal })
    },
    mutate<K extends WriteOperation>(operation: K, request: WriteInput<K>, signal?: AbortSignal): Promise<WriteResponse<K>> {
      const [method, route] = adminOperations[operation]
      if (method !== 'POST') throw new Error(`Operation is not a mutation: ${operation}`)
      const path = buildPath(route, request.path as Record<string, unknown>, request.query as Record<string, unknown>)
      return client.request<WriteResponse<K>>(path, {
        method,
        signal,
        ...(request.body === undefined ? {} : { body: request.body as object }),
        ...(request.idempotencyKey === undefined ? {} : { idempotencyKey: request.idempotencyKey }),
      })
    },
  }
}
