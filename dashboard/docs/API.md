# LIFE MM API foundation

The dashboard lives in `dashboard/` alongside the Python backend. Endpoint behavior
and financial semantics are documented in [FE integration](../../docs/fe-integration.md).

## Contract generation

From the repository root, in the backend Python environment:

```sh
make swagger
make swagger-check
```

`make swagger` first generates OpenAPI from Python and then updates the dashboard
types. Backend generation failure stops the chain. `make swagger-check` checks both
artifacts without writing them. Install dashboard dependencies first with
`pnpm --dir dashboard install --frozen-lockfile`; both commands require Python and Node/pnpm.

For frontend-only regeneration from the checked OpenAPI, from `dashboard/`:

```sh
pnpm run api:generate
pnpm run api:check
```

The pinned development tool `openapi-typescript` generates `src/core/api/schema.d.ts`
offline from the checked schema. Commit both artifacts when the contract changes.
Frontend CI checks generated types when dashboard or schema changes. Backend drift
checking uses `make swagger-backend-check` in its Python-only CI lane; the two CI lanes jointly check the contract chain. Generated declarations are excluded from ESLint; TypeScript
checks their consumers. No server is needed for frontend generation.

## HTTP adapter

```ts
import { createApiClient } from '../src/core/api/client'
import type { RequestBody, ResponseBody } from '../src/core/api/types'

const api = createApiClient({
  baseUrl: 'https://your-service.example/api/v1/admin',
  getToken: () => currentGoogleIdToken(), // Supplied by the in-memory auth session.
})
const orders = await api.request<ResponseBody<'listOrders'>>('/orders?limit=50')
// Start/Stop bodies use RequestBody<'submitCommand'>.
```

The adapter reads a token for each request, accepts AbortSignal and explicit
Idempotency-Key, preserves JSON strings/nulls, and exposes HTTP status plus safe
error details through ApiError. It does not validate successful JSON at runtime;
generated types provide compile-time checks only. Network, abort and malformed
success responses reject normally. No token storage or Basic credentials are built in.

An accepted mutation is a receipt, not completion. Persist the receipt identity and
original idempotency key in the eventual feature workflow; poll the appropriate
command or credential endpoint. After timeout, retry the same request/key. Refresh
versions after conflict. The client never retries automatically or creates new keys.
Generic request<T> does not enforce route/body pairing; use generated operation types
in feature adapters, with runtime validation where needed.

## React Query stack

The integration layers are:

```text
OpenAPI → generated DTOs → typed admin operation adapter → query options/keys → hooks → feature UI
```

The shared server data for authenticated routes follows the workspace boundary:

```text
AppProviders (Theme → Auth → authenticated API session)
└── root AppShell (brand/theme/footer)
    └── AuthenticatedWorkspace (auth guard)
        └── OperationalProvider (useOperationalController → React Query)
            └── OperationalShell (useOperationalContext consumer)
                ├── ContextBar
                └── Outlet → protected route pages
```

The operational provider mounts only below the authenticated API boundary. It
composes the controller once and exposes a derived context to the shell and protected
pages. `ContextBar` and page views consume that context or receive explicit props;
they do not read auth/config directly or start duplicate context queries. Server
observations remain in TanStack Query; the provider does not copy them to a second
store. Public routes such as Login and UI Kit do not mount the provider or its polling.

`admin.ts` maps every current JSON admin operation, excluding Swagger/docs assets.
Operation names, paths, filters, required bodies, idempotency headers and response
types come from the checked backend contract. A test rejects operation-map drift.

`AppProviders` accepts an optional `apiSession` (or use `ApiProvider` directly):

```tsx
<AppProviders apiSession={{
  baseUrl: serviceUrl + '/api/v1/admin',
  cacheIdentity: verifiedPrincipalId + ':' + loginSessionRevision,
  principalId: verifiedPrincipalId,
  getToken: getCurrentGoogleIdToken,
}}>
  <Application />
</AppProviders>
```

