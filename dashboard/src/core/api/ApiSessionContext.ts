import { createContext, useContext } from 'react'

export type ApiSessionScope = Readonly<{ apiBaseUrl: string; principalId: string | undefined }>
export const ApiSessionContext = createContext<ApiSessionScope | undefined>(undefined)

export function useApiSessionScope(): ApiSessionScope | undefined {
  return useContext(ApiSessionContext)
}
