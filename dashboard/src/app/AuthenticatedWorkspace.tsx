import { useLocation, useNavigate } from '@tanstack/react-router'
import { useEffect } from 'react'
import { useAuth } from '../core/auth/auth-context'
import { safeRedirect } from './redirect'
import { OperationalProvider } from '../features/operational-context/OperationalProvider'
import { OperationalShell } from './OperationalShell'
import { SessionRestoring } from '../features/auth/SessionRestoring'

export function AuthenticatedWorkspace() {
  const auth = useAuth()
  const href = useLocation({ select: (location) => location.href })
  const navigate = useNavigate()
  useEffect(() => {
    if (auth.status === 'anonymous' && href.split('?')[0] !== '/login') {
      void navigate({ to: '/login', search: { redirect: safeRedirect(href) }, replace: true })
    }
  }, [auth.status, href, navigate])
  if (auth.status === 'restoring') return <SessionRestoring />
  return auth.status === 'authenticated' ? <OperationalProvider><OperationalShell /></OperationalProvider> : null
}