The application supplies these from `AuthProvider`; the optional provider override
is also available for tests. No URL, token or identity is fabricated. Without a
session, public routes send no admin requests; consuming admin hooks requires a session.
Never put tokens in `cacheIdentity`, query keys or Vite configuration. Change
`cacheIdentity` on every login/logout or privilege change, even for the same email.
A service/identity change remounts the provider with a fresh QueryClient, cancels
old reads and clears its query/mutation caches. Same-session token callbacks stay
current without rebuilding the cache. Do not reuse an identity key across users.
Each provider owns its own client; do not copy its query options into a shared
cross-account QueryClient without adding equivalent service/identity isolation.

```ts
const orders = useAdminQuery('listOrders', {
  query: { market: 'spot', limit: 50 },
})
const history = useAdminInfiniteQuery('listFills', {
  query: { market: 'perpetual', limit: 50 },
})
const command = useAdminMutation('submitCommand')
const accepted = await command.mutateAsync({
  body: { action: 'stop', expected_version: currentVersion, session_id: sessionId },
  idempotencyKey: retainedOperationKey,
})
// Follow accepted.id with useCommandReceipt; acceptance is not completion.
```

Call hooks at component top level. `useCommandReceipt(id)`,
`useCredentialReceipt(id)` and `useReconciliationReceipt(id)` use skipToken when
no ID exists, poll the requested receipt every second while pending, and stop on
succeeded/failed/superseded. Terminal receipt observations invalidate their related
current-state panels. A succeeded reconciliation receipt can still report mismatch.
Recovery uses `checkRecovery` / `resetRecovery` and
`useAdminQuery('getRecoveryOperation', { path: { operation_id: accepted.id } })`.
Its receipt follows the same pending/terminal polling policy and refreshes recovery,
runtime and position observations at completion; recovery does not submit Start.
`useAdminQuery(operation, undefined)` also skips safely; `enabled: false` pauses
an otherwise configured query but still permits manual `refetch()`.

`adminQueryOptions` and `adminInfiniteOptions` support prefetching/testing outside
hooks, in the same session-owned client. Keys contain operation, path and all
filters. Old filter requests are canceled; no cross-filter placeholder data is
shown. Current-state queries poll at the guide's initial cadences, with background
polling disabled. Profiles/configuration/history receipts fetch on demand. Use
`{ poll: false }` to disable periodic refresh and `{ enabled: false }` to pause.
Stale daily statistics honor the backend retry delay while preserving their
original observation timestamps. Values are never coerced to numbers.

Infinite history queries retain filters across opaque cursor pages, reset with
filter changes, and deduplicate concurrent next-page requests by default. They do
not poll every loaded page. Repeated cursors end pagination. Reference-pair search
uses its distinct `after` contract through ordinary `useAdminQuery` calls.

Read retries are bounded to two retries for network failures and eligible 503s;
auth/validation/conflict, cancellation and parse errors are not retried. Polling
also stops on non-retryable errors. Retried reads retain last-good data. Independent
query keys let history failures coexist with status/controls. Backend-supplied
retry delays take precedence over default exponential delays.

Mutations always disable automatic retries, offline queuing and optimistic changes.
Only one request at a time is accepted per mutation hook; this guard covers HTTP
submission, not the later async operation lifetime. Backend idempotency still
handles duplicate requests across hooks, reloads or operators. HTTP acceptance
invalidates related observations, then the receipt hook refreshes them again at
completion. A conflict/error does not claim success or trigger success invalidation.

The mutation hook exposes `mutateAsync`, status/data/error/reset, and deliberately
hides React Query's raw `mutate` and `variables`. Actual bodies, including credential
secrets, stay outside mutation state; only an opaque ticket enters its cache. Inputs
are removed from the temporary request map as submission begins and on settlement.
Consumers must clear secret form fields after submission. No query/mutation cache
persistence or Devtools are installed. Do not add persistence of secret input state.

Successful operation responses with a valid receipt identity retain a bounded,
non-secret reference through `receipt-tracking.ts`. Capture requires a stable
`principalId` and a successful, non-invalidated status observation with a bot ID;
the API/principal/bot scope is captured before submission so a late response cannot
be attributed to a new context. The list retains up to 50 references for seven days.
Browser storage failure does not fail an accepted mutation. This list is a local
convenience; deletion or concurrent tab updates can lose references, so backend
history remains the authoritative lookup.

