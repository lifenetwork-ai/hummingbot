import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { SessionRestoring } from './SessionRestoring'

describe('SessionRestoring', () => {
  it('renders status role with accessible message for session restoration', () => {
    render(<SessionRestoring />)

    const statusRegion = screen.getByRole('status')
    expect(statusRegion).toBeInTheDocument()
    expect(statusRegion).toHaveTextContent('Restoring your sign-in')
    expect(statusRegion).toHaveTextContent('LIFE · Market making')
    expect(statusRegion).toHaveTextContent('Checking stored credentials and verifying workspace access')
    expect(statusRegion).toHaveTextContent('Verifying active session with admin access gateway')
  })

  it('renders the brand logo and heading', () => {
    render(<SessionRestoring />)

    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Restoring your sign-in…')
    expect(screen.getByAltText('LIFE AI')).toBeInTheDocument()
  })
})
