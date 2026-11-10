import type { components, operations } from './schema'

export type ApiErrorBody = components['schemas']['ErrorResponse']
export type CommandReceipt = components['schemas']['CommandResponseOutput']
export type CredentialReceipt = components['schemas']['CredentialReceiptOutput']

/** Extract payloads from generated operation IDs, keeping endpoint DTOs authoritative. */
export type RequestBody<K extends keyof operations> = operations[K] extends {
  requestBody: { content: { 'application/json': infer Body } }
} ? Body : never
export type ResponseBody<K extends keyof operations, S extends number = 200> =
  operations[K] extends { responses: infer Responses }
    ? S extends keyof Responses
      ? Responses[S] extends { content: { 'application/json': infer Body } } ? Body : never
      : never
    : never
