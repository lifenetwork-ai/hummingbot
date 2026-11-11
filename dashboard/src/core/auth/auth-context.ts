import { createContext, useContext } from 'react'
import type { ApiSession } from '../api/ApiProvider'
import type { ResponseBody } from '../api/types'

export type AuthStatus = 'anonymous' | 'restoring' | 'authenticating' | 'authenticated'
export type AuthContextValue = {
  status: AuthStatus
  identity: ResponseBody<'getAdminIdentity'> | null
  error: string | null
  login: (credential: string) => Promise<void>
  logout: () => void
  apiSession: ApiSession | undefined
}

export const AuthContext = createContext<AuthContextValue | null>(null)

export function useAuth(): AuthContextValue {
  const auth = useContext(AuthContext)
  if (!auth) throw new Error('useAuth must be used within AuthProvider')
  return auth
}
