import { describe, expect, it } from 'vitest'
import { clearPendingOperation, readPendingOperation, savePendingOperation } from './operation-journal'
import type { ReceiptScope } from './receipt-tracking'
import type { WriteInput } from './admin'

const scope: ReceiptScope = { apiBaseUrl: 'https://api.test/admin/', principalId: 'user-1', botId: 'bot-1' }
const stop: WriteInput<'submitCommand'> = { body: { action: 'stop', expected_version: 2, session_id: 's1' }, idempotencyKey: 'stop-key' }
const start: WriteInput<'submitCommand'> = { body: { action: 'start', expected_version: 2, expected_catalogue_hash: 'hash',
  market_mode: 'both', selection: 'random', profile_id: null, duration_mode: 'until_stopped', duration_seconds: null }, idempotencyKey: 'start-key' }
const reconcile: WriteInput<'requestReconciliation'> = { body: { market: 'all', pair: null, lookback_seconds: 3600, max_orders: 100, max_pages: 5 }, idempotencyKey: 'check-key' }
function memoryStorage(): Storage {
  const values = new Map<string, string>()
  return { get length() { return values.size }, clear: () => values.clear(), getItem: (key) => values.get(key) ?? null,
    key: (index) => [...values.keys()][index] ?? null, removeItem: (key) => { values.delete(key) }, setItem: (key, value) => { values.set(key, value) } }
}
describe('pending operation journal', () => {
  it('restores exact scoped requests without replay and isolates concurrent channels', () => {
    const storage = memoryStorage()
    expect(savePendingOperation(scope, 'submitCommand', start, storage, 1000)).toBe(true)
    expect(savePendingOperation(scope, 'submitCommand', stop, storage, 1000)).toBe(true)
    expect(savePendingOperation(scope, 'requestReconciliation', reconcile, storage, 1000)).toBe(true)
    expect(readPendingOperation(scope, 'session', storage, 1100)).toEqual({ operation: 'submitCommand', input: start })
    expect(readPendingOperation(scope, 'stop', storage, 1100)?.input).toEqual(stop)
    expect(readPendingOperation(scope, 'reconciliation', storage, 1100)?.input).toEqual(reconcile)
    for (const changed of [{ ...scope, botId: 'other' }, { ...scope, principalId: 'other' }, { ...scope, apiBaseUrl: 'https://other.test' }]) {
      expect(readPendingOperation(changed, 'session', storage, 1100)).toBeUndefined()
    }
    expect(readPendingOperation({ ...scope, apiBaseUrl: scope.apiBaseUrl.replace(/\/$/, '') }, 'session', storage, 1100)?.input).toEqual(start)
  })
  it('preserves idempotency and rejects a new decision until the old one is explicitly cleared', () => {
    const storage = memoryStorage()
    const now = Date.now()
    savePendingOperation(scope, 'submitCommand', start, storage, now)
    expect(savePendingOperation(scope, 'submitCommand', start, storage, now)).toBe(true)
    expect(savePendingOperation(scope, 'submitCommand', { ...start, idempotencyKey: 'new-key' }, storage, now)).toBe(false)
    clearPendingOperation(scope, 'session', 'wrong-key', storage)
    expect(readPendingOperation(scope, 'session', storage)?.input).toEqual(start)
    clearPendingOperation(scope, 'session', start.idempotencyKey, storage)
    expect(readPendingOperation(scope, 'session', storage)).toBeUndefined()
  })
  it('expires malformed, future and old entries without affecting another channel', () => {
    const storage = memoryStorage()
    savePendingOperation(scope, 'submitCommand', start, storage, 1000)
    expect(readPendingOperation(scope, 'session', storage, 1000 + 86_400_000)).toBeUndefined()
    savePendingOperation(scope, 'submitCommand', stop, storage, 1000)
    expect(readPendingOperation(scope, 'stop', storage, -400_000)).toBeUndefined()
    savePendingOperation(scope, 'submitCommand', stop, storage, 1000)
    const key = storage.key(0)!
    storage.setItem(key, '{bad-json')
    expect(readPendingOperation(scope, 'stop', storage, 1100)).toBeUndefined()
    storage.setItem(key, JSON.stringify({ version: 1, createdAt: 1000, operation: 'submitCommand', input: { ...stop, body: { ...stop.body, secret: 'never' } } }))
    expect(readPendingOperation(scope, 'stop', storage, 1100)).toBeUndefined()
  })
  it('rejects secrets and extra fields before persisting', () => {
    const storage = memoryStorage()
    expect(savePendingOperation(scope, 'submitCommand', Object.assign({}, start, { body: Object.assign({}, start.body, { secret: 'never' }) }), storage)).toBe(false)
    expect(savePendingOperation(scope, 'submitCommand', Object.assign({}, stop, { token: 'never' }), storage)).toBe(false)
    expect(savePendingOperation(scope, 'submitCommand', Object.assign({}, stop, { body: Object.assign({}, stop.body, { action: 'configure' }) }), storage)).toBe(false)
    expect(savePendingOperation(undefined, 'submitCommand', start, storage)).toBe(false)
    expect(savePendingOperation({ ...scope, botId: '' }, 'submitCommand', start, storage)).toBe(false)
    expect(savePendingOperation(scope, 'submitCommand', start, storage, NaN)).toBe(false)
    expect(storage.length).toBe(0)
  })
  it('handles unavailable or blocked storage without submission or secrets in errors', () => {
    const storage = memoryStorage()
    storage.getItem = () => { throw new Error('blocked') }
    storage.setItem = () => { throw new Error('blocked') }
    storage.removeItem = () => { throw new Error('blocked') }
    expect(savePendingOperation(scope, 'submitCommand', start, storage)).toBe(false)
    expect(readPendingOperation(scope, 'session', storage)).toBeUndefined()
    expect(() => clearPendingOperation(scope, 'session', undefined, storage)).not.toThrow()
    expect(readPendingOperation(scope, 'session', null)).toBeUndefined()
  })
})
