import { Outlet, useLocation, useNavigate } from '@tanstack/react-router'
import { useEffect } from 'react'
import { useAuth } from '../core/auth/auth-context'
import { safeRedirect } from './redirect'

export function ProtectedRoute() {
  const auth = useAuth()
  const href = useLocation({ select: (location) => location.href })
  const navigate = useNavigate()
  useEffect(() => {
    if (auth.status !== 'authenticated' && href.split('?')[0] !== '/login') {
      void navigate({ to: '/login', search: { redirect: safeRedirect(href) }, replace: true })
    }
  }, [auth.status, href, navigate])
  return auth.status === 'authenticated' ? <Outlet /> : null
}
