import type { components } from './schema'
import type { ReadResponse } from './admin'
import type { ReceiptReference } from './receipt-tracking'

export type ReceiptOutcome =
  | {
      kind: 'command'
      action: components['schemas']['CommandResponseOutput']['action']
      sessionId: string | null
      error: string | null
    }
  | {
      kind: 'credential'
      action: components['schemas']['CredentialReceiptOutput']['action']
      stage: string
      operatorActionRequired: boolean
      reasonCode: string | null
      error: string | null
    }
  | {
      kind: 'reconciliation'
      state: components['schemas']['ReconciliationReportOutput']['state']
      reasons: string[]
    }
  | {
      kind: 'recovery'
      action: components['schemas']['RecoveryOperationOutput']['action']
      stage: string
      canRecover: boolean
      blockers: Array<Pick<components['schemas']['RecoveryBlockerOutput'], 'code' | 'detail' | 'next_action'>>
    }

export function projectReceiptOutcome(reference: ReceiptReference, data: unknown): ReceiptOutcome | undefined {
  switch (reference.kind) {
    case 'command': {
      const receipt = data as ReadResponse<'getCommand'>
      return { kind: 'command', action: receipt.action, sessionId: receipt.session_id, error: receipt.error }
    }
    case 'credential': {
      const receipt = data as ReadResponse<'getCredentialOperation'>
      return {
        kind: 'credential', action: receipt.action, stage: receipt.result.blocked_stage ?? receipt.result.stage,
        operatorActionRequired: receipt.result.operator_action_required, reasonCode: receipt.result.reason_code,
        error: receipt.error,
      }
    }
    case 'reconciliation': {
      const receipt = data as ReadResponse<'getReconciliationById'>
      return {
        kind: 'reconciliation', state: receipt.report.state,
        reasons: [...new Set(receipt.report.markets?.flatMap((market) => market.reasons) ?? [])],
      }
    }
    case 'recovery': {
      const receipt = data as ReadResponse<'getRecoveryOperation'>
      return {
        kind: 'recovery', action: receipt.action, stage: receipt.stage, canRecover: receipt.can_recover,
        blockers: (receipt.blockers ?? []).map(({ code, detail, next_action }) => ({ code, detail, next_action })),
      }
    }
  }
}
