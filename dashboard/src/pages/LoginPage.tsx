import { useEffect } from 'react'
import { useNavigate, useSearch } from '@tanstack/react-router'
import { useAuth } from '../core/auth/auth-context'
import { LoginCard } from '../features/auth/LoginCard'
import { MotionPage } from '../shared/motion/MotionPage'
import { SessionRestoring } from '../features/auth/SessionRestoring'

export function LoginPage() {
  const auth = useAuth()
  const { redirect } = useSearch({ from: '/login' })
  const navigate = useNavigate()

  useEffect(() => {
    if (auth.status === 'authenticated') {
      void navigate({ to: redirect, replace: true })
    }
  }, [auth.status, navigate, redirect])

  if (auth.status === 'authenticated') return null
  if (auth.status === 'restoring') return <SessionRestoring />

  return (
    <MotionPage>
      <div className="mx-auto flex w-full flex-1 max-w-lg items-center justify-center px-4 py-4 sm:px-6">
        <LoginCard
          status={auth.status}
          error={auth.error}
          redirect={redirect}
          onLogin={(credential) => {
            void auth.login(credential)
          }}
        />
      </div>
    </MotionPage>
  )
}
