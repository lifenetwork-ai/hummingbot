import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { AuthAlert } from './AuthAlert'

describe('AuthAlert', () => {
  it('renders title and alert role', () => {
    render(<AuthAlert title="Something went wrong" />)
    const alert = screen.getByRole('alert')
    expect(alert).toBeInTheDocument()
    expect(alert).toHaveTextContent('Something went wrong')
  })

  it('renders optional description and action button', () => {
    render(
      <AuthAlert
        title="Connection failed"
        description="Please check your network connection."
        action={<button type="button">Retry</button>}
      />,
    )
    expect(screen.getByText('Please check your network connection.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument()
  })
})
