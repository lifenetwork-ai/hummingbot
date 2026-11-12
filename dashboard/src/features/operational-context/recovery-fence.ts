import type { ReceiptReference } from '../../core/api/receipt-tracking'
import type { ReadResponse } from '../../core/api/admin'

type Source = { phase: 'loading' | 'ready' | 'error'; data?: ReadResponse<'getRecoveryOperation'> }
/** A scoped receipt fence is separate from backend runtime/risk availability. Stop is never fenced here. */
export function deriveRecoveryFence(reference: ReceiptReference | undefined, source: Source, now = Date.now()) {
  const previewExpiry = Date.parse(source.data?.expires_at ?? '')
  const reviewActive = source.data?.action === 'recovery_check' && source.data.status === 'succeeded' && source.data.stage === 'review_required' && source.data.can_recover && (!Number.isFinite(previewExpiry) || previewExpiry > now)
  const terminal = source.phase === 'ready' && (source.data?.status === 'failed' || source.data?.status === 'succeeded' && !reviewActive)
  const active = Boolean(reference && !terminal)
  return { active, reference: reference ?? null,
    phase: !active ? 'inactive' as const : source.phase === 'ready' ? 'pending' as const : 'unknown' as const,
    detail: active ? 'Recovery is running or receipt outcome is not yet verified. Track exact receipt before Start/Switch or changing connection.' : '',
  }
}
export type RecoveryFenceModel = ReturnType<typeof deriveRecoveryFence>
