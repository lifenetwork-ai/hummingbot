import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { BrandLogo } from './BrandLogo'

describe('BrandLogo', () => {
  it('uses self-hosted LifeAI brand assets', () => {
    render(<BrandLogo />)

    expect(screen.getByRole('img', { name: 'LIFE AI' })).toHaveAttribute(
      'src',
      '/brand/lifeai-logo.svg',
    )

    expect(document.querySelector('img[aria-hidden="true"]')).toHaveAttribute(
      'src',
      '/brand/lifeai-logo-white.svg',
    )
  })
})
