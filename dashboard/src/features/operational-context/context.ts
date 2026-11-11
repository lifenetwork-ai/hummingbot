import type { ResponseBody } from '../../core/api/types'

export type ContextSource<T> = {
  phase: 'loading' | 'ready' | 'error'
  data?: T
  error: Error | null
  refreshing: boolean
}
export type SessionAction = 'start' | 'switch' | 'stop'
export type ActionDecision = { allowed: boolean; reasonCode: string | null; detail: string }
/** Client presentation state; raw backend observation remains available separately. */
export type ObservationState = 'fresh' | 'stale' | 'unknown' | 'session_mismatch'
export type OperationalContextInput = {
  identity: ResponseBody<'getAdminIdentity'> | null
  status: ContextSource<ResponseBody<'getRuntimeStatus'>>
  balances: ContextSource<ResponseBody<'getBalances'>>
}

function knownEnvironment(value: string | undefined): value is 'live' | 'demo' {
  return value === 'live' || value === 'demo'
}
function mismatched(left: string | undefined, right: string | undefined): boolean {
  return knownEnvironment(left) && knownEnvironment(right) && left !== right
}

function displayObservationState(source: OperationalContextInput['status']): ObservationState {
  const observation = source.data?.observation
  if (!observation?.observed_at) return 'unknown'
  if (source.phase === 'error') return 'stale'
  if (source.phase !== 'ready' || !source.data?.owner_available) return 'unknown'
  if (!observation.session_matches) return 'session_mismatch'
  return observation.stale ? 'stale' : 'fresh'
}

/** Presentation mapping only; backend remains authoritative for every POST. */
export function deriveOperationalContext({ identity, status, balances }: OperationalContextInput) {
  const data = status.data
  const processEnvironment = data?.process_identity?.trading_environment ?? 'unknown'
  const ownerEnvironment = data?.owner_identity?.trading_environment ?? 'unknown'
  const currentOwner = status.phase === 'ready' && data?.owner_available === true
  const accounts = (balances.data?.accounts ?? []).map((account) => {
    const verified = account.identity_verified && Boolean(account.account_id?.trim())
    const fresh = balances.phase === 'ready' && balances.data?.owner_available === true && account.balances.length > 0 &&
      account.balances.every((balance) => !balance.stale && balance.present_in_latest_snapshot)
    return {
      data: account,
      verification: verified ? 'verified' as const : 'unverified' as const,
      observation: !account.balances.length ? 'unknown' as const : fresh ? 'fresh' as const : 'stale' as const,
      environmentMismatch: currentOwner && verified && fresh &&
        (mismatched(processEnvironment, account.environment) || mismatched(ownerEnvironment, account.environment)),
    }
  })

  function decision(action: SessionAction): ActionDecision {
    if (!identity?.permissions.operate) return { allowed: false, reasonCode: 'permission_required', detail: 'This action requires the operate permission.' }
    if (status.phase !== 'ready' || !data) return { allowed: false, reasonCode: 'status_unavailable', detail: 'Refresh the bot status before reviewing this action.' }
    const availability = data.actions?.[action]
    if (!availability) return { allowed: false, reasonCode: 'availability_unknown', detail: 'The backend has not provided availability for this action.' }
    return { allowed: availability.allowed, reasonCode: availability.reason_code, detail: availability.detail }
  }

  return {
    identity, status, balances,
    botId: data?.bot_id ?? null,
    controlVersion: data?.control_version ?? null,
    processEnvironment, ownerEnvironment,
    environmentMismatch: (currentOwner && mismatched(processEnvironment, ownerEnvironment)) || accounts.some((account) => account.environmentMismatch),
    accounts,
    markets: data?.markets ?? [],
    observation: data?.observation ?? null,
    observationState: displayObservationState(status),
    actions: { start: decision('start'), switch: decision('switch'), stop: decision('stop') },
  }
}

export type OperationalContextModel = ReturnType<typeof deriveOperationalContext>
