import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { CopyButton } from './CopyButton'
import { CopyableId } from './CopyableId'

describe('CopyButton & CopyableId', () => {
  it('renders CopyableId with truncated text and full id in title', () => {
    const fullId = 'cmd-1234567890abcdef'
    render(<CopyableId id={fullId} truncateLength={8} />)

    expect(screen.getByText('cmd-1234…')).toBeInTheDocument()
    const container = screen.getByTitle(fullId)
    expect(container).toBeInTheDocument()
  })

  it('renders full text when id length is below truncateLength', () => {
    const shortId = 'cmd-1'
    render(<CopyableId id={shortId} truncateLength={8} />)
    expect(screen.getByText('cmd-1')).toBeInTheDocument()
  })

  it('copies text to clipboard when copy button is clicked', async () => {
    const user = userEvent.setup()
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText },
      writable: true,
      configurable: true,
    })

    const fullId = 'order-999-long-id'
    render(<CopyableId id={fullId} truncateLength={5} />)

    const copyBtn = screen.getByRole('button', { name: /Copy/i })
    await user.click(copyBtn)

    expect(writeText).toHaveBeenCalledWith(fullId)
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Copied' })).toBeInTheDocument()
    })
  })

  it('stops click propagation so parent handler is not triggered', async () => {
    const user = userEvent.setup()
    const onParentClick = vi.fn()
    render(
      <div role="button" tabIndex={0} onKeyDown={() => {}} onClick={onParentClick}>
        <CopyButton text="sample-text" />
      </div>
    )

    const copyBtn = screen.getByRole('button', { name: /Copy/i })
    await user.click(copyBtn)

    expect(onParentClick).not.toHaveBeenCalled()
  })
})
