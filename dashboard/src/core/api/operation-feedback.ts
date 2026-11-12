import { ApiError, type ApiFieldError } from './client'

export type RetryGuidance = 'fix_request' | 'refresh_state' | 'same_request' | 'wait' | 'reconnect' | 'contact_support' | 'none'
export type OperationFeedback = {
  surface: 'inline' | 'banner'
  code: string | null
  requestId: string | null
  fields: ApiFieldError[]
  retry: RetryGuidance
  retryAt: number | null
  outcomeUnknown: boolean
}

/** A view model only: interpreting feedback never submits, retries or announces. */
export function operationFeedback(error: Error | DOMException, now?: number): OperationFeedback
export function operationFeedback(error: unknown, now?: number): OperationFeedback | null
export function operationFeedback(error: unknown, now?: number): OperationFeedback | null {
  const observedAt = now ?? (error instanceof ApiError ? error.observedAt : Date.now())
  if (error === null || error === undefined) return null
  if (!(error instanceof ApiError)) {
    return { surface: 'banner', code: null, requestId: null, fields: [],
      retry: error instanceof TypeError ? 'same_request' : 'none', retryAt: null,
      outcomeUnknown: error instanceof TypeError ||
        ((error instanceof Error || error instanceof DOMException) && ['AbortError', 'TimeoutError'].includes(error.name)) }
  }
  const known = ['fix_request', 'refresh_state', 'same_request', 'wait', 'reconnect', 'contact_support'] as const
  const retry = known.find((value) => value === error.retry) ?? 'none'
  return {
    surface: error.fields.length ? 'inline' : 'banner',
    code: error.body?.code ?? null,
    requestId: error.requestId ?? null,
    fields: error.fields.map(({ path, code }) => ({ path, code })),
    retry,
    retryAt: retry === 'wait' && error.retryAfterSeconds !== undefined && Number.isFinite(error.retryAfterSeconds)
      ? observedAt + Math.max(0, error.retryAfterSeconds) * 1000 : null,
    outcomeUnknown: retry === 'same_request',
  }
}

/** Stable identifiers let the UI update one notification while retaining receipt access. */
export function operationFeedbackId(kind: string, id: string): string {
  return `operation:${encodeURIComponent(kind)}:${encodeURIComponent(id)}`
}

export type FeedbackWorkflow = 'session' | 'credentials' | 'recovery' | 'account'
const sessionFields: Record<string, string> = { action: 'action', market_mode: 'marketMode', selection: 'selection',
  profile_id: 'profileId', duration_mode: 'durationMode', duration_seconds: 'durationSeconds' }
/** Only editable, declared inputs receive inline errors. Version/token/readonly errors stay global. */
export function deriveFormFeedback(error: unknown, workflow: FeedbackWorkflow, credentialFields: readonly string[] = []) {
  const feedback = operationFeedback(error)
  const inlineErrors: Record<string, string[]> = {}
  const globalFields: ApiFieldError[] = []
  for (const field of feedback?.fields ?? []) {
    const path = field.path.replace(/^\/body\//, '').replace(/^body\./, '').replace(/\//g, '.')
    const credentialName = path.replace(/^credentials\./, '')
    const input = workflow === 'session' ? sessionFields[path]
      : workflow === 'credentials' && credentialFields.includes(credentialName) ? credentialName
      : workflow === 'account' && ['email', 'role', 'active'].includes(path) ? path : undefined
    if (input) (inlineErrors[input] ??= []).push(field.code)
    else globalFields.push({ path: field.path, code: field.code })
  }
  return { feedback, inlineErrors, globalFields }
}
export type FormFeedbackModel = ReturnType<typeof deriveFormFeedback>
