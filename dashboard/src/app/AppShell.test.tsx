import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { AppShell } from './AppShell'
import * as authContext from '../core/auth/auth-context'
import { ThemeProvider } from '../theme/useTheme'

vi.mock('@tanstack/react-router', () => ({
  Link: ({
    children,
    to,
    onClick,
    className,
    'aria-label': ariaLabel,
  }: {
    children: React.ReactNode
    to?: string
    onClick?: () => void
    className?: string
    'aria-label'?: string
  }) => (
    <a href={to ?? '#'} onClick={onClick} className={className} aria-label={ariaLabel}>
      {children}
    </a>
  ),
  Outlet: () => <div data-testid="outlet">Outlet Content</div>,
  useLocation: () => ({ pathname: '/', href: '/' }),
}))

describe('AppShell', () => {
  it('renders brand and theme toggle when anonymous', () => {
    vi.spyOn(authContext, 'useAuth').mockReturnValue({
      status: 'anonymous',
      identity: null,
      error: null,
      login: vi.fn(),
      logout: vi.fn(),
      apiSession: undefined,
    })

    render(
      <ThemeProvider>
        <AppShell />
      </ThemeProvider>,
    )
    expect(screen.getByLabelText(/LIFE AI Market making/i)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Sign out' })).not.toBeInTheDocument()
    expect(screen.getByTestId('outlet')).toBeInTheDocument()
  })

  it('renders user identity, role badge and sign out when authenticated', () => {
    const logout = vi.fn()
    vi.spyOn(authContext, 'useAuth').mockReturnValue({
      status: 'authenticated',
      identity: {
        id: 'user-123',
        kind: 'google',
        email: 'operator@example.test',
        role: 'admin',
        role_label: 'Administrator',
        permissions: { admin: true },
      },
      error: null,
      login: vi.fn(),
      logout,
      apiSession: undefined,
    })

    render(
      <ThemeProvider>
        <AppShell />
      </ThemeProvider>,
    )
    expect(screen.getByTitle('operator@example.test')).toBeInTheDocument()
    expect(screen.getByText('Administrator')).toBeInTheDocument()

    const menuTrigger = screen.getByRole('button', { name: /Account operator@example\.test/i })
    fireEvent.click(menuTrigger)
    const signOutBtn = screen.getByRole('button', { name: 'Sign out' })
    expect(signOutBtn).toBeInTheDocument()

    // System Administration link is rendered in user menu
    const adminLink = screen.getByRole('link', { name: /System Administration/i })
    expect(adminLink).toBeInTheDocument()
    expect(adminLink).toHaveAttribute('href', '/admin')

    fireEvent.click(signOutBtn)
    expect(logout).toHaveBeenCalledTimes(1)
  })

  it('renders streamlined navigation items and toggles report dropdown', () => {
    vi.spyOn(authContext, 'useAuth').mockReturnValue({
      status: 'authenticated',
      identity: {
        id: 'user-123',
        kind: 'google',
        email: 'operator@example.test',
        role: 'write',
        role_label: 'Operator',
        permissions: {},
      },
      error: null,
      login: vi.fn(),
      logout: vi.fn(),
      apiSession: undefined,
    })

    render(
      <ThemeProvider>
        <AppShell />
      </ThemeProvider>,
    )

    // Primary navigation links and active highlights
    const homeLink = screen.getByRole('link', { name: 'Overview' })
    const sessionLink = screen.getByRole('link', { name: 'Session' })
    expect(homeLink).toBeInTheDocument()
    expect(sessionLink).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Orders' })).toBeInTheDocument()

    // Active item has text-app-action and no text-app-muted, inactive item has text-app-muted
    expect(homeLink.className).toContain('text-app-action')
    expect(homeLink.className).not.toContain('text-app-muted')
    expect(sessionLink.className).toContain('text-app-muted')
    expect(sessionLink.className).not.toContain('text-app-action')

    // Report dropdown trigger
    const reportBtn = screen.getByRole('button', { name: 'Reports and analytics menu' })
    expect(reportBtn).toBeInTheDocument()
    expect(reportBtn).toHaveAttribute('aria-expanded', 'false')

    // Open report dropdown
    fireEvent.click(reportBtn)
    expect(reportBtn).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByRole('link', { name: /Daily Statistics/i })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Activity & Reconciliation/i })).toBeInTheDocument()

    // Close on Escape
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(reportBtn).toHaveAttribute('aria-expanded', 'false')
  })

  it('renders categorized mobile navigation when opened', () => {
    vi.spyOn(authContext, 'useAuth').mockReturnValue({
      status: 'authenticated',
      identity: {
        id: 'user-123',
        kind: 'google',
        email: 'operator@example.test',
        role: 'write',
        role_label: 'Operator',
        permissions: {},
      },
      error: null,
      login: vi.fn(),
      logout: vi.fn(),
      apiSession: undefined,
    })

    render(
      <ThemeProvider>
        <AppShell />
      </ThemeProvider>,
    )

    const mobileMenuTrigger = screen.getByRole('button', { name: 'Open navigation' })
    fireEvent.click(mobileMenuTrigger)

    const mobileNav = screen.getByRole('navigation', { name: 'Mobile navigation' })
    expect(mobileNav).toBeInTheDocument()
    expect(screen.getByText('Operations')).toBeInTheDocument()
    expect(screen.getByText('Reports & Analytics')).toBeInTheDocument()
    expect(screen.getByText('System')).toBeInTheDocument()
  })
})
