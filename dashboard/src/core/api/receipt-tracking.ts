export type ReceiptKind = 'command' | 'credential' | 'reconciliation' | 'recovery'
export type ReceiptReference = Readonly<{ kind: ReceiptKind; id: string }>
export type ReceiptScope = Readonly<{ apiBaseUrl: string; principalId: string; botId: string }>

const storagePrefix = 'life-mm.accepted-receipts.v1:'
const maxReceipts = 50
const ttlMs = 7 * 24 * 60 * 60 * 1000
const kinds = new Set<ReceiptKind>(['command', 'credential', 'reconciliation', 'recovery'])
type StoredReceipt = ReceiptReference & { acceptedAt: number }
type Store = { version: 1; receipts: StoredReceipt[] }

function receiptFields(value: unknown): { id: string; status: string } | undefined {
  if (!value || typeof value !== 'object' || !('id' in value) || typeof value.id !== 'string' ||
      !('status' in value) || typeof value.status !== 'string') return undefined
  return { id: value.id, status: value.status }
}

export function acceptedReceiptReference(operation: string, response: unknown): ReceiptReference | undefined {
  let kind: ReceiptKind | undefined
  const candidate: unknown = response
  if (operation === 'submitCommand') kind = 'command'
  else if (['setExchangeCredentials', 'repairCleanupCredentials', 'disconnectExchangeCredentials', 'retryCredentialCleanup'].includes(operation)) kind = 'credential'
  else if (operation === 'requestReconciliation') kind = 'reconciliation'
  else if (operation === 'checkRecovery' || operation === 'resetRecovery') kind = 'recovery'
  if (!kind) return undefined
  const fields = receiptFields(candidate)
  const validStatuses: Record<ReceiptKind, readonly string[]> = {
    command: ['accepted', 'applying', 'succeeded', 'failed', 'superseded'],
    credential: ['accepted', 'applying', 'succeeded', 'failed'],
    reconciliation: ['accepted', 'applying', 'succeeded', 'failed'],
    recovery: ['accepted', 'applying', 'succeeded', 'failed'],
  }
  return fields && validStatuses[kind].includes(fields.status) && fields.id.trim() && fields.id.length <= 512 ? { kind, id: fields.id } : undefined
}

function usableScope(scope: ReceiptScope | undefined): scope is ReceiptScope {
  return Boolean(scope?.apiBaseUrl.trim() && scope.principalId.trim() && scope.botId.trim())
}

function storageOrNull(): Storage | null {
  try { return typeof window === 'undefined' ? null : window.localStorage } catch { return null }
}

export function receiptStorageKey(scope: ReceiptScope): string {
  return `${storagePrefix}${encodeURIComponent(scope.apiBaseUrl.replace(/\/+$/, ''))}:${encodeURIComponent(scope.principalId)}:${encodeURIComponent(scope.botId)}`
}

function parseStore(value: string | null): Store | null {
  if (!value) return { version: 1, receipts: [] }
  try {
    const parsed: unknown = JSON.parse(value)
    if (!parsed || typeof parsed !== 'object' || !('version' in parsed) || parsed.version !== 1 || !('receipts' in parsed) || !Array.isArray(parsed.receipts)) return null
    const receipts = parsed.receipts.filter((item): item is StoredReceipt => Boolean(item && typeof item === 'object' &&
      'kind' in item && typeof item.kind === 'string' && kinds.has(item.kind as ReceiptKind) &&
      'id' in item && typeof item.id === 'string' && item.id.length > 0 && item.id.length <= 512 &&
      'acceptedAt' in item && typeof item.acceptedAt === 'number' && Number.isFinite(item.acceptedAt)))
      .map(({ kind, id, acceptedAt }) => ({ kind, id, acceptedAt }))
    return { version: 1, receipts }
  } catch { return null }
}

function cleaned(receipts: StoredReceipt[], now: number): StoredReceipt[] {
  return receipts.filter((receipt) => now - receipt.acceptedAt < ttlMs && receipt.acceptedAt <= now + 5 * 60 * 1000)
    .sort((a, b) => b.acceptedAt - a.acceptedAt).slice(0, maxReceipts)
}

export function readTrackedReceipts(scope: ReceiptScope | undefined, storage = storageOrNull(), now = Date.now()): ReceiptReference[] {
  if (!usableScope(scope) || !storage) return []
  const key = receiptStorageKey(scope)
  try {
    const value = storage.getItem(key)
    if (value === null) return []
    const store = parseStore(value)
    if (!store) { storage.removeItem(key); return [] }
    const receipts = cleaned(store.receipts, now)
    const serialized = JSON.stringify({ version: 1, receipts })
    if (serialized !== value) storage.setItem(key, serialized)
    return receipts.map(({ kind, id }) => ({ kind, id }))
  } catch { return [] }
}

export function rememberAcceptedReceipt(scope: ReceiptScope | undefined, reference: ReceiptReference, storage = storageOrNull(), now = Date.now()): boolean {
  if (!usableScope(scope) || !storage || !kinds.has(reference.kind) || !reference.id.trim() || reference.id.length > 512) return false
  const key = receiptStorageKey(scope)
  try {
    const prior = parseStore(storage.getItem(key))
    const receipts = cleaned(prior?.receipts ?? [], now)
      .filter(({ kind, id }) => kind !== reference.kind || id !== reference.id)
    receipts.unshift({ kind: reference.kind, id: reference.id, acceptedAt: now })
    storage.setItem(key, JSON.stringify({ version: 1, receipts: receipts.slice(0, maxReceipts) } satisfies Store))
    if (typeof window !== 'undefined') window.dispatchEvent(new CustomEvent('life-mm-receipts-changed', { detail: key }))
    return true
  } catch { return false }
}

export function findReceiptAcceptedAt(id: string, storage = storageOrNull()): number | undefined {
  if (!storage) return undefined
  try {
    for (let i = 0; i < storage.length; i++) {
      const key = storage.key(i)
      if (key && key.startsWith(storagePrefix)) {
        const store = parseStore(storage.getItem(key))
        const found = store?.receipts.find((r) => r.id === id)
        if (found?.acceptedAt) return found.acceptedAt
      }
    }
  } catch { return undefined }
  return undefined
}

export function subscribeTrackedReceipts(scope: ReceiptScope | undefined, onChange: () => void): () => void {
  if (!usableScope(scope) || typeof window === 'undefined') return () => undefined
  const key = receiptStorageKey(scope)
  const custom = (event: Event) => {
    if (event instanceof CustomEvent && event.detail === key) onChange()
  }
  const storage = (event: StorageEvent) => { if (event.key === null || event.key === key) onChange() }
  window.addEventListener('life-mm-receipts-changed', custom)
  window.addEventListener('storage', storage)
  return () => {
    window.removeEventListener('life-mm-receipts-changed', custom)
    window.removeEventListener('storage', storage)
  }
}