`useTrackedReceipts()` reads that scoped list after the current backend bot identity
is available. UI can select a reference and call the corresponding read-only receipt
hook. The list does not poll receipts or replay mutations automatically. Only kind,
ID and an internal retention timestamp are persisted: no bodies, idempotency keys,
Google tokens, recovery confirmation tokens or exchange credentials. Timeout before
receiving an ID, same-body/key persistence and product recovery UI remain T-13 work.

## Test scenarios

`src/test/mocks/adminHandlers.ts` exports opt-in MSW handlers and synthetic fixtures:
recorded canceled orders with exact decimal strings, empty pagination, a pending
Stop receipt, and stale balances with zero distinct from unknown. They are never
installed in application runtime. Tests can override handlers for failures using
`apiFailure(code, retry)` and `server.use(...)`.

| Scope | Cases |
| --- | --- |
| HTTP adapter | Success, current auth, structured validation/conflict/503, malformed errors, network/abort, explicit retry key, independent concurrent requests |
| Fixtures | Decimal preservation, empty page, stale/null versus zero, pending receipt |
| React Query reads | Same-key deduplication, filter isolation, stale-response cancellation, bounded transient/persistent failure, retained observations, independent queries |
| Receipts and mutations | Pending/terminal polling, acceptance/completion invalidation, conflict/no retry, in-flight duplicate guard, explicit same-key recovery, secret-free mutation state |
| Session and history | Auth callback refresh, identity cache replacement and cancellation, cursor/filter handling, receipt remount with retained ID |
| Feature delivery boundaries | Browser persistence/reload recovery UI, cross-operator exchange effects, hosted auth/CORS/IAM and trading acceptance |

Run `pnpm run check` and `pnpm run api:check`. Browser tests are required when the
foundation is wired into UI; transport unit tests do not qualify hosted auth/CORS/IAM,
trading, database behavior or exchange side effects.


React Query v5 references: [useQuery](https://tanstack.com/query/v5/docs/framework/react/reference/useQuery),
[useMutation](https://tanstack.com/query/v5/docs/framework/react/reference/useMutation),
[QueryClient](https://tanstack.com/query/v5/docs/reference/QueryClient),
[disabling queries](https://tanstack.com/query/v5/docs/framework/react/guides/disabling-queries).

## Dashboard sign-in

Copy `.env.example` to `.env.local`. Set `VITE_GOOGLE_CLIENT_ID` to the Google OAuth
**web** client ID matching the backend audience, and `VITE_ADMIN_API_BASE_URL` to the
reachable admin prefix (default `/api/v1/admin`). Both are public configuration.
Register the dashboard origin in Google OAuth and the backend CORS configuration.
The relative API default requires a same-origin gateway; Vite does not provide an
API proxy automatically. A private Cloud Run endpoint requires the documented gateway.

The official Google Identity Services script supplies an ID token. The dashboard
calls header-only `POST /access/login`, then verifies identity through `/access/me`.
After verification, auth keeps the active token in memory and persists the same
credential in localStorage under an API-base-URL/Google-client-ID scoped key
(`life-mm.auth-token.v1:...`). Requests still use Bearer headers and omit cookies;
no backend session or refresh-token flow was added.

On reload, a new tab or reopening the browser, auth reads the stored token and
filters malformed/expired credentials locally, then calls only `GET /access/me`.
Identity, current permissions and protected API consumers remain unavailable until
that request succeeds. Routes show a restoring state without redirecting to Login
or starting operational polling early. A safe local deep link is preserved.

Logout, expiry and active 401 responses remove the matching persisted token and
clear the session/API cache. Storage events synchronize logout and token replacement
across tabs; focus/online rechecks catch suspended-tab expiry and allow a retry after
transient restoration failure. A changed token is verified before publishing its
identity. Late responses cannot overwrite a newer attempt or remove another tab's
new credential. A restore 401/403 removes storage; transient lookup failure denies
workspace access but keeps the unexpired credential for a later verification attempt.

Persistence never extends Google's expiry and never replays a mutation. If browser
storage is unavailable, a verified login can still work in memory for the current
page, but restoration across reload is unavailable. Only the Google sign-in token
has this approved persistence path: exchange secrets, recovery confirmation tokens,
query caches and mutation bodies are not persisted by auth. Tokens never belong in
URLs or logs. Hosted Google/CORS/gateway/IAM and real account acceptance still need
separate evidence.
