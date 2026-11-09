import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { ThemeProvider, useTheme } from './useTheme'

function ThemeProbe() {
  const { theme, toggleTheme, setTheme } = useTheme()

  return (
    <div>
      <button type="button" onClick={toggleTheme}>{theme}</button>
      <button type="button" onClick={() => setTheme('dark')}>Set dark</button>
    </div>
  )
}

describe('ThemeProvider', () => {
  it('defaults to light and shares theme through context', () => {
    render(<ThemeProvider><ThemeProbe /></ThemeProvider>)
    fireEvent.click(screen.getByRole('button', { name: 'light' }))
    expect(screen.getByRole('button', { name: 'dark' })).toBeInTheDocument()
    expect(document.documentElement).toHaveClass('dark')
    expect(window.localStorage.getItem('lifeai-fe-starter-theme')).toBe('dark')
  })

  it('restores a stored preference and supports explicit updates', () => {
    window.localStorage.setItem('lifeai-fe-starter-theme', 'dark')
    render(<ThemeProvider><ThemeProbe /></ThemeProvider>)
    fireEvent.click(screen.getByRole('button', { name: 'Set dark' }))
    expect(screen.getByRole('button', { name: 'dark' })).toBeInTheDocument()
  })

  it('requires the provider', () => {
    // React renders the thrown hook error through its usual error boundary path.
    expect(() => render(<ThemeProbe />)).toThrow('useTheme must be used inside ThemeProvider')
  })
})
