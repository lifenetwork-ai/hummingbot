import type { ResponseBody } from '../../core/api/types'
import type { CredentialSetOutcome, deriveCredentialSlot } from './connections'
import type { DecisionLock } from './operation-state-context'

export type CredentialCatalogue = ResponseBody<'listCredentialInputs'>
export type CredentialSlot = ReturnType<typeof deriveCredentialSlot> & { setOutcome: CredentialSetOutcome | null }
export type CredentialLock = DecisionLock
