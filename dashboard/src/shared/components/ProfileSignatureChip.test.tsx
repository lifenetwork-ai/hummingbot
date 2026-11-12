import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { ProfileSignatureChip } from './ProfileSignatureChip'

describe('ProfileSignatureChip', () => {
  it('renders default fallback when profileId is empty or unknown', () => {
    render(<ProfileSignatureChip profileId={null} />)
    expect(screen.getByText('No profile')).toBeInTheDocument()
  })

  it('renders fixed tight profile with signature styling', () => {
    render(<ProfileSignatureChip profileId="fixed_tight" selection="fixed" />)
    const chip = screen.getByTestId('profile-signature-chip')
    expect(chip).toBeInTheDocument()
    expect(chip).toHaveTextContent('fixed_tight')
    expect(chip.className).toContain('emerald')
  })

  it('renders random profile with purple signature styling', () => {
    render(<ProfileSignatureChip profileId="random_rotation" selection="random" />)
    const chip = screen.getByTestId('profile-signature-chip')
    expect(chip).toBeInTheDocument()
    expect(chip).toHaveTextContent('random_rotation')
    expect(chip.className).toContain('purple')
  })
})
