import { afterEach, describe, expect, it, vi } from 'vitest'
import { toast } from 'sonner'
import { notify } from './notify'

vi.mock('sonner', () => ({
  toast: Object.assign(
    vi.fn((title, data) => data?.id ?? 'mock-id'),
    {
      success: vi.fn((title, data) => data?.id ?? 'success-id'),
      error: vi.fn((title, data) => data?.id ?? 'error-id'),
      info: vi.fn((title, data) => data?.id ?? 'info-id'),
      warning: vi.fn((title, data) => data?.id ?? 'warning-id'),
      loading: vi.fn((title, data) => data?.id ?? 'loading-id'),
      dismiss: vi.fn((id) => id ?? 'all'),
    },
  ),
}))

afterEach(() => {
  vi.clearAllMocks()
})

describe('notify adapter', () => {
  it('calls toast.success with 5000ms default duration', () => {
    const id = notify.success('Operation succeeded', { description: 'Detail' })
    expect(toast.success).toHaveBeenCalledWith('Operation succeeded', expect.objectContaining({
      description: 'Detail',
      duration: 5000,
    }))
    expect(id).toBe('success-id')
  })

  it('calls toast.info with 5000ms default duration', () => {
    notify.info('Info notice')
    expect(toast.info).toHaveBeenCalledWith('Info notice', expect.objectContaining({
      duration: 5000,
    }))
  })

  it('calls toast.loading with infinite duration', () => {
    const id = notify.loading('Submitting order…', { id: 'order-123' })
    expect(toast.loading).toHaveBeenCalledWith('Submitting order…', expect.objectContaining({
      id: 'order-123',
      duration: Number.POSITIVE_INFINITY,
    }))
    expect(id).toBe('order-123')
  })

  it('keeps warning and error persistent when action is required', () => {
    const onClick = vi.fn()
    notify.warning('Reconnection required', {
      action: { label: 'Reconnect', onClick },
    })
    expect(toast.warning).toHaveBeenCalledWith('Reconnection required', expect.objectContaining({
      duration: Number.POSITIVE_INFINITY,
      action: expect.objectContaining({ label: 'Reconnect' }),
    }))

    notify.error('Order rejected', {
      action: { label: 'Review', onClick },
    })
    expect(toast.error).toHaveBeenCalledWith('Order rejected', expect.objectContaining({
      duration: Number.POSITIVE_INFINITY,
    }))
  })

  it('allows custom duration override', () => {
    notify.success('Saved', { duration: 2000 })
    expect(toast.success).toHaveBeenCalledWith('Saved', expect.objectContaining({
      duration: 2000,
    }))
  })

  it('updates an existing toast with specified type', () => {
    notify.update('task-1', {
      type: 'success',
      title: 'Task completed',
      description: 'All records updated',
    })
    expect(toast.success).toHaveBeenCalledWith('Task completed', expect.objectContaining({
      id: 'task-1',
      description: 'All records updated',
      duration: 5000,
    }))
  })

  it('updates an existing toast without changing type', () => {
    notify.update('task-2', {
      title: 'Still processing…',
    })
    expect(toast).toHaveBeenCalledWith('Still processing…', expect.objectContaining({
      id: 'task-2',
    }))
  })

  it('dismisses a toast by id or all toasts', () => {
    notify.dismiss('toast-to-close')
    expect(toast.dismiss).toHaveBeenCalledWith('toast-to-close')

    notify.dismiss()
    expect(toast.dismiss).toHaveBeenCalledWith(undefined)
  })

  it('handles action click and callbacks', () => {
    const onDismiss = vi.fn()
    const onAutoClose = vi.fn()
    const onClick = vi.fn()

    notify.error('Network failure', {
      onDismiss,
      onAutoClose,
      action: { label: 'Retry', onClick },
    })

    const callArgs = vi.mocked(toast.error).mock.calls[0]?.[1]
    expect(callArgs).toBeDefined()
    callArgs?.onDismiss?.({} as never)
    expect(onDismiss).toHaveBeenCalledTimes(1)
    callArgs?.onAutoClose?.({} as never)
    expect(onAutoClose).toHaveBeenCalledTimes(1)

    const event = {} as React.MouseEvent<HTMLButtonElement>
    const action = callArgs?.action
    if (action && typeof action === 'object' && 'onClick' in action) {
      action.onClick(event)
    }
    expect(onClick).toHaveBeenCalledWith(event)
  })
})
