import { describe, expect, it, vi } from 'vitest'
import { ApiError } from './client'
import { operationFeedback, operationFeedbackId } from './operation-feedback'

describe('operation feedback contract', () => {
  it('does not produce feedback without an error', () => {
    expect(operationFeedback(null)).toBeNull()
    expect(operationFeedback(undefined)).toBeNull()
  })
  it('keeps field validation inline with safe diagnostics, without duplicating a toast', () => {
    expect(operationFeedback(new ApiError(422, { code: 'invalid', request_id: 'request-1', retry: 'fix_request',
      fields: [{ path: 'duration_minutes', code: 'range' }] }))).toEqual({ surface: 'inline', code: 'invalid',
      requestId: 'request-1', fields: [{ path: 'duration_minutes', code: 'range' }], retry: 'fix_request', retryAt: null, outcomeUnknown: false })
  })
  it.each([409, 422, 503])('does not infer retry permission from HTTP %s', (status) => {
    expect(operationFeedback(new ApiError(status)).retry).toBe('none')
  })
  it.each(['refresh_state', 'same_request', 'reconnect', 'contact_support'] as const)('retains %s guidance', (retry) => {
    expect(operationFeedback(new ApiError(409, { retry })).retry).toBe(retry)
  })
  it('keeps cooldown and unknown outcome distinct', () => {
    expect(operationFeedback(new ApiError(429, { retry: 'wait', retry_after_seconds: 7 }), 1000)).toMatchObject({ retryAt: 8000, outcomeUnknown: false })
    expect(operationFeedback(new TypeError('network'))).toMatchObject({ outcomeUnknown: true, retry: 'same_request' })
    expect(operationFeedback(new DOMException('timed out', 'TimeoutError')).outcomeUnknown).toBe(true)
    expect(operationFeedback(new Error('parse failure')).retry).toBe('none')
  })
  it('uses distinct stable identities for independent receipts', () => {
    expect(operationFeedbackId('command', '1')).toBe(operationFeedbackId('command', '1'))
    expect(operationFeedbackId('command', '1')).not.toBe(operationFeedbackId('recovery', '1'))
    expect(operationFeedbackId('a:b', 'c')).not.toBe(operationFeedbackId('a', 'b:c'))
  })
  it('anchors cooldown to response observation rather than postponing it on every render', () => {
    const clock = vi.spyOn(Date, 'now').mockReturnValue(1000)
    const error = new ApiError(429, { retry: 'wait', retry_after_seconds: 7 })
    clock.mockReturnValue(5000)
    expect(operationFeedback(error).retryAt).toBe(8000)
    clock.mockReturnValue(7000)
    expect(operationFeedback(error).retryAt).toBe(8000)
    clock.mockRestore()
  })

})
