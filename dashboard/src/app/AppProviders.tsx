import { ApiProvider, type ApiSession } from '../core/api/ApiProvider'
import type { PropsWithChildren } from 'react'
import { AuthProvider } from '../core/auth/AuthProvider'
import { useAuth } from '../core/auth/auth-context'
import { ToastHost } from '../shared/components/ToastHost'
import { ThemeProvider } from '../theme/useTheme'

function AuthenticatedApi({ children, apiSession }: PropsWithChildren<{ apiSession?: ApiSession }>) {
  const auth = useAuth()
  return <ApiProvider session={apiSession ?? auth.apiSession}>{children}</ApiProvider>
}
export function AppProviders({ children, apiSession }: PropsWithChildren<{ apiSession?: ApiSession }>) {
  return (
    <ThemeProvider>
      <AuthProvider>
        <AuthenticatedApi apiSession={apiSession}>
          {children}
          <ToastHost />
        </AuthenticatedApi>
      </AuthProvider>
    </ThemeProvider>
  )
}
