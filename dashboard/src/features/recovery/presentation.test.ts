import { describe, expect, it } from 'vitest'
import type { ReadResponse } from '../../core/api/admin'
import type { RecoveryOperation } from './recovery'
import { deriveRecoveryPresentation, deriveRecoveryVerification } from './presentation'

const status = (overrides: Partial<ReadResponse<'getRuntimeStatus'>> = {}) => ({
  owner_available: true,
  observation: { observed_at: '2026-11-16T07:00:00Z', stale: false, session_matches: true },
  recovery: { halted: false, next_action: 'none' },
  automatic_recovery: null,
  ...overrides,
}) as ReadResponse<'getRuntimeStatus'>

const operation = (overrides: Partial<RecoveryOperation> = {}) => ({
  action: 'recovery_check', id: 'check-1', status: 'succeeded', stage: 'review_required',
  can_recover: true, confirmation_token: null, created_at: '', updated_at: '', detail: '', label: '',
  trading_state: 'stopped', verification: {}, proposed_changes: [],
  ...overrides,
}) as RecoveryOperation

describe('deriveRecoveryPresentation', () => {
  it('keeps unavailable or incompletely observed status unknown and hides the no recovery message', () => {
    const view = deriveRecoveryPresentation(undefined, false, 1_000)
    expect(view.observation).toBe('unknown')
    expect(view.showNoRecoveryNeeded).toBe(false)
    expect(view.retryAt).toBeNull()
    expect(deriveRecoveryPresentation(status({ observation: null }), true).observation).toBe('unknown')
    expect(deriveRecoveryPresentation(status({ recovery: null }), true).showNoRecoveryNeeded).toBe(false)
  })

  it('treats automatic stale data as stale, even when the containing status is fresh', () => {
    const view = deriveRecoveryPresentation(status({ automatic_recovery: {
      phase: 'reconciling', stale: true, next_retry_at: 2_000,
    } as never }), true, 1_000)
    expect(view.observation).toBe('stale')
    expect(view.active).toBe(false)
    expect(view.retryAt).toBeNull()
  })

  it('normalizes backend epoch seconds to milliseconds for a normal Date.now clock', () => {
    const automatic = {
      phase: 'reconciling', stale: false, next_retry_at: 1_800_000_002, started_at: 1_800_000_000,
      last_progress_at: 1_800_000_001, isolated: true,
      admission_mode: 'funded_spot_sell', reserved_base: { LIFE: '1.25' },
      reserved_quote_worst_case: '12.50', operator_required: true,
    }
    const view = deriveRecoveryPresentation(status({ automatic_recovery: automatic as never }), true, 1_800_000_000_500)
    expect(view).toMatchObject({ observation: 'fresh', phase: 'reconciling', active: true,
      retryAt: 1_800_000_002_000, startedAt: 1_800_000_000_000, lastProgressAt: 1_800_000_001_000,
      isolated: true, admissionMode: 'funded_spot_sell', reservedBase: { LIFE: '1.25' },
      reservedQuoteWorstCase: '12.50', operatorRequired: true })
  })

  it('requires independent owner and session observation evidence', () => {
    const active = { phase: 'reconciling', stale: false, next_retry_at: 1_800_000_002 } as never
    expect(deriveRecoveryPresentation(status({ owner_available: false, automatic_recovery: active }), true).observation).toBe('stale')
    expect(deriveRecoveryPresentation(status({ observation: { observed_at: null, stale: false, session_matches: true }, automatic_recovery: active } as never), true).observation).toBe('unknown')
    expect(deriveRecoveryPresentation(status({ observation: { observed_at: '2026-11-16T07:00:00Z', stale: false, session_matches: false }, automatic_recovery: active } as never), true).observation).toBe('stale')
  })

  it('renders wait shutdown separately and only shows no recovery for fresh idle state', () => {
    const waiting = deriveRecoveryPresentation(status({ recovery: { halted: true, next_action: 'wait' } as never }), true)
    expect(waiting.waitingForShutdown).toBe(true)
    expect(waiting.showManualHalt).toBe(false)
    expect(waiting.showNoRecoveryNeeded).toBe(false)
    expect(deriveRecoveryPresentation(status(), true).showNoRecoveryNeeded).toBe(true)
    expect(deriveRecoveryPresentation(status({ automatic_recovery: { phase: 'settled_stopped', stale: false } as never }), true).showNoRecoveryNeeded).toBe(false)
  })

  it('does not treat manual_required as active work or show its retry countdown', () => {
    const view = deriveRecoveryPresentation(status({ automatic_recovery: {
      phase: 'manual_required', stale: false, next_retry_at: 1_800_000_002,
    } as never }), true, 1_800_000_000_000)
    expect(view.active).toBe(false)
    expect(view.retryAt).toBeNull()
  })
})

describe('deriveRecoveryVerification', () => {
  it('identifies verified checkpoint proof and does not invent proposed changes', () => {
    const view = deriveRecoveryVerification(operation({
      verification: { scope: 'verified_checkpoint', orders_checked: 0, fills_checked: 17 },
      proposed_changes: [],
    }))
    expect(view).toMatchObject({ scope: 'verified_checkpoint', checkpointPreserved: true,
      ordersChecked: 0, fillsChecked: 17, proposedChanges: [] })
    expect(view.ordersCheckedMeaning).toBe('no_historical_per_order_reads')
  })

  it('keeps other and absent verification scopes distinguishable', () => {
    expect(deriveRecoveryVerification(undefined).scope).toBeNull()
    expect(deriveRecoveryVerification(operation({ verification: { scope: 'bounded_complete_local_history_and_dedicated_account' } })).checkpointPreserved).toBe(false)
  })
})
