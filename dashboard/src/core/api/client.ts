export type ApiFieldError = { path: string; code: string }

export type ApiErrorBody = {
  error?: string
  code?: string | null
  request_id?: string | null
  retry?: string | null
  retry_after_seconds?: number | null
  fields?: ApiFieldError[]
  detail?: string | null
  [key: string]: unknown
}

export class ApiError extends Error {
  readonly status: number
  readonly body: ApiErrorBody | undefined
  readonly requestId: string | undefined
  readonly retry: string | undefined
  readonly retryAfterSeconds: number | undefined
  readonly fields: ApiFieldError[]

  constructor(status: number, body?: ApiErrorBody) {
    super(body?.detail ?? body?.error ?? `Request failed with status ${status}`)
    this.name = 'ApiError'
    this.status = status
    this.body = body
    this.requestId = body?.request_id ?? undefined
    this.retry = body?.retry ?? undefined
    this.retryAfterSeconds = body?.retry_after_seconds ?? undefined
    this.fields = body?.fields ?? []
  }
}

export type ApiClientOptions = {
  baseUrl: string
  getToken?: () => string | null | undefined | Promise<string | null | undefined>
  fetch?: typeof globalThis.fetch
}

export type ApiRequestOptions = Omit<RequestInit, 'body' | 'headers' | 'method'> & {
  method?: string
  headers?: HeadersInit
  body?: BodyInit | object | null
  idempotencyKey?: string
}

export type ApiClient = {
  request<T>(path: string, options?: ApiRequestOptions): Promise<T>
}

export function createApiClient({ baseUrl, getToken, fetch: fetchImpl = globalThis.fetch }: ApiClientOptions): ApiClient {
  return {
    async request<T>(path: string, options: ApiRequestOptions = {}): Promise<T> {
      const { idempotencyKey, body, headers: inputHeaders, ...init } = options
      const headers = new Headers(inputHeaders)
      if (body !== undefined && body !== null && typeof body === 'object' && !(body instanceof Blob) && !(body instanceof FormData) && !(body instanceof URLSearchParams) && !(body instanceof ArrayBuffer)) {
        headers.set('Content-Type', 'application/json')
      }
      const token = await getToken?.()
      if (token) headers.set('Authorization', `Bearer ${token}`)
      if (idempotencyKey !== undefined) headers.set('Idempotency-Key', idempotencyKey)
      const requestUrl = `${baseUrl.replace(/\/+$/, '')}/${path.replace(/^\/+/, '')}`
      const response = await fetchImpl(requestUrl, {
        ...init,
        method: options.method ?? (body === undefined ? 'GET' : 'POST'),
        headers,
        body: body === undefined || body === null ? undefined : headers.get('Content-Type') === 'application/json' && typeof body === 'object' ? JSON.stringify(body) : body as BodyInit,
      })
      if (!response.ok) {
        let errorBody: ApiErrorBody | undefined
        try {
          const parsed: unknown = await response.json()
          if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) errorBody = parsed as ApiErrorBody
        } catch {
          // Preserve status for empty or non-JSON error responses.
        }
        throw new ApiError(response.status, errorBody)
      }
      if (response.status === 204) return undefined as T
      return await response.json() as T
    },
  }
}
