import { act, renderHook } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { useObservationTime } from './useObservationTime'

describe('cached observation clock', () => {
  afterEach(() => vi.useRealTimers())
  it('advances cached evidence age without a response and releases its timer on disable/unmount', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-11-16T00:00:00Z'))
    const { result, rerender, unmount } = renderHook(({ enabled }) => useObservationTime(enabled), { initialProps: { enabled: true } })
    const initial = result.current
    act(() => vi.advanceTimersByTime(6000))
    expect(result.current - initial).toBe(6000)
    rerender({ enabled: false })
    expect(vi.getTimerCount()).toBe(0)
    rerender({ enabled: true })
    expect(vi.getTimerCount()).toBe(1)
    unmount()
    expect(vi.getTimerCount()).toBe(0)
  })
})
