import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { ToastHost } from './ToastHost'
import { ThemeProvider } from '../../theme/useTheme'

describe('ToastHost', () => {
  it('renders Toaster container within theme provider without crashing', () => {
    render(
      <ThemeProvider>
        <ToastHost />
      </ThemeProvider>,
    )
    expect(screen.getByRole('region', { name: /notifications/i })).toBeInTheDocument()
  })
})
