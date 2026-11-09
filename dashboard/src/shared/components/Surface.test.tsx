import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { Surface } from './Surface'

describe('Surface', () => {
  it('forwards semantics, DOM attributes and classes', () => {
    render(<Surface role="region" aria-label="Details" className="px-4">Content</Surface>)
    expect(screen.getByRole('region', { name: 'Details' })).toHaveClass('px-4', 'bg-app-surface')
    expect(screen.getByText('Content')).toBeVisible()
  })
})
