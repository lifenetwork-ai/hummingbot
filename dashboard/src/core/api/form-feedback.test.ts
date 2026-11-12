import { describe, expect, it } from 'vitest'
import { ApiError } from './client'
import { deriveFormFeedback } from './operation-feedback'
const error = new ApiError(422, { code: 'invalid_request', request_id: 'req-1', retry: 'fix_request', fields: [
  { path: '/body/duration_seconds', code: 'range' }, { path: 'expected_version', code: 'conflict' }, { path: 'confirmation_token', code: 'expired' },
] })
describe('form feedback handoff', () => {
  it('maps only editable session fields and preserves safe diagnostics/retry guidance', () => {
    const model = deriveFormFeedback(error, 'session')
    expect(model.inlineErrors).toEqual({ durationSeconds: ['range'] })
    expect(model.globalFields.map((field) => field.path)).toEqual(['expected_version', 'confirmation_token'])
    expect(model.feedback).toMatchObject({ requestId: 'req-1', retry: 'fix_request', code: 'invalid_request' })
  })
  it('does not create a credential field that the selected catalogue did not advertise', () => {
    const failure = new ApiError(422, { fields: [{ path: 'credentials.api_key', code: 'required' }, { path: 'credentials.unknown', code: 'extra' }] })
    expect(deriveFormFeedback(failure, 'credentials', ['api_key']).inlineErrors).toEqual({ api_key: ['required'] })
    expect(deriveFormFeedback(failure, 'credentials', ['api_key']).globalFields).toEqual([{ path: 'credentials.unknown', code: 'extra' }])
    expect(deriveFormFeedback(failure, 'credentials', []).inlineErrors).toEqual({})
  })
  it('keeps recovery tokens out of editable inputs and separates unknown network outcomes', () => {
    expect(deriveFormFeedback(error, 'recovery').inlineErrors).toEqual({})
    expect(deriveFormFeedback(new TypeError('network'), 'session').feedback).toMatchObject({ outcomeUnknown: true, retry: 'same_request' })
    expect(deriveFormFeedback(null, 'account')).toEqual({ feedback: null, inlineErrors: {}, globalFields: [] })
  })
})
