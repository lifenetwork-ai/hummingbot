import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { EmptyState } from './EmptyState'

describe('EmptyState', () => {
  it('renders a title and explanation without a CTA by default', () => {
    render(<EmptyState title="Nothing here" description="Add an item to begin" />)
    expect(screen.getByRole('heading', { name: 'Nothing here' })).toBeVisible()
    expect(screen.getByText('Add an item to begin')).toBeVisible()
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })

  it('renders a caller-supplied action', () => {
    render(<EmptyState title="Empty" description="No data" action={<button type="button">Create</button>} />)
    expect(screen.getByRole('button', { name: 'Create' })).toBeVisible()
  })
})
