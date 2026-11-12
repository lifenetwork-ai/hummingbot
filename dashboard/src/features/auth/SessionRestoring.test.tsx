import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { SessionRestoring } from './SessionRestoring'

describe('SessionRestoring', () => {
  it('renders status role with accessible message for session restoration', () => {
    render(<SessionRestoring />)

    const statusRegion = screen.getByRole('status')
    expect(statusRegion).toBeInTheDocument()
    expect(statusRegion).toHaveTextContent('Restoring session…')
    expect(statusRegion).toHaveTextContent('LIFE · Market making')
    expect(statusRegion).toHaveTextContent('Verifying saved credentials')
    expect(statusRegion).toHaveTextContent('Verifying session with authentication gateway')
  })

  it('renders the brand logo and heading', () => {
    render(<SessionRestoring />)

    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Restoring session…')
    expect(screen.getByAltText('LIFE AI')).toBeInTheDocument()
  })
})
