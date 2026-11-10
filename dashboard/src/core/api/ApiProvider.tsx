import { useEffect, useRef, useState, type PropsWithChildren } from 'react'
import { QueryClientProvider } from '@tanstack/react-query'
import { createApiClient, type ApiClientOptions } from './client'
import { createAdminApi } from './admin'
import { createDashboardQueryClient } from './queryClient'
import { ApiContext } from './ApiContext'

export type ApiSession = ApiClientOptions & {
  /** Public, non-secret ID; change on login/logout, principal or privilege changes. */
  cacheIdentity: string
}

function SessionProvider({ session, children }: PropsWithChildren<{ session?: ApiSession }>) {
  const latestSession = useRef(session)
  latestSession.current = session
  const [queryClient] = useState(createDashboardQueryClient)
  const [api] = useState(() => session ? createAdminApi(createApiClient({ ...session, getToken: () => latestSession.current?.getToken?.() })) : null)
  useEffect(() => () => {
    void queryClient.cancelQueries()
    queryClient.clear()
  }, [queryClient])
  return <QueryClientProvider client={queryClient}>
    <ApiContext.Provider value={api}>{children}</ApiContext.Provider>
  </QueryClientProvider>
}

/** A fresh cache and API instance per connection/auth session. No requests without consumers. */
export function ApiProvider({ session, children }: PropsWithChildren<{ session?: ApiSession }>) {
  const key = session ? JSON.stringify([session.baseUrl, session.cacheIdentity]) : 'disconnected'
  return <SessionProvider key={key} session={session}>{children}</SessionProvider>
}
