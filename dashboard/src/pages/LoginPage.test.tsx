import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { LoginPage } from './LoginPage'
import * as authContext from '../core/auth/auth-context'
import * as router from '@tanstack/react-router'

vi.mock('@tanstack/react-router', () => ({
  useNavigate: vi.fn(),
  useSearch: vi.fn(),
}))

vi.mock('../features/auth/LoginCard', () => ({
  LoginCard: vi.fn(({ status, error, redirect, onLogin }: { status: string; error: string | null; redirect?: string; onLogin: (cred: string) => void }) => (
    <div data-testid="login-card">
      <span data-testid="card-status">{status}</span>
      <span data-testid="card-error">{error}</span>
      <span data-testid="card-redirect">{redirect}</span>
      <button onClick={() => onLogin('test-credential')}>Mock Login</button>
    </div>
  )),
}))

describe('LoginPage', () => {
  it('waits for stored credential verification instead of mounting Google login', () => {
    const navigate = vi.fn()
    vi.spyOn(router, 'useSearch').mockReturnValue({ redirect: '/?market=spot#orders' } as never)
    vi.spyOn(router, 'useNavigate').mockReturnValue(navigate)
    vi.spyOn(authContext, 'useAuth').mockReturnValue({ status: 'restoring', identity: null, error: null,
      login: vi.fn(), logout: vi.fn(), apiSession: undefined })
    render(<LoginPage />)
    expect(screen.queryByTestId('login-card')).not.toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('Đang khôi phục phiên đăng nhập')
    expect(navigate).not.toHaveBeenCalled()
  })

  it('orchestrates routing, passes state to LoginCard, and triggers login', () => {
    const login = vi.fn()
    vi.spyOn(router, 'useSearch').mockReturnValue({ redirect: '/ui-kit' } as never)
    vi.spyOn(router, 'useNavigate').mockReturnValue(vi.fn())
    vi.spyOn(authContext, 'useAuth').mockReturnValue({
      status: 'anonymous',
      identity: null,
      error: 'Invalid token',
      login,
      logout: vi.fn(),
      apiSession: undefined,
    })

    render(<LoginPage />)
    expect(screen.getByTestId('login-card')).toBeInTheDocument()
    expect(screen.getByTestId('card-status')).toHaveTextContent('anonymous')
    expect(screen.getByTestId('card-error')).toHaveTextContent('Invalid token')
    expect(screen.getByTestId('card-redirect')).toHaveTextContent('/ui-kit')

    fireEvent.click(screen.getByRole('button', { name: 'Mock Login' }))
    expect(login).toHaveBeenCalledWith('test-credential')
  })

  it('redirects and returns null when status is authenticated', () => {
    const navigate = vi.fn()
    vi.spyOn(router, 'useSearch').mockReturnValue({ redirect: '/ui-kit' } as never)
    vi.spyOn(router, 'useNavigate').mockReturnValue(navigate)
    vi.spyOn(authContext, 'useAuth').mockReturnValue({
      status: 'authenticated',
      identity: { id: 'test', kind: 'google', email: 'test@example.com', role: 'admin', role_label: 'Admin', permissions: {} },
      error: null,
      login: vi.fn(),
      logout: vi.fn(),
      apiSession: undefined,
    })

    const { container } = render(<LoginPage />)
    expect(container).toBeEmptyDOMElement()
    expect(navigate).toHaveBeenCalledWith({ to: '/ui-kit', replace: true })
  })
})
