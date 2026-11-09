import { http, HttpResponse } from 'msw'

// Test-only endpoint: no dependency on the original Vesting backend.
export const MOCK_API_URL = 'https://example.test/api/health'

export const handlers = [
  http.get(MOCK_API_URL, () => HttpResponse.json({ ok: true, service: 'starter' })),
]
