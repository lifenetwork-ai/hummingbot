import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { LoginCard } from './LoginCard'

vi.mock('./GoogleSignIn', () => ({
  GoogleSignIn: vi.fn(({ onCredential, disabled }: { onCredential: (cred: string) => void; disabled: boolean }) => (
    <button
      data-testid="google-signin-btn"
      disabled={disabled}
      onClick={() => onCredential('test-token')}
    >
      Sign in with Google
    </button>
  )),
}))

describe('LoginCard', () => {
  it('renders branding, heading, and sign-in button', () => {
    const onLogin = vi.fn()
    render(<LoginCard status="anonymous" error={null} onLogin={onLogin} />)

    expect(screen.getByRole('heading', { name: 'Đăng nhập vào bảng điều khiển' })).toBeInTheDocument()
    expect(screen.getByText('LIFE · Market making')).toBeInTheDocument()
    const btn = screen.getByTestId('google-signin-btn')
    expect(btn).toBeInTheDocument()
    expect(btn).not.toBeDisabled()

    fireEvent.click(btn)
    expect(onLogin).toHaveBeenCalledWith('test-token')
  })

  it('renders redirect destination when provided', () => {
    render(<LoginCard status="anonymous" error={null} redirect="/ui-kit" onLogin={vi.fn()} />)
    expect(screen.getByText('/ui-kit')).toBeInTheDocument()
  })

  it('renders authenticating loading indicator and disables button', () => {
    render(<LoginCard status="authenticating" error={null} onLogin={vi.fn()} />)
    expect(screen.getByRole('status')).toHaveTextContent('Đang xác minh quyền truy cập…')
    expect(screen.getByTestId('google-signin-btn')).toBeDisabled()
  })

  it('renders AuthAlert when error is present', () => {
    render(<LoginCard status="anonymous" error="Access denied: account not found" onLogin={vi.fn()} />)
    const alert = screen.getByRole('alert')
    expect(alert).toHaveTextContent('Access denied: account not found')
    expect(alert).toHaveTextContent('Quyền truy cập chỉ giới hạn cho tài khoản vận hành đã được phê duyệt.')
  })
})
