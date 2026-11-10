import { KeyRound, Loader2, ShieldCheck } from 'lucide-react'
import type { AuthStatus } from '../../core/auth/auth-context'
import { BrandLogo } from '../../shared/components/BrandLogo'
import { SectionLabel } from '../../shared/components/SectionLabel'
import { Surface } from '../../shared/components/Surface'
import { AuthAlert } from './AuthAlert'
import { GoogleSignIn } from './GoogleSignIn'

export type LoginCardProps = {
  status: AuthStatus
  error: string | null
  redirect?: string
  onLogin: (credential: string) => void
}

export function LoginCard({ status, error, redirect, onLogin }: LoginCardProps) {
  return (
    <Surface className="relative w-full overflow-hidden p-5 shadow-card sm:p-10">
      <div className="brand-grid pointer-events-none absolute inset-0 opacity-40" />
      <div className="pointer-events-none absolute -right-24 -top-24 h-48 w-48 rounded-full bg-app-action/10 blur-3xl" />
      <div className="pointer-events-none absolute -bottom-24 -left-24 h-48 w-48 rounded-full bg-app-accent/10 blur-3xl" />

      <section className="relative w-full" aria-labelledby="login-title">
        <BrandLogo />
        <SectionLabel tone="brand" className="mt-8">
          LIFE · Market making
        </SectionLabel>

        <h1 id="login-title" className="mt-3 font-heavy text-3xl font-extrabold tracking-tight text-app-text sm:text-4xl">
          Sign in to dashboard
        </h1>
        <p className="mt-3 text-sm leading-6 text-app-muted sm:text-base">
          Authorized operator portal for algorithmic trading and liquidity management. Sign in with your approved Google account.
        </p>

        {redirect && redirect !== '/' && (
          <div className="mt-4 flex items-center gap-2 rounded-button border border-app-border bg-app-surface-muted px-3 py-2 text-xs text-app-muted">
            <KeyRound size={14} className="shrink-0 text-app-action" />
            <span>
              You will be redirected to <code className="font-mono font-medium text-app-text">{redirect}</code> after signing in.
            </span>
          </div>
        )}

        <div className="mt-8 flex flex-col items-start gap-4">
          <GoogleSignIn
            disabled={status === 'authenticating'}
            onCredential={onLogin}
          />

          {status === 'authenticating' && (
            <div
              role="status"
              className="flex items-center gap-2.5 rounded-button border border-app-border bg-app-surface-muted px-3.5 py-2 text-sm text-app-muted"
            >
              <Loader2 className="h-4 w-4 animate-spin text-app-action" />
              <span>Verifying your access…</span>
            </div>
          )}

          {error && (
            <AuthAlert
              title={error}
              description="Access is limited to approved operator accounts. Contact your administrator if you need access."
            />
          )}
        </div>

        <div className="mt-8 flex items-start gap-3 border-t border-app-border/80 pt-6 text-xs leading-5 text-app-muted sm:text-sm sm:leading-6">
          <ShieldCheck size={18} className="mt-0.5 shrink-0 text-app-action" />
          <p>
            Access is limited to approved accounts. When your session expires, sign in again to continue.
          </p>
        </div>
      </section>
    </Surface>
  )
}
