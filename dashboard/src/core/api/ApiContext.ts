import { createContext, useContext } from 'react'
import type { AdminApi } from './admin'

export const ApiContext = createContext<AdminApi | null>(null)
export function useAdminApi() {
  const api = useContext(ApiContext)
  if (!api) throw new Error('Admin API requires ApiProvider with an authenticated API session')
  return api
}
