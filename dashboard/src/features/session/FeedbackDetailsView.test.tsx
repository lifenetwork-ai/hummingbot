import { render, screen, fireEvent, act } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { FeedbackDetailsView } from './FeedbackDetailsView'
import type { OperationFeedback } from '../../core/api/operation-feedback'

describe('FeedbackDetailsView', () => {
  it('renders request ID, error code and retry guidance', () => {
    const feedback: OperationFeedback = {
      surface: 'inline',
      code: 'validation_error',
      requestId: 'req-abc-12345678',
      fields: [],
      retry: 'fix_request',
      retryAt: null,
      outcomeUnknown: false,
    }

    render(<FeedbackDetailsView feedback={feedback} />)

    expect(screen.getByText('Code: validation_error')).toBeInTheDocument()
    expect(screen.getByText(/Inspect and adjust form fields/)).toBeInTheDocument()

    // Open diagnostics
    const toggle = screen.getByRole('button', { name: /Show diagnostics details/ })
    fireEvent.click(toggle)

    expect(screen.getByText('req-abc-12345678')).toBeInTheDocument()
  })

  it('renders outcome unknown warning and retry callback for same_request', () => {
    const feedback: OperationFeedback = {
      surface: 'banner',
      code: 'timeout_error',
      requestId: 'req-timeout-888',
      fields: [],
      retry: 'same_request',
      retryAt: null,
      outcomeUnknown: true,
    }

    const onRetry = vi.fn()
    const onRefresh = vi.fn()

    render(
      <FeedbackDetailsView
        feedback={feedback}
        onRetry={onRetry}
        onRefresh={onRefresh}
      />,
    )

    // Outcome unknown warning
    expect(screen.getByText('Outcome Unknown')).toBeInTheDocument()
    expect(screen.getByText(/Do not create and submit a new mutation with a new idempotency key/)).toBeInTheDocument()

    // Action buttons
    const retryBtn = screen.getByRole('button', { name: /Retry with same request/ })
    fireEvent.click(retryBtn)
    expect(onRetry).toHaveBeenCalledTimes(1)

    const refreshBtn = screen.getByRole('button', { name: /Refresh state/ })
    fireEvent.click(refreshBtn)
    expect(onRefresh).toHaveBeenCalledTimes(1)
  })

  it('renders global unmapped fields errors', () => {
    const globalFields = [
      { path: 'version', code: 'version_conflict' },
      { path: 'catalogue_hash', code: 'hash_mismatch' },
    ]

    render(<FeedbackDetailsView globalFields={globalFields} />)

    expect(screen.getByText('Global / Read-only Fields Errors:')).toBeInTheDocument()
    expect(screen.getByText('version:')).toBeInTheDocument()
    expect(screen.getByText('version_conflict')).toBeInTheDocument()
    expect(screen.getByText('catalogue_hash:')).toBeInTheDocument()
    expect(screen.getByText('hash_mismatch')).toBeInTheDocument()
  })

  it('returns null when neither feedback nor globalFields are provided', () => {
    const { container } = render(<FeedbackDetailsView />)
    expect(container.firstChild).toBeNull()
  })
  it('keeps explicit retries disabled before the supplied cooldown without submitting automatically', () => {
    vi.useFakeTimers()
    const retry = vi.fn()
    try {
      render(<FeedbackDetailsView feedback={{ surface: 'banner', code: null, requestId: 'cooldown', fields: [], retry: 'wait', retryAt: Date.now() + 1000, outcomeUnknown: false }} onRetry={retry} />)
      const button = screen.getByRole('button', { name: /Retry with same request/ })
      expect(button).toBeDisabled()
      act(() => { vi.advanceTimersByTime(1000) })
      expect(button).toBeEnabled()
      expect(retry).not.toHaveBeenCalled()
      fireEvent.click(button)
      expect(retry).toHaveBeenCalledTimes(1)
    } finally { vi.useRealTimers() }
  })

})
