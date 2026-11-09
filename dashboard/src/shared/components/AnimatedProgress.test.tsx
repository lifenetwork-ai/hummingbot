import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { AnimatedProgress } from './AnimatedProgress'

describe('AnimatedProgress', () => {
  it('exposes progress accessibly', () => {
    render(<AnimatedProgress value={73.5} ariaLabel="Task progress" />)
    const progress = screen.getByRole('progressbar', { name: 'Task progress' })
    expect(progress).toHaveAttribute('aria-valuenow', '73.5')
    expect(progress).toHaveAttribute('aria-valuemin', '0')
    expect(progress).toHaveAttribute('aria-valuemax', '100')
  })

  it('clamps values to 0–100', () => {
    const { rerender } = render(<AnimatedProgress value={125} />)
    expect(screen.getByRole('progressbar', { name: 'Progress' })).toHaveAttribute('aria-valuenow', '100')
    rerender(<AnimatedProgress value={-50} className="custom-bar" trackClassName="custom-track" />)
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '0')
    expect(screen.getByRole('progressbar')).toHaveClass('custom-track')
  })
})
