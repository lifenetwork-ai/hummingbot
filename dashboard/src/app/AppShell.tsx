import { Link, Outlet } from '@tanstack/react-router'
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion'
import { LogOut, Menu, Moon, Sun, X } from 'lucide-react'
import { useState } from 'react'
import { useAuth } from '../core/auth/auth-context'
import { Badge } from '../shared/components/Badge'
import { Button } from '../shared/components/Button'
import { BrandLogo } from '../shared/components/BrandLogo'
import { useTheme } from '../theme/useTheme'

const navItems = [
  { to: '/' as const, label: 'Home' },
  { to: '/ui-kit' as const, label: 'UI kit' },
]

export function AppShell() {
  const auth = useAuth()
  const { theme, toggleTheme } = useTheme()
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false)
  const reducedMotion = useReducedMotion()

  return (
    <div className="flex min-h-screen flex-col bg-app-bg text-app-text">
      <header className="sticky top-0 z-40 border-b border-app-border/80 bg-app-bg/[0.88] backdrop-blur-xl">
        <div className="mx-auto flex h-[76px] max-w-[1440px] items-center gap-2 px-3 sm:px-5 md:gap-7 md:px-10 lg:px-16">
          <Link to="/" className="flex shrink-0 items-center gap-3" aria-label="LIFE AI Market making — Go to home">
            <BrandLogo />
            <span className="hidden border-l border-app-border pl-3 text-[10px] font-semibold uppercase tracking-[0.18em] text-app-muted sm:block">
              Market making
            </span>
          </Link>

          <nav hidden={auth.status !== 'authenticated'} className="hidden items-center gap-1 md:flex" aria-label="Main navigation">
            {(auth.status === 'authenticated' ? navItems : []).map((item) => (
              <Link
                key={item.to}
                to={item.to}
                activeOptions={{ exact: true }}
                className="rounded-button px-3 py-2 text-sm font-medium text-app-muted transition-colors hover:text-app-text"
                activeProps={{ className: 'bg-app-surface-muted text-app-action' }}
              >
                {item.label}
              </Link>
            ))}
          </nav>

          <div className="ml-auto flex items-center gap-1.5 sm:gap-2">
            {auth.status === 'authenticated' && auth.identity && (
              <div
                className="hidden items-center gap-2 rounded-card border border-app-border bg-app-surface px-3 py-1.5 text-xs lg:flex"
                aria-label={`Logged in as ${auth.identity.email ?? auth.identity.id}`}
              >
                <span className="grid h-6 w-6 place-items-center rounded-full bg-app-action/10 text-[11px] font-bold text-app-action uppercase">
                  {auth.identity.email ? auth.identity.email[0] : 'U'}
                </span>
                <span className="max-w-[160px] truncate font-medium text-app-text" title={auth.identity.email ?? undefined}>
                  {auth.identity.email ?? auth.identity.id}
                </span>
                <Badge tone={auth.identity.role === 'admin' ? 'brand' : 'neutral'} className="text-[10px] px-1.5 py-0">
                  {auth.identity.role_label || auth.identity.role}
                </Badge>
              </div>
            )}

            {auth.status === 'authenticated' && (
              <Button
                variant="secondary"
                aria-label="Sign out"
                onClick={() => {
                  auth.logout()
                  setMobileMenuOpen(false)
                }}
                className="flex items-center gap-1.5 px-3 sm:px-5"
              >
                <LogOut size={15} />
                <span className="hidden sm:inline">Sign out</span>
              </Button>
            )}

            <button
              type="button"
              onClick={toggleTheme}
              aria-label={theme === 'light' ? 'Use dark theme' : 'Use light theme'}
              className="grid h-11 w-11 place-items-center rounded-button border border-app-border bg-app-surface text-app-muted transition-colors hover:border-app-action hover:text-app-action focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-app-action"
            >
              {theme === 'light' ? <Moon size={17} /> : <Sun size={17} />}
            </button>

            {auth.status === 'authenticated' && (
              <button
                type="button"
                className="grid h-11 w-11 place-items-center rounded-button border border-app-border bg-app-surface text-app-muted transition-colors hover:border-app-action hover:text-app-action md:hidden"
                aria-label={mobileMenuOpen ? 'Close navigation' : 'Open navigation'}
                aria-expanded={mobileMenuOpen}
                aria-controls="mobile-navigation"
                onClick={() => setMobileMenuOpen((open) => !open)}
              >
                {mobileMenuOpen ? <X size={18} /> : <Menu size={18} />}
              </button>
            )}
          </div>
        </div>

        <AnimatePresence initial={false}>
          {mobileMenuOpen ? (
            <motion.div
              id="mobile-navigation"
              initial={reducedMotion ? false : { opacity: 0, y: -8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={reducedMotion ? undefined : { opacity: 0, y: -8 }}
              transition={{ duration: reducedMotion ? 0 : 0.18 }}
              className="border-t border-app-border bg-app-bg px-5 py-5 md:hidden"
            >
              {auth.status === 'authenticated' && auth.identity && (
                <div className="mb-4 flex items-center justify-between rounded-card border border-app-border bg-app-surface p-3 text-xs">
                  <div className="flex min-w-0 items-center gap-2.5">
                    <span className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-app-action/10 text-xs font-bold text-app-action uppercase">
                      {auth.identity.email ? auth.identity.email[0] : 'U'}
                    </span>
                    <div className="min-w-0">
                      <p className="truncate font-semibold text-app-text">{auth.identity.email ?? auth.identity.id}</p>
                      <p className="text-[11px] text-app-muted">Authorized operator</p>
                    </div>
                  </div>
                  <Badge tone={auth.identity.role === 'admin' ? 'brand' : 'neutral'} className="shrink-0 text-[10px]">
                    {auth.identity.role_label || auth.identity.role}
                  </Badge>
                </div>
              )}

              <nav className="grid gap-1" aria-label="Mobile navigation">
                {(auth.status === 'authenticated' ? navItems : []).map((item) => (
                  <Link
                    key={item.to}
                    to={item.to}
                    activeOptions={{ exact: true }}
                    onClick={() => setMobileMenuOpen(false)}
                    className="rounded-button px-4 py-3 text-sm font-semibold text-app-muted transition-colors hover:bg-app-surface-muted hover:text-app-text"
                    activeProps={{ className: 'bg-app-surface-muted text-app-action' }}
                  >
                    {item.label}
                  </Link>
                ))}
              </nav>
            </motion.div>
          ) : null}
        </AnimatePresence>
      </header>

      <main className="flex-1">
        <Outlet />
      </main>
      <footer className="mx-auto w-full max-w-[1440px] border-t border-app-border px-5 py-8 text-sm text-app-muted md:px-10 lg:px-16">
        <div className="flex flex-col justify-between gap-3 sm:flex-row">
          <span>LifeAI · Market making</span>
          <span>Approved accounts only</span>
        </div>
      </footer>
    </div>
  )
}
