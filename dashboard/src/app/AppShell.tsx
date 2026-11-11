import { Link, Outlet } from '@tanstack/react-router'
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion'
import { ChevronDown, LogOut, Menu, Moon, Sun, X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { useAuth } from '../core/auth/auth-context'
import { Badge } from '../shared/components/Badge'
import { BrandLogo } from '../shared/components/BrandLogo'
import { useTheme } from '../theme/useTheme'
import { cn } from '../shared/utils/cn'

const navItems = [
  { to: '/' as const, label: 'Home' },
  { to: '/ui-kit' as const, label: 'UI kit' },
]

export function AppShell() {
  const auth = useAuth()
  const { theme, toggleTheme } = useTheme()
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false)
  const [userMenuOpen, setUserMenuOpen] = useState(false)
  const userMenuRef = useRef<HTMLDivElement>(null)
  const reducedMotion = useReducedMotion()

  useEffect(() => {
    if (!userMenuOpen) return
    const handleClickOutside = (event: MouseEvent) => {
      if (userMenuRef.current && !userMenuRef.current.contains(event.target as Node)) {
        setUserMenuOpen(false)
      }
    }
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setUserMenuOpen(false)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    document.addEventListener('keydown', handleKeyDown)
    return () => {
      document.removeEventListener('mousedown', handleClickOutside)
      document.removeEventListener('keydown', handleKeyDown)
    }
  }, [userMenuOpen])

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
              <div ref={userMenuRef} className="relative">
                <button
                  type="button"
                  onClick={() => setUserMenuOpen((open) => !open)}
                  aria-expanded={userMenuOpen}
                  aria-haspopup="menu"
                  aria-label={`Tài khoản ${auth.identity.email ?? auth.identity.id}`}
                  className="flex items-center gap-2 rounded-card border border-app-border bg-app-surface py-1.5 pl-2 pr-2.5 text-xs transition-colors hover:border-app-action focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-app-action"
                >
                  <span className="grid h-6 w-6 place-items-center rounded-full bg-app-action/10 text-[11px] font-bold uppercase text-app-action">
                    {auth.identity.email ? auth.identity.email[0] : 'U'}
                  </span>
                  <span
                    className="max-w-[140px] truncate font-medium text-app-text hidden sm:inline-block"
                    title={auth.identity.email ?? undefined}
                  >
                    {auth.identity.email ?? auth.identity.id}
                  </span>
                  <Badge tone={auth.identity.role === 'admin' ? 'brand' : 'neutral'} className="text-[10px] px-1.5 py-0 hidden md:inline-flex">
                    {auth.identity.role_label || auth.identity.role}
                  </Badge>
                  <ChevronDown
                    size={14}
                    className={`text-app-muted transition-transform duration-200 ${userMenuOpen ? 'rotate-180' : ''}`}
                  />
                </button>

                <AnimatePresence>
                  {userMenuOpen && (
                    <motion.div
                      role="menu"
                      aria-label="Tùy chọn tài khoản"
                      initial={reducedMotion ? false : { opacity: 0, y: 6, scale: 0.98 }}
                      animate={{ opacity: 1, y: 0, scale: 1 }}
                      exit={reducedMotion ? undefined : { opacity: 0, y: 4, scale: 0.98 }}
                      transition={{ duration: reducedMotion ? 0 : 0.15 }}
                      className="absolute right-0 top-full mt-2 w-64 z-50 rounded-card border border-app-border bg-app-surface p-2 shadow-card"
                    >
                      <div className="flex items-center gap-3 rounded-button bg-app-surface-muted/60 p-2.5">
                        <span className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-app-action/10 text-sm font-bold uppercase text-app-action">
                          {auth.identity.email ? auth.identity.email[0] : 'U'}
                        </span>
                        <div className="min-w-0 flex-1">
                          <p className="truncate text-xs font-semibold text-app-text" title={auth.identity.email ?? undefined}>
                            {auth.identity.email ?? auth.identity.id}
                          </p>
                          <div className="mt-0.5 flex items-center gap-1.5">
                            <Badge tone={auth.identity.role === 'admin' ? 'brand' : 'neutral'} className="text-[10px] px-1.5 py-0">
                              {auth.identity.role_label || auth.identity.role}
                            </Badge>
                            <span className="truncate text-[11px] text-app-muted">Người vận hành</span>
                          </div>
                        </div>
                      </div>

                      <div className="my-1.5 border-t border-app-border/80" />

                      <div className="px-1 py-1">
                        <div className="mb-1.5 px-2 text-[11px] font-medium text-app-muted">
                          Giao diện
                        </div>
                        <div className="grid grid-cols-2 gap-1 rounded-button bg-app-surface-muted p-1 border border-app-border/60">
                          <button
                            type="button"
                            aria-label="Use light theme"
                            onClick={() => { if (theme === 'dark') toggleTheme() }}
                            className={cn(
                              'flex cursor-pointer items-center justify-center gap-1.5 rounded-button py-1.5 text-xs transition-all',
                              theme === 'light'
                                ? 'bg-app-surface text-app-action font-semibold shadow-xs border border-app-border/60'
                                : 'text-app-muted hover:text-app-text font-medium',
                            )}
                          >
                            <Sun size={14} />
                            <span>Sáng</span>
                          </button>
                          <button
                            type="button"
                            aria-label="Use dark theme"
                            onClick={() => { if (theme === 'light') toggleTheme() }}
                            className={cn(
                              'flex cursor-pointer items-center justify-center gap-1.5 rounded-button py-1.5 text-xs transition-all',
                              theme === 'dark'
                                ? 'bg-app-surface text-app-action font-semibold shadow-xs border border-app-border/60'
                                : 'text-app-muted hover:text-app-text font-medium',
                            )}
                          >
                            <Moon size={14} />
                            <span>Tối</span>
                          </button>
                        </div>
                      </div>

                      <div className="my-1.5 border-t border-app-border/80" />

                      <button
                        type="button"
                        aria-label="Sign out"
                        onClick={() => {
                          setUserMenuOpen(false)
                          auth.logout()
                          setMobileMenuOpen(false)
                        }}
                        className="flex w-full items-center gap-2 rounded-button px-3 py-2 text-xs font-semibold text-app-danger transition-colors hover:bg-app-danger/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-app-danger"
                      >
                        <LogOut size={15} />
                        <span>Đăng xuất</span>
                      </button>
                    </motion.div>
                  )}
                </AnimatePresence>
              </div>
            )}

            {auth.status !== 'authenticated' && (
              <button
                type="button"
                onClick={toggleTheme}
                aria-label={theme === 'light' ? 'Use dark theme' : 'Use light theme'}
                className="grid h-11 w-11 place-items-center rounded-button border border-app-border bg-app-surface text-app-muted transition-colors hover:border-app-action hover:text-app-action focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-app-action"
              >
                {theme === 'light' ? <Moon size={17} /> : <Sun size={17} />}
              </button>
            )}

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
                <div className="mb-4 rounded-card border border-app-border bg-app-surface p-3 text-xs">
                  <div className="flex items-center justify-between">
                    <div className="flex min-w-0 items-center gap-2.5">
                      <span className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-app-action/10 text-xs font-bold uppercase text-app-action">
                        {auth.identity.email ? auth.identity.email[0] : 'U'}
                      </span>
                      <div className="min-w-0">
                        <p className="truncate font-semibold text-app-text">{auth.identity.email ?? auth.identity.id}</p>
                        <p className="text-[11px] text-app-muted">Người vận hành được ủy quyền</p>
                      </div>
                    </div>
                    <Badge tone={auth.identity.role === 'admin' ? 'brand' : 'neutral'} className="shrink-0 text-[10px]">
                      {auth.identity.role_label || auth.identity.role}
                    </Badge>
                  </div>
                  <div className="mt-3 flex items-center justify-between border-t border-app-border/80 pt-2.5">
                    <div className="grid grid-cols-2 gap-1 rounded-button bg-app-surface-muted p-0.5 border border-app-border/60">
                      <button
                        type="button"
                        aria-label="Use light theme"
                        onClick={() => { if (theme === 'dark') toggleTheme() }}
                        className={cn(
                          'flex items-center gap-1.5 rounded-button px-2.5 py-1 text-xs transition-all',
                          theme === 'light'
                            ? 'bg-app-surface text-app-action font-semibold shadow-xs'
                            : 'text-app-muted hover:text-app-text font-medium',
                        )}
                      >
                        <Sun size={13} />
                        <span>Sáng</span>
                      </button>
                      <button
                        type="button"
                        aria-label="Use dark theme"
                        onClick={() => { if (theme === 'light') toggleTheme() }}
                        className={cn(
                          'flex items-center gap-1.5 rounded-button px-2.5 py-1 text-xs transition-all',
                          theme === 'dark'
                            ? 'bg-app-surface text-app-action font-semibold shadow-xs'
                            : 'text-app-muted hover:text-app-text font-medium',
                        )}
                      >
                        <Moon size={13} />
                        <span>Tối</span>
                      </button>
                    </div>
                    <button
                      type="button"
                      aria-label="Sign out"
                      onClick={() => {
                        auth.logout()
                        setMobileMenuOpen(false)
                      }}
                      className="flex items-center gap-1.5 rounded-button border border-app-danger/30 bg-app-danger/5 px-3 py-1.5 text-xs font-semibold text-app-danger transition-colors hover:bg-app-danger/10"
                    >
                      <LogOut size={14} />
                      <span>Đăng xuất</span>
                    </button>
                  </div>
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
          <span>Lưu hành nội bộ</span>
        </div>
      </footer>
    </div>
  )
}
