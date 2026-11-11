import type { PropsWithChildren } from 'react'
import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { AuthenticatedWorkspace } from './AuthenticatedWorkspace'
import * as authContext from '../core/auth/auth-context'

const { navigate } = vi.hoisted(() => ({ navigate: vi.fn() }))
vi.mock('@tanstack/react-router', () => ({ useLocation: () => '/?market=spot#orders', useNavigate: () => navigate }))
vi.mock('../features/operational-context/OperationalProvider', () => ({
  OperationalProvider: ({ children }: PropsWithChildren) => <div data-testid="operational-provider">{children}</div>,
}))
vi.mock('./OperationalShell', () => ({ OperationalShell: () => <div>Protected workspace</div> }))

function setAuth(status: authContext.AuthStatus) {
  navigate.mockClear()
  vi.spyOn(authContext, 'useAuth').mockReturnValue({ status, identity: null, error: null,
    login: vi.fn(), logout: vi.fn(), apiSession: undefined })
}

describe('AuthenticatedWorkspace auth boundary', () => {
  it('does not redirect or mount operational queries while restoring a token', () => {
    setAuth('restoring')
    render(<AuthenticatedWorkspace />)
    expect(screen.getByRole('status')).toHaveTextContent('Đang khôi phục phiên đăng nhập')
    expect(screen.queryByTestId('operational-provider')).not.toBeInTheDocument()
    expect(navigate).not.toHaveBeenCalled()
  })

  it('preserves the safe deep link when restoration leaves the user anonymous', () => {
    setAuth('anonymous')
    render(<AuthenticatedWorkspace />)
    expect(navigate).toHaveBeenCalledWith({ to: '/login', search: { redirect: '/?market=spot#orders' }, replace: true })
    expect(screen.queryByTestId('operational-provider')).not.toBeInTheDocument()
  })

  it('mounts the operational provider only after authentication', () => {
    setAuth('authenticated')
    render(<AuthenticatedWorkspace />)
    expect(screen.getByTestId('operational-provider')).toBeInTheDocument()
    expect(navigate).not.toHaveBeenCalled()
  })
})
