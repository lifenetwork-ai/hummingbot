import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { SectionLabel } from './SectionLabel'

describe('SectionLabel', () => {
  it('uses muted style by default', () => {
    render(<SectionLabel>Section</SectionLabel>)
    expect(screen.getByText('Section')).toHaveClass('text-app-muted')
  })
  it('supports brand style and custom classes', () => {
    render(<SectionLabel tone="brand" className="extra">Title</SectionLabel>)
    expect(screen.getByText('Title')).toHaveClass('text-app-action', 'extra')
  })
})
