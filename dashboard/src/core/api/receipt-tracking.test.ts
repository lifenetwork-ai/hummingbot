import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  readTrackedReceipts,
  receiptStorageKey,
  rememberAcceptedReceipt,
  acceptedReceiptReference,
  type ReceiptScope,
} from './receipt-tracking'
import type { ResponseBody } from './types'

const scope: ReceiptScope = { apiBaseUrl: 'https://api.example.test/v1/admin', principalId: 'google:subject-7', botId: 'life-mm-demo' }
const isolatedStorage = () => {
  const data = new Map<string, string>()
  return {
    getItem: (key: string) => data.get(key) ?? null,
    setItem: (key: string, value: string) => { data.set(key, value) },
    removeItem: (key: string) => { data.delete(key) },
  } as unknown as Storage
}

describe('accepted receipt tracking', () => {
  afterEach(() => vi.restoreAllMocks())

  it('scopes references by API, stable principal and trusted bot', () => {
    const storage = isolatedStorage()
    rememberAcceptedReceipt(scope, { kind: 'command', id: 'cmd-1' }, storage, 1_000)
    expect(readTrackedReceipts(scope, storage, 1_000)).toEqual([{ kind: 'command', id: 'cmd-1' }])
    expect(readTrackedReceipts({ ...scope, principalId: 'google:subject-8' }, storage, 1_000)).toEqual([])
    expect(readTrackedReceipts({ ...scope, botId: 'other-bot' }, storage, 1_000)).toEqual([])
    expect(readTrackedReceipts({ ...scope, apiBaseUrl: 'https://other.test/v1/admin' }, storage, 1_000)).toEqual([])
  })

  it('deduplicates a receipt across repeated acceptance and reload reads', () => {
    const storage = isolatedStorage()
    rememberAcceptedReceipt(scope, { kind: 'credential', id: 'receipt-1' }, storage, 1_000)
    rememberAcceptedReceipt(scope, { kind: 'credential', id: 'receipt-1' }, storage, 2_000)
    expect(readTrackedReceipts(scope, storage, 2_000)).toEqual([{ kind: 'credential', id: 'receipt-1' }])
  })

  it('expires old references and bounds the retained list', () => {
    const storage = isolatedStorage()
    rememberAcceptedReceipt(scope, { kind: 'command', id: 'old' }, storage, 1_000)
    expect(readTrackedReceipts(scope, storage, 1_000 + 8 * 24 * 60 * 60 * 1000)).toEqual([])
    for (let index = 0; index < 60; index++) rememberAcceptedReceipt(scope, { kind: 'command', id: `cmd-${index}` }, storage, 10_000 + index)
    const refs = readTrackedReceipts(scope, storage, 20_000)
    expect(refs).toHaveLength(50)
    expect(refs[0]).toEqual({ kind: 'command', id: 'cmd-59' })
  })

  it('ignores untrusted scope and malformed persisted data', () => {
    const storage = isolatedStorage()
    expect(rememberAcceptedReceipt({ ...scope, botId: '' }, { kind: 'command', id: 'cmd-1' }, storage, 1_000)).toBe(false)
    storage.setItem(receiptStorageKey(scope), '{broken')
    expect(readTrackedReceipts(scope, storage, 1_000)).toEqual([])
    expect(storage.getItem(receiptStorageKey(scope))).toBeNull()
  })

  it('treats unavailable browser storage as an empty, non-fatal tracker', () => {
    const storage = { getItem: () => { throw new Error('blocked') }, setItem: () => { throw new Error('blocked') }, removeItem: () => { throw new Error('blocked') } } as unknown as Storage
    expect(rememberAcceptedReceipt(scope, { kind: 'recovery', id: 'rec-1' }, storage, 1_000)).toBe(false)
    expect(readTrackedReceipts(scope, storage, 1_000)).toEqual([])
  })

  it.each([
    ['submitCommand', { id: 'cmd-1', status: 'accepted' }, { kind: 'command', id: 'cmd-1' }],
    ['setExchangeCredentials', { id: 'cred-1', status: 'accepted' }, { kind: 'credential', id: 'cred-1' }],
    ['requestReconciliation', {
      created_at: '2026-11-10T00:00:00Z', error: null, id: 'check-1',
      payload: { lookback_seconds: 3600, market: 'all', max_orders: 100, max_pages: 5, pair: null },
      report: { finished_at: null, scope: 'bot_owned_orders_and_configured_perpetual_exposure', started_at: null, state: 'not_checked' },
      status: 'accepted', updated_at: '2026-11-10T00:00:00Z',
    } satisfies ResponseBody<'requestReconciliation', 202>, { kind: 'reconciliation', id: 'check-1' }],
    ['checkRecovery', { id: 'recovery-1', status: 'accepted' }, { kind: 'recovery', id: 'recovery-1' }],
  ] as const)('maps an accepted %s response to its read-only receipt ref', (operation, response, expected) => {
    expect(acceptedReceiptReference(operation, response)).toEqual(expected)
  })

  it.each([
    ['submitCommand', { id: 'cmd-1', status: 'unexpected' }],
    ['submitCommand', { id: '', status: 'accepted' }],
    ['adminLogin', { id: 'not-a-receipt', status: 'accepted' }],
    ['requestReconciliation', { check: null }],
  ] as const)('ignores non-accepted, malformed or non-operation response for %s', (operation, response) => {
    expect(acceptedReceiptReference(operation, response)).toBeUndefined()
  })

  it.each(['accepted', 'applying', 'succeeded', 'failed'] as const)(
    'retains a valid idempotent retry receipt already in %s state', (status) => {
      expect(acceptedReceiptReference('retryCredentialCleanup', { id: 'same-operation', status }))
        .toEqual({ kind: 'credential', id: 'same-operation' })
    },
  )

  it('can be reloaded with all receipt families while keeping only their references and retention time', () => {
    const storage = isolatedStorage()
    const refs = [
      { kind: 'command', id: 'cmd-1' },
      { kind: 'credential', id: 'cred-1' },
      { kind: 'reconciliation', id: 'check-1' },
      { kind: 'recovery', id: 'recovery-1' },
    ] as const
    refs.forEach((ref, index) => rememberAcceptedReceipt(scope, ref, storage, 1_000 + index))
    expect(readTrackedReceipts(scope, storage, 1_010)).toEqual([...refs].reverse())
    const serialized = storage.getItem(receiptStorageKey(scope)) ?? ''
    expect(serialized).not.toContain('body')
    expect(serialized).not.toContain('token')
    expect(serialized).not.toContain('secret')
    expect(JSON.parse(serialized).receipts[0]).toEqual({ ...refs[3], acceptedAt: 1_003 })
  })

  it('strips unexpected fields from valid-looking storage records', () => {
    const storage = isolatedStorage()
    storage.setItem(receiptStorageKey(scope), JSON.stringify({ version: 1, receipts: [
      { kind: 'command', id: 'cmd-1', acceptedAt: 1_000, body: { secret: 'must not persist' } },
    ] }))
    expect(readTrackedReceipts(scope, storage, 1_000)).toEqual([{ kind: 'command', id: 'cmd-1' }])
    expect(storage.getItem(receiptStorageKey(scope))).toBe(JSON.stringify({ version: 1, receipts: [
      { kind: 'command', id: 'cmd-1', acceptedAt: 1_000 },
    ] }))
  })
})
