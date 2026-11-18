import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { CursorPagination } from './CursorPagination'

describe('CursorPagination', () => {
  it('renders nothing when all pages are loaded', () => {
    const { container } = render(<CursorPagination itemLabel="command" hasMore={false} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('renders nothing without an onLoadMore handler', () => {
    const { container } = render(<CursorPagination itemLabel="command" hasMore={true} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('pluralizes the label and triggers onLoadMore', () => {
    const onLoadMore = vi.fn()
    render(<CursorPagination itemLabel="log" pluralLabel="logs" hasMore={true} onLoadMore={onLoadMore} />)
    fireEvent.click(screen.getByRole('button', { name: /Load more logs/i }))
    expect(onLoadMore).toHaveBeenCalledTimes(1)
  })

  it('singular items do not double the s and the button disables while loading', () => {
    render(<CursorPagination itemLabel="command" hasMore={true} isLoading={true} onLoadMore={() => {}} />)
    expect(screen.getByRole('button', { name: /Loading more…/i })).toBeDisabled()
  })
})
