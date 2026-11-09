import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { Badge } from './Badge'

describe('Badge', () => {
  it('renders neutral by default', () => {
    render(<Badge>Status</Badge>)
    expect(screen.getByText('Status')).toHaveClass('text-app-muted')
  })

  it.each(['brand', 'success', 'danger'] as const)('renders %s semantic tone', (tone) => {
    render(<Badge tone={tone} className="extra">Status</Badge>)
    expect(screen.getByText('Status')).toHaveClass(`text-app-${tone === 'brand' ? 'action' : tone}`, 'extra')
  })
})
