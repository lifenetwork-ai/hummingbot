import { ApiProvider, type ApiSession } from '../core/api/ApiProvider'
import type { PropsWithChildren } from 'react'
import { ThemeProvider } from '../theme/useTheme'

export function AppProviders({ children, apiSession }: PropsWithChildren<{ apiSession?: ApiSession }>) {
  return (
    <ThemeProvider>
      <ApiProvider session={apiSession}>{children}</ApiProvider>
    </ThemeProvider>
  )
}
