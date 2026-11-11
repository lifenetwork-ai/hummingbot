import type { QueryClient } from '@tanstack/react-query'
import { adminKeys } from './queries'
import type { ReadResponse } from './admin'

/** Bot identity is a routing scope: accept only a successful, non-invalidated status observation. */
export function trustedBotId(client: QueryClient): string | undefined {
  const key = adminKeys.query('getRuntimeStatus', {})
  const query = client.getQueryState<ReadResponse<'getRuntimeStatus'>>(key)
  const data = client.getQueryData<ReadResponse<'getRuntimeStatus'>>(key)
  return query?.status === 'success' && !query.isInvalidated && !query.error && data?.bot_id?.trim()
    ? data.bot_id
    : undefined
}
