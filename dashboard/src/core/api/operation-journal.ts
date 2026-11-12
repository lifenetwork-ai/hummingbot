import type { ReceiptScope } from './receipt-tracking'
import type { WriteInput } from './admin'

export type RetryableOperation = 'submitCommand' | 'requestReconciliation'
export type JournalChannel = 'session' | 'stop' | 'reconciliation'
export type PendingOperation = {
  [K in RetryableOperation]: { operation: K; input: WriteInput<K> }
}[RetryableOperation]
type JournalEntry = PendingOperation & { version: 1; createdAt: number }
const prefix = 'life-mm.pending-operation.v2:'
const ttl = 24 * 60 * 60 * 1000

function storageOrNull(): Storage | null {
  try { return typeof window === 'undefined' ? null : window.localStorage } catch { return null }
}
function validScope(scope: ReceiptScope | undefined): scope is ReceiptScope {
  return Boolean(scope?.apiBaseUrl.trim() && scope.principalId.trim() && scope.botId.trim())
}
function scopeKey(scope: ReceiptScope, channel: JournalChannel) {
  return prefix + [scope.apiBaseUrl.replace(/\/+$/, ''), scope.principalId, scope.botId, channel].map(encodeURIComponent).join(':')
}
function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}
function exact(value: Record<string, unknown>, keys: string[]) {
  return Object.keys(value).length === keys.length && keys.every((key) => Object.hasOwn(value, key))
}
function text(value: unknown): value is string { return typeof value === 'string' && value.trim().length > 0 && value.length <= 512 }
function integer(value: unknown): value is number { return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0 }
function oneOf(value: unknown, values: string[]) { return typeof value === 'string' && values.includes(value) }

/** Validate an exact non-secret DTO, including on reads from untrusted browser storage. */
function validInput(operation: unknown, input: unknown): input is WriteInput<RetryableOperation> {
  if (!record(input) || !exact(input, ['body', 'idempotencyKey']) || !text(input.idempotencyKey) || !record(input.body)) return false
  const body = input.body
  if (operation === 'submitCommand') {
    if (!integer(body.expected_version)) return false
    if (body.action === 'stop') return exact(body, ['action', 'expected_version', 'session_id']) && text(body.session_id)
    if (!oneOf(body.action, ['start', 'switch'])) return false
    const fields = ['action', 'expected_version', 'expected_catalogue_hash', 'market_mode', 'selection', 'profile_id', 'duration_mode', 'duration_seconds']
    if (body.action === 'switch') fields.push('session_id')
    return exact(body, fields) && (body.action !== 'switch' || text(body.session_id)) &&
      (body.expected_catalogue_hash === null || text(body.expected_catalogue_hash)) &&
      oneOf(body.market_mode, ['spot', 'perpetual', 'both']) && oneOf(body.selection, ['fixed', 'random']) &&
      (body.selection === 'fixed' ? text(body.profile_id) : body.profile_id === null) &&
      oneOf(body.duration_mode, ['timed', 'until_stopped']) &&
      (body.duration_mode === 'until_stopped' ? body.duration_seconds === null : body.duration_seconds === null || integer(body.duration_seconds))
  }
  return operation === 'requestReconciliation' && exact(body, ['market', 'pair', 'lookback_seconds', 'max_orders', 'max_pages']) &&
    oneOf(body.market, ['spot', 'perpetual', 'all']) && (body.pair === null || text(body.pair)) &&
    integer(body.lookback_seconds) && integer(body.max_orders) && integer(body.max_pages)
}
function channelFor(entry: PendingOperation): JournalChannel {
  return entry.operation === 'requestReconciliation' ? 'reconciliation' : entry.input.body.action === 'stop' ? 'stop' : 'session'
}

export function readPendingOperation(scope: ReceiptScope | undefined, channel: JournalChannel = 'session', storage = storageOrNull(), now = Date.now()): PendingOperation | undefined {
  if (!validScope(scope) || !storage || !Number.isFinite(now)) return undefined
  const key = scopeKey(scope, channel)
  try {
    const raw = storage.getItem(key)
    if (!raw) return undefined
    const entry: unknown = JSON.parse(raw)
    if (!record(entry) || !exact(entry, ['version', 'operation', 'input', 'createdAt']) || entry.version !== 1 ||
      typeof entry.createdAt !== 'number' || !Number.isFinite(entry.createdAt) || now - entry.createdAt >= ttl || entry.createdAt > now + 5 * 60_000 ||
      !validInput(entry.operation, entry.input)) {
      storage.removeItem(key); return undefined
    }
    // Runtime validation above ties each DTO to its operation discriminator.
    const candidate = { operation: entry.operation, input: entry.input } as PendingOperation
    if (channelFor(candidate) !== channel) { storage.removeItem(key); return undefined }
    return candidate
  } catch { return undefined }
}

export function savePendingOperation<K extends RetryableOperation>(scope: ReceiptScope | undefined, operation: K, input: WriteInput<K>, storage = storageOrNull(), now = Date.now()): boolean {
  if (!validScope(scope) || !storage || !Number.isFinite(now) || !validInput(operation, input)) return false
  const entry = { operation, input } as PendingOperation
  const channel = channelFor(entry)
  try {
    const previous = readPendingOperation(scope, channel, storage, now)
    if (previous && (previous.input.idempotencyKey !== input.idempotencyKey || JSON.stringify(previous.input) !== JSON.stringify(input))) return false
    storage.setItem(scopeKey(scope, channel), JSON.stringify({ ...entry, version: 1, createdAt: now } satisfies JournalEntry))
    return true
  } catch { return false }
}

/** Matching-key deletion prevents an old response from discarding a newer decision. */
export function clearPendingOperation(scope: ReceiptScope | undefined, channel: JournalChannel = 'session', expectedIdempotencyKey?: string, storage = storageOrNull()) {
  if (!validScope(scope) || !storage) return
  try {
    if (expectedIdempotencyKey !== undefined && readPendingOperation(scope, channel, storage)?.input.idempotencyKey !== expectedIdempotencyKey) return
    storage.removeItem(scopeKey(scope, channel))
  } catch { /* blocked storage must not interrupt receipt inspection */ }
}
