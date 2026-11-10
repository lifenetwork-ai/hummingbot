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
    expect(screen.getByText('operator@example.test')).toBeInTheDocument()
    expect(screen.getByText('Administrator')).toBeInTheDocument()
    const signOutBtn = screen.getByRole('button', { name: 'Sign out' })
    expect(signOutBtn).toBeInTheDocument()

    fireEvent.click(signOutBtn)
    expect(logout).toHaveBeenCalledTimes(1)
  })
})
