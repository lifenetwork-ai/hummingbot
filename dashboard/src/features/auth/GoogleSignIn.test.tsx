import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { GoogleSignIn } from './GoogleSignIn'
import { loadGoogleIdentity } from './google-script'
vi.mock('./google-script', () => ({ loadGoogleIdentity: vi.fn() }))
afterEach(() => { vi.unstubAllEnvs(); vi.resetAllMocks() })

describe('Google sign-in control', () => {
  it('explains missing configuration without loading Google', () => {
    vi.stubEnv('VITE_GOOGLE_CLIENT_ID', '')
    render(<GoogleSignIn onCredential={vi.fn()} disabled={false} />)
    expect(screen.getByRole('status')).toHaveTextContent('not configured')
    expect(loadGoogleIdentity).not.toHaveBeenCalled()
  })
  it('reports a script failure and supports explicit retry', async () => {
    vi.stubEnv('VITE_GOOGLE_CLIENT_ID', 'public-client-id')
    vi.mocked(loadGoogleIdentity).mockRejectedValue(new Error('offline'))
    render(<GoogleSignIn onCredential={vi.fn()} disabled={false} />)
    await screen.findByRole('alert')
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    await waitFor(() => expect(loadGoogleIdentity).toHaveBeenCalledTimes(2))
    expect(screen.getByRole('alert')).toHaveTextContent('Failed to load Google')
  })
  it('ignores callbacks while submitting and after unmount', async () => {
    vi.stubEnv('VITE_GOOGLE_CLIENT_ID', 'public-client-id')
    let receive!: (response: { credential: string }) => void
    vi.mocked(loadGoogleIdentity).mockResolvedValue({
      initialize: ({ callback }) => { receive = callback }, renderButton: vi.fn(),
    })
    const onCredential = vi.fn()
    const view = render(<GoogleSignIn onCredential={onCredential} disabled={false} />)
    await waitFor(() => expect(receive).toBeTypeOf('function'))
    act(() => receive({ credential: 'first' }))
    expect(onCredential).toHaveBeenCalledWith('first')
    view.rerender(<GoogleSignIn onCredential={onCredential} disabled />)
    act(() => receive({ credential: 'second' }))
    view.unmount()
    act(() => receive({ credential: 'late' }))
    expect(onCredential).toHaveBeenCalledTimes(1)
  })
})
