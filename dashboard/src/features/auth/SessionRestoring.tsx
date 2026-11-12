import { motion, useReducedMotion } from 'framer-motion'
import { Loader2, ShieldCheck } from 'lucide-react'
import { BrandLogo } from '../../shared/components/BrandLogo'
import { SectionLabel } from '../../shared/components/SectionLabel'
import { Surface } from '../../shared/components/Surface'
import { MotionPage } from '../../shared/motion/MotionPage'

/**
 * Restoring view shown while inspecting stored credentials and verifying access
 * before rendering the login form or authenticated operational workspace.
 */
export function SessionRestoring() {
  const reducedMotion = useReducedMotion()

  return (
    <MotionPage>
      <div className="mx-auto flex w-full flex-1 max-w-lg items-center justify-center px-4 py-4 sm:px-6">
        <Surface className="relative w-full overflow-hidden p-6 shadow-card sm:p-8">
          <div className="brand-grid pointer-events-none absolute inset-0 opacity-30" />
          <div className="pointer-events-none absolute -right-20 -top-20 h-44 w-44 rounded-full bg-app-action/10 blur-3xl" />
          <div className="pointer-events-none absolute -bottom-20 -left-20 h-44 w-44 rounded-full bg-app-accent/10 blur-3xl" />

          <section
            role="status"
            aria-live="polite"
            className="relative flex flex-col items-center text-center"
          >
            <BrandLogo />
            <SectionLabel tone="brand" className="mt-6">
              LIFE · Market making
            </SectionLabel>

            <div className="mt-8 flex h-14 w-14 items-center justify-center rounded-pill border border-app-border bg-app-surface-muted shadow-sm">
              <Loader2 className="h-6 w-6 animate-spin text-app-action" aria-hidden="true" />
            </div>

            <h1 className="mt-5 font-heavy text-2xl font-extrabold tracking-tight text-app-text sm:text-3xl">
              Restoring session…
            </h1>
            <p className="mt-2.5 max-w-sm text-sm leading-6 text-app-muted">
              Verifying saved credentials and checking workspace authorization.
            </p>

            <div
              className="mt-6 w-full max-w-xs overflow-hidden rounded-pill border border-app-border/80 bg-app-surface-muted p-0.5"
              aria-hidden="true"
            >
              <div className="relative h-1.5 w-full overflow-hidden rounded-pill bg-app-border/40">
                <motion.div
                  className="h-full rounded-pill bg-gradient-to-r from-app-accent via-app-action to-app-action-hover"
                  initial={reducedMotion ? { width: '60%', x: '20%' } : { width: '45%', x: '-100%' }}
                  animate={reducedMotion ? { width: '60%', x: '20%' } : { x: '240%' }}
                  transition={
                    reducedMotion
                      ? undefined
                      : {
                          repeat: Infinity,
                          duration: 1.4,
                          ease: 'easeInOut',
                        }
                  }
                />
              </div>
            </div>

            <div className="mt-8 flex items-center justify-center gap-2 border-t border-app-border/80 pt-5 text-xs text-app-muted">
              <ShieldCheck size={16} className="shrink-0 text-app-action" aria-hidden="true" />
              <span>Verifying session with authentication gateway</span>
            </div>
          </section>
        </Surface>
      </div>
    </MotionPage>
  )
}
