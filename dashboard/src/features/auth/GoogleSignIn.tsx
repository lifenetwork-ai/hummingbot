import { useEffect, useRef, useState } from 'react'
import { AlertCircle, Loader2 } from 'lucide-react'
import { getAppConfig } from '../../core/config/env'
import { Button } from '../../shared/components/Button'
import { AuthAlert } from './AuthAlert'
import { loadGoogleIdentity } from './google-script'

export type GoogleSignInProps = {
  onCredential: (credential: string) => void
  disabled: boolean
}

export function GoogleSignIn({ onCredential, disabled }: GoogleSignInProps) {
  const container = useRef<HTMLDivElement>(null)
  const callback = useRef(onCredential)
  callback.current = onCredential
  const busy = useRef(disabled)
  busy.current = disabled
  const [attempt, setAttempt] = useState(0)
  const [state, setState] = useState<'loading' | 'ready' | 'error'>('loading')
  const { googleClientId: clientId } = getAppConfig()

  useEffect(() => {
    if (!clientId) return
    let active = true
    const target = container.current
    setState('loading')
    void loadGoogleIdentity().then((identity) => {
      if (!active || !target) return
      identity.initialize({
        client_id: clientId,
        auto_select: false,
        callback: ({ credential }) => { if (active && !busy.current) callback.current(credential) },
      })
      const containerWidth = target.clientWidth || 320
      const width = Math.min(Math.max(containerWidth, 200), 320)
      identity.renderButton(target, { theme: 'outline', size: 'large', text: 'signin_with', width })
      setState('ready')
    }).catch(() => { if (active) setState('error') })
    return () => { active = false; target?.replaceChildren() }
  }, [clientId, attempt])

  if (!clientId) {
    return (
      <div
        role="status"
        className="flex items-start gap-3 rounded-card border border-app-border bg-app-surface-muted p-4 text-sm text-app-muted"
      >
        <AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-app-muted" />
        <p>Google sign-in is not configured. Please contact an administrator.</p>
      </div>
    )
  }

  return (
    <div className="flex w-full flex-col items-center gap-3">
      <div className="relative flex min-h-[44px] w-full justify-center">
        <div
          ref={container}
          className="flex justify-center"
          style={disabled ? { opacity: 0.6, pointerEvents: 'none' } : undefined}
          aria-busy={disabled}
        />
      </div>

      {state === 'loading' && (
        <div className="flex flex-col items-center gap-2">
          <div
            className="flex h-[44px] w-[280px] sm:w-[320px] animate-pulse items-center justify-center rounded-button border border-app-border bg-app-surface-muted text-xs text-app-muted"
            aria-hidden="true"
          >
            <Loader2 className="mr-2 h-4 w-4 animate-spin text-app-muted" />
            <span>Initializing Google…</span>
          </div>
          <p role="status" className="flex items-center gap-1.5 text-xs text-app-muted">
            <Loader2 className="h-3.5 w-3.5 animate-spin text-app-action" />
            <span>Loading Google sign-in…</span>
          </p>
        </div>
      )}

      {state === 'error' && (
        <AuthAlert
          title="Failed to load Google. Please check your connection and try again."
          action={
            <Button variant="secondary" onClick={() => setAttempt((value) => value + 1)}>
              Retry
            </Button>
          }
          className="w-full max-w-md"
        />
      )}
    </div>
  )
}
