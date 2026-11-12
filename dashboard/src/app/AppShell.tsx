import { Link, Outlet, useLocation } from '@tanstack/react-router'
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion'
import { BarChart3, ChevronDown, FileText, LogOut, Menu, Moon, OctagonAlert, Shield, Sun, X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { useAuth } from '../core/auth/auth-context'
import { Badge } from '../shared/components/Badge'
import { BrandLogo } from '../shared/components/BrandLogo'
import { useTheme } from '../theme/useTheme'
import { cn } from '../shared/utils/cn'

const mainNavItems = [
  { to: '/' as const, label: 'Overview' },
  { to: '/session' as const, label: 'Session' },
  { to: '/orders' as const, label: 'Orders' },
]

const reportNavItems = [
  {
    to: '/statistics' as const,
    label: 'Daily Statistics',
    description: 'PnL, volume & fill rate statistics',
    icon: BarChart3,
  },
  {
    to: '/activity' as const,
    label: 'Activity & Reconciliation',
    description: 'Bot logs, execution reconciliation & recovery',
    icon: FileText,
  },
]

export function AppShell() {
  const auth = useAuth()
  const location = useLocation()
  const pathname = (typeof location === 'string' ? location : location?.pathname) ?? ''
  const isItemActive = (to: string) => {
    if (to === '/') return pathname === '/'
    return pathname === to || pathname.startsWith(to + '/')
  }
  const isReportActive = pathname === '/statistics' || pathname === '/activity' || pathname.startsWith('/statistics/') || pathname.startsWith('/activity/')

  const { theme, toggleTheme } = useTheme()
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false)
  const [userMenuOpen, setUserMenuOpen] = useState(false)
  const [reportMenuOpen, setReportMenuOpen] = useState(false)
  const userMenuRef = useRef<HTMLDivElement>(null)
  const reportMenuRef = useRef<HTMLDivElement>(null)
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

  useEffect(() => {
    if (!reportMenuOpen) return
    const handleClickOutside = (event: MouseEvent) => {
      if (reportMenuRef.current && !reportMenuRef.current.contains(event.target as Node)) {
        setReportMenuOpen(false)
      }
    }
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setReportMenuOpen(false)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    document.addEventListener('keydown', handleKeyDown)
    return () => {
      document.removeEventListener('mousedown', handleClickOutside)
      document.removeEventListener('keydown', handleKeyDown)
    }
  }, [reportMenuOpen])

  const toggleReportMenu = () => {
    setReportMenuOpen((open) => {
      if (!open) setUserMenuOpen(false)
      return !open
    })
  }

  const toggleUserMenu = () => {
    setUserMenuOpen((open) => {
      if (!open) setReportMenuOpen(false)
      return !open
    })
  }

  return (
    <div className="flex min-h-screen flex-col bg-app-bg text-app-text">
      <header className="sticky top-0 z-40 border-b border-app-border/80 bg-app-bg/[0.88] backdrop-blur-xl">
        <div className="mx-auto flex h-[76px] w-full max-w-[1536px] items-center gap-1 px-3 sm:gap-2 sm:px-6 md:gap-7 md:px-8 lg:px-10">
          <Link to="/" className="flex shrink-0 items-center gap-3" aria-label="LIFE AI Market making — Go to home">
            <BrandLogo className="[&_img]:h-6 sm:[&_img]:h-7" />
            {/* <span className="hidden border-l border-app-border pl-3 text-[10px] font-semibold uppercase tracking-[0.18em] text-app-muted sm:block">
              Market making
            </span> */}
          </Link>

          <nav hidden={auth.status !== 'authenticated'} className="hidden items-center gap-1 md:flex" aria-label="Main navigation">
            {auth.status === 'authenticated' && (
              <>
                {mainNavItems.map((item) => {
                  const active = isItemActive(item.to)
                  return (
                    <Link
                      key={item.to}
                      to={item.to}
                      activeOptions={{ exact: true }}
                      className={cn(
                        'rounded-button px-3 py-2 text-sm transition-colors',
                        active
                          ? 'bg-app-action/10 text-app-action font-semibold shadow-xs'
                          : 'text-app-muted hover:bg-app-surface-muted/60 hover:text-app-text font-medium',
                      )}
                    >
                      {item.label}
                    </Link>
                  )
                })}

                {/* Dropdown Reports */}
                <div ref={reportMenuRef} className="relative">
                  <button
                    type="button"
                    onClick={toggleReportMenu}
                    aria-expanded={reportMenuOpen}
                    aria-haspopup="menu"
                    aria-label="Reports and analytics menu"
                    className={cn(
                      'flex items-center gap-1 rounded-button px-3 py-2 text-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-app-action',
                      isReportActive || reportMenuOpen
                        ? 'bg-app-action/10 text-app-action font-semibold shadow-xs'
                        : 'text-app-muted hover:bg-app-surface-muted/60 hover:text-app-text font-medium',
                    )}
                  >
                    <span>Reports</span>
                    <ChevronDown
                      size={14}
                      className={cn('transition-transform duration-200', reportMenuOpen ? 'rotate-180' : '')}
                    />
                  </button>

                  <AnimatePresence>
                    {reportMenuOpen && (
                      <motion.div
                        role="menu"
                        aria-label="Report options"
                        initial={reducedMotion ? false : { opacity: 0, y: 6, scale: 0.98 }}
                        animate={{ opacity: 1, y: 0, scale: 1 }}
                        exit={reducedMotion ? undefined : { opacity: 0, y: 4, scale: 0.98 }}
                        transition={{ duration: reducedMotion ? 0 : 0.15 }}
                        className="absolute left-0 top-full mt-2 w-72 z-50 rounded-card border border-app-border bg-app-surface p-1.5 shadow-card"
                      >
                        <div className="grid gap-1">
                          {reportNavItems.map((item) => {
                            const Icon = item.icon
                            const isActive = pathname === item.to || pathname.startsWith(item.to + '/')
                            return (
                              <Link
                                key={item.to}
                                to={item.to}
                                onClick={() => setReportMenuOpen(false)}
                                className={cn(
                                  'flex items-start gap-2.5 rounded-button p-2.5 transition-colors',
                                  isActive
                                    ? 'bg-app-action/10 text-app-action font-semibold'
                                    : 'text-app-text hover:bg-app-surface-muted/60',
                                )}
                              >
                                <div
                                  className={cn(
                                    'mt-0.5 grid h-7 w-7 shrink-0 place-items-center rounded-lg',
                                    isActive
                                      ? 'bg-app-action text-white'
                                      : 'bg-app-action/10 text-app-action',
                                  )}
                                >
                                  <Icon size={15} />
                                </div>
                                <div className="min-w-0 flex-1">
                                  <div className="text-xs font-semibold flex items-center justify-between">
                                    <span className={isActive ? 'text-app-action' : 'text-app-text'}>
                                      {item.label}
                                    </span>
                                    {isActive && (
                                      <span className="h-1.5 w-1.5 rounded-full bg-app-action" />
                                    )}
                                  </div>
                                  <p className="mt-0.5 text-[11px] leading-tight text-app-muted font-normal">
                                    {item.description}
                                  </p>
                                </div>
                              </Link>
                            )
                          })}
                        </div>
                      </motion.div>
                    )}
                  </AnimatePresence>
                </div>
              </>
            )}
          </nav>

          <div className="ml-auto flex items-center gap-1.5 sm:gap-2">
            {auth.status === 'authenticated' && (
              <button
                type="button"
                onClick={() => window.dispatchEvent(new CustomEvent('open-stop-dialog'))}
                aria-label="Stop orders"
                disabled={auth.identity?.permissions.operate !== true}
                className="inline-flex h-9 items-center gap-1.5 rounded-button bg-app-danger px-2 py-1.5 text-xs font-bold sm:px-3 text-white shadow-sm transition-opacity hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-app-danger focus-visible:ring-offset-2"
              >
                <OctagonAlert size={14} className="shrink-0" />
                <span className="hidden sm:inline">Stop orders</span>
                <span className="sm:hidden">Stop</span>
              </button>
            )}

            {auth.status === 'authenticated' && auth.identity && (
              <div ref={userMenuRef} className="relative">
                <button
                  type="button"
                  onClick={toggleUserMenu}
                  aria-expanded={userMenuOpen}
                  aria-haspopup="menu"
                  className="flex items-center gap-1 rounded-card border border-app-border bg-app-surface px-1.5 py-1.5 sm:gap-2 sm:pl-2 sm:pr-2.5 text-xs transition-colors hover:border-app-action focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-app-action"
                >
                  <span className="sr-only">Account </span>
                  <span className="sr-only sm:hidden">{auth.identity.email ?? auth.identity.id}</span>
                  <span aria-hidden="true" className="grid h-6 w-6 place-items-center rounded-full bg-app-action/10 text-[11px] font-bold uppercase text-app-action">
                    {auth.identity.email ? auth.identity.email[0] : 'U'}
                  </span>
                  <span
                    className="sr-only font-medium text-app-text sm:not-sr-only sm:inline-block sm:max-w-[140px] sm:truncate"
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
                      aria-label="Account options"
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
                            <span className="truncate text-[11px] text-app-muted">Authorized operator</span>
                          </div>
                        </div>
                      </div>

                      <div className="my-1.5 border-t border-app-border/80" />

                      <div className="px-1 py-0.5">
                        <Link
                          to="/admin"
                          onClick={() => setUserMenuOpen(false)}
                          className={cn(
                            'flex items-center gap-2.5 rounded-button px-2.5 py-2 text-xs transition-colors',
                            pathname === '/admin' || pathname.startsWith('/admin/')
                              ? 'bg-app-action/10 text-app-action font-semibold'
                              : 'text-app-text hover:bg-app-surface-muted hover:text-app-action',
                          )}
                        >
                          <div
                            className={cn(
                              'grid h-7 w-7 shrink-0 place-items-center rounded-lg',
                              pathname === '/admin' || pathname.startsWith('/admin/')
                                ? 'bg-app-action text-white'
                                : 'bg-app-action/10 text-app-action',
                            )}
                          >
                            <Shield size={15} />
                          </div>
                          <div className="min-w-0 flex-1 text-left">
                            <div className={cn('font-semibold', pathname === '/admin' || pathname.startsWith('/admin/') ? 'text-app-action' : 'text-app-text')}>
                              System Administration
                            </div>
                            <div className="text-[11px] text-app-muted">Exchange connections, accounts & audit logs</div>
                          </div>
                        </Link>
                      </div>

                      <div className="my-1.5 border-t border-app-border/80" />

                      <div className="px-1 py-1">
                        <div className="mb-1.5 px-2 text-[11px] font-medium text-app-muted">
                          Theme
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
                            <span>Light</span>
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
                            <span>Dark</span>
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
                        <span>Sign out</span>
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
                        <p className="text-[11px] text-app-muted">Authorized operator</p>
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
                        <span>Light</span>
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
                        <span>Dark</span>
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
                      <span>Sign out</span>
                    </button>
                  </div>
                </div>
              )}

              <nav className="grid gap-1" aria-label="Mobile navigation">
                {auth.status === 'authenticated' && (
                  <>
                    <div className="px-3 pt-1 pb-1 text-[11px] font-bold uppercase tracking-wider text-app-muted">
                      Operations
                    </div>
                    {mainNavItems.map((item) => {
                      const active = isItemActive(item.to)
                      return (
                        <Link
                          key={item.to}
                          to={item.to}
                          activeOptions={{ exact: true }}
                          onClick={() => setMobileMenuOpen(false)}
                          className={cn(
                            'rounded-button px-3.5 py-2.5 text-sm transition-all',
                            active
                              ? 'bg-app-action/10 text-app-action font-semibold'
                              : 'text-app-muted hover:bg-app-surface-muted hover:text-app-text font-medium',
                          )}
                        >
                          {item.label}
                        </Link>
                      )
                    })}

                    <div className="mt-2 px-3 pt-2 pb-1 text-[11px] font-bold uppercase tracking-wider text-app-muted border-t border-app-border/60">
                      Reports & Analytics
                    </div>
                    {reportNavItems.map((item) => {
                      const active = pathname === item.to || pathname.startsWith(item.to + '/')
                      return (
                        <Link
                          key={item.to}
                          to={item.to}
                          activeOptions={{ exact: true }}
                          onClick={() => setMobileMenuOpen(false)}
                          className={cn(
                            'rounded-button px-3.5 py-2.5 text-sm transition-all',
                            active
                              ? 'bg-app-action/10 text-app-action font-semibold'
                              : 'text-app-muted hover:bg-app-surface-muted hover:text-app-text font-medium',
                          )}
                        >
                          {item.label}
                        </Link>
                      )
                    })}

                    <div className="mt-2 px-3 pt-2 pb-1 text-[11px] font-bold uppercase tracking-wider text-app-muted border-t border-app-border/60">
                      System
                    </div>
                    <Link
                      to="/admin"
                      activeOptions={{ exact: true }}
                      onClick={() => setMobileMenuOpen(false)}
                      className={cn(
                        'flex items-center gap-2 rounded-button px-3.5 py-2.5 text-sm transition-all',
                        pathname === '/admin' || pathname.startsWith('/admin/')
                          ? 'bg-app-action/10 text-app-action font-semibold'
                          : 'text-app-muted hover:bg-app-surface-muted hover:text-app-text font-medium',
                      )}
                    >
                      <Shield size={16} className={pathname === '/admin' || pathname.startsWith('/admin/') ? 'text-app-action' : 'text-app-muted'} />
                      <span>System Administration</span>
                    </Link>
                  </>
                )}
              </nav>
            </motion.div>
          ) : null}
        </AnimatePresence>
      </header>

      <main className={cn('flex flex-1 flex-col', auth.status === 'authenticated' ? 'pb-12' : '')}>
        <Outlet />
      </main>
      {auth.status !== 'authenticated' && (
        <footer className="mx-auto w-full max-w-[1536px] shrink-0 border-t border-app-border/80 px-4 sm:px-6 md:px-8 lg:px-10 py-3 text-xs text-app-muted">
          <div className="flex flex-col items-center justify-between gap-2 sm:flex-row">
            <span>LifeAI · Market making</span>
            <span>Internal use only</span>
          </div>
        </footer>
      )}
    </div>
  )
}
