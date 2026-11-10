import { useEffect } from 'react'
import { useNavigate, useSearch } from '@tanstack/react-router'
import { useAuth } from '../core/auth/auth-context'
import { LoginCard } from '../features/auth/LoginCard'
import { MotionPage } from '../shared/motion/MotionPage'

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

  return (
    <MotionPage>
      <div className="mx-auto flex min-h-[calc(100vh-76px-88px)] max-w-xl items-center justify-center px-3 py-10 sm:px-6 sm:py-16">
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
