# Admin API and operations dashboard review

Reviewed source: `3c272c265`, 2026-10-27. Scope: the aiohttp handlers, generated
OpenAPI, models, control/session stores, reporting and deployment template.
There is no tracked dashboard application in this repository; this is an API
and proposed operator-workflow review, not a usability test of an existing UI.

The existing API can support a useful first dashboard. Keep the current service,
Basic Auth, PostgreSQL receipts, V2 reports and polling. The main remaining work
is making state, errors and recovery understandable, rather than adding another
trading engine or analytics platform. Liquidity, availability, inventory and
operating cost take precedence over profit in this dashboard.

The [FE integration guide](../docs/admin-dashboard-integration.md) defines the
proposed screens and how to use today's contract. Recommendations below are
**not implemented by this review**. No trading or deployment settings changed.

## Findings, in operational priority order

### 1. Resolve browser access before integrating the hosted dashboard

The [service template](../deploy/cloudrun/service.example.yaml) sets
`run.googleapis.com/ingress: internal`. The application implements Basic Auth
and CORS, but that does not give an ordinary external browser network access.
Hosting static FE assets on Google infrastructure does not make the user's
browser an internal caller.

Choose and test the actual browser-to-service route. Reuse an existing operations
gateway or FE backend with an allowed ingress path if one exists. If direct
browser access is selected instead, ingress and platform authentication need an
explicit deployment design; this review does not silently make the bot public.
Cloud Run ingress is separate from application authentication.
[Google ingress documentation](https://docs.cloud.google.com/run/docs/securing/ingress).

For an IAM-authenticated server-to-server hop, Google supports
`X-Serverless-Authorization: Bearer ...` while the application's `Authorization`
header remains Basic. This does not by itself solve browser CORS preflight or
internal ingress. Google's end-user guidance specifically addresses preflight
with authenticated services. Keep cloud credentials out of a static FE build.
[Service authentication](https://docs.cloud.google.com/run/docs/authenticating/service-to-service),
[browser authentication](https://docs.cloud.google.com/run/docs/authenticating/end-users).

Priority: deployment prerequisite. Validate GET, OPTIONS, POST and error responses
through the intended route with trading stopped; no separate staging environment
is required.

### 2. Validation and conflicts are too generic for actionable forms

[`admin_errors`](../hummingbot/cli/admin_api.py#L71) collapses all validation into
`422 {"error":"validation_failed"}`. Invalid market and negative duration produce
the same response. The FE cannot reliably identify the field to fix.

All `ControlConflict` subclasses become the same 409. That combines stale control
version, changed session, active session, HALT, immutable policy mismatch,
idempotency-key reuse, and reconciliation cooldown/in-progress states. They need
different operator actions; blindly retrying all conflicts is incorrect.
See [control checks](../hummingbot/model/bot_state_store.py#L140) and
[reconciliation checks](../hummingbot/model/admin_reconciliation.py#L26).

Add backward-compatible, stable codes, sanitized field paths, retry guidance and
a request identifier. Keep the existing `error` field. For example, a field error
can identify `duration_seconds`; a conflict can identify `control_version_changed`
or `recovery_required`. These are proposed names, not current response fields.
Do not expose raw Pydantic inputs, SQL, credentials or arbitrary exception text.
Use structured domain errors rather than matching exception messages.

The [Swagger generator](../hummingbot/cli/swagger.py#L52) currently omits 400/415/500
responses that handlers can return and lacks error-response examples. Add these
alongside specific conflict/validation examples. If request ID or retry headers
are added, expose them to cross-origin callers and document them.
[MDN header exposure](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Access-Control-Expose-Headers).

Priority: before polishing interactive control forms.

### 3. Status needs an operator-facing interpretation and explicit identity

[`StatusResponse`](../hummingbot/cli/admin_models.py#L280) has useful separate
requested/observed sessions, owner health, incidents and market guards. It has no
top-level trading environment, deployed revision, complete active configuration,
or explicit action availability. Environment only appears in some account/funding
observations, which may not exist before the first connection.

An operator should see Demo/Live and the target pair before pressing Start. Add a
small, typed identity/configuration projection and action-block reasons to the
existing status/profile contracts. During revision overlap, distinguish the
responding process's metadata from the trading owner's published metadata.
Capabilities must reflect deployed markets: `/profiles` currently returns all
three `market_modes` unconditionally even if a catalogue configures only one.
Configured support is still distinct from exchange instrument/account readiness.

Current [/status](../hummingbot/cli/admin_api.py#L187) makes `running=false` and
`phase=owner_unavailable` when the owner expires, but passes through prior nested
market and drawdown observations. Do not show a nested `ready=true` as current
without checking top-level freshness. During switch, `markets` describe the
observed session and may lag the newly requested session. Add explicit observation
provenance/freshness, not a new trading-state machine in the browser.

`/profiles` is the deployed catalogue for new commands. The active session pins
its catalogue; the full original configuration is available through
`/sessions/{id}/events?event_type=session_created`. Do not show current deployed
YAML as if it necessarily describes a session resumed from an older revision.
Reuse that event/configuration first; a separate configuration subsystem is not
necessary.

Priority: before unattended or multi-operator UI use. State/action summaries are
advisory; the existing durable control/version/final-send checks remain authoritative.

### 4. Stop and HALT must have honest, usable recovery flows

The only control actions are `start`, `switch`, `stop`. There is no pause/resume,
cancel-one-order, flatten-position, risk-policy editing or HALT-reset API.
Do not display these as available actions.

A stop receipt's success checks strategy/clock shutdown in
[`record_observation`](../hummingbot/model/admin_store.py#L245); it is not a separate
certificate that account positions are zero or that every exchange order has
been independently reconciled. The UI should show remaining recorded orders,
held positions and any incomplete reconciliation after stop. Its label should be
“Stop MM”, not “Close all positions”.

HALT recovery currently requires diagnosis, account reconciliation and an explicit
operator-reviewed maintenance procedure, documented in
[runtime operations](../docs/postgres-runtime.md). An observational reconciliation
does not clear HALT and may be unavailable after trading clients have stopped.
Add a sanitized, read-only recovery summary/runbook link before considering a
write API. A one-click `clear_halt` would skip existing safeguards and is not
recommended. A fully browser-only recovery workflow is therefore not complete.

Priority: required UI semantics now; read-only recovery detail is a small follow-up.

### 5. Durable data exists, but history discovery is incomplete

There is `GET /commands/{id}` and session-event lookup by known session ID, but
no GET command list or session list. A fresh browser or another operator cannot
discover all previous receipts/sessions. Local browser storage alone is not a
durable operations history.

Reconciliation returns only the latest result. `/commands/{id}` deliberately
rejects reconciliation receipts. If another operator starts a check, an older
check has no dedicated GET-by-ID path, although retrying its identical POST can
recover its receipt when the key/body are retained.

Add bounded `GET /commands`, `GET /sessions`, and
`GET /reconciliation/{check_id}` using existing `admin_commands` and `mm_events`.
Reuse filter-bound keyset pagination and constant-query projections. A session
list must disclose missing legacy events; do not invent complete historical
status. No new scheduler, queue or event store is needed.

Priority: before daily handover/reload workflows are considered complete.

### 6. Open-order progress is missing; avoid introducing FE N+1 calls

[`OrderView`](../hummingbot/cli/admin_models.py#L338) exposes original recorded
amount/status, but no filled quantity, remaining quantity or fill-coverage label.
The list and detail responses are recorder views, not fresh OKX open-order lists.

A table showing progress must not fetch `/fills?order_id=...` once per visible
order. Add one grouped fill aggregate for the selected page, with Decimal outputs
and explicit provisional/incomplete semantics. Name any derived remainder as
recorded remainder; it is not independently confirmed exchange resting quantity.
Cancelled/failed orders can have an unfilled original remainder with zero resting
quantity. This is a potential FE integration N+1, not an observed existing browser
performance problem.

Priority: useful before adding order-progress bars; current MVP can show original
quantity/status and fetch fills only when opening an order drawer.

### 7. Charts must match the measurements already available

Daily fees, funding, volume and last-observed account balances exist. Historical
minute-by-minute quotes, market candles and target/reference curves do not.
[`_liquidity_view`](../hummingbot/core/mm_liquidity.py#L127) reads only the current
session checkpoint; an old `session_id` does not retrieve an archived curve.

Current liquidity reports sampled two-sided coverage and total remaining own
notional on each side. It does not certify depth within a chosen distance from
an independent market midpoint or satisfaction of an exchange MM mandate.
Do not relabel it as qualified target-depth uptime. Daily missing buckets are
gaps; lifetime controller PnL is not daily/session PnL.

Start with current liquidity cards, a labelled chart collected since the page
opened, and real daily series. Persist a bounded historical liquidity series only
if operators need post-incident replay; reuse the observer and PostgreSQL, with
retention/downsampling and no exchange requests on dashboard reads.

Priority: correct labels now; historical intraday storage can wait.

## Recommended implementation order

The findings above describe the original review baseline. Backend follow-up is
recorded in [the implementation report](admin-operations-api-implementation.md).
The current [ownership contract](../docs/admin-configuration-ownership.md)
supersedes the original request-override behavior. Unchecked external tasks are
not failures of the backend test suite.

- [x] Inventory the 19 registered operations, including three documentation routes.
- [x] Review control, freshness, accounting scopes, history and Cloud Run access.
- [x] Verify focused API/runtime tests and generated-contract drift.
- [x] Write the FE screen/interaction and API mapping guide.
- [ ] U0: choose the browser access route and verify it with trading stopped.
- [x] U1: Red/Green structured validation/conflict errors, request IDs and complete
  error documentation. Cover invalid fields, identical retries, changed keys,
  stale versions, HALT, cooldown, malformed JSON and unsupported content type.
- [x] U2: Red/Green status identity/freshness, configured capabilities and read-only
  recovery/action guidance. Cover cold start, standby/old owner, stopped, owner
  loss, DB 503, old observed session, restored pinned config and partial markets.
- [x] U3: Red/Green command/session lists and stable reconciliation lookup. Cover
  reload, two operators, pagination/filter changes, duplicate retries and legacy
  history gaps. Reuse existing tables; measure query count with real PostgreSQL.
- [ ] U4: build the five-screen FE MVP from the integration guide; generate client
  types from the checked OpenAPI. Test workflows with a real browser and local
  service/simulator, including auth/CORS, unknown POST outcome, superseded command,
  stale reporting, stop with held exposure, Decimal/UTC and partial reconciliation.
- [x] U5: if order progress is displayed, add the grouped recorded-fill projection
  and verify query counts are independent of visible order count. Intraday history
  remains optional until the operational need is established.
- [x] Run native/PG API regression and a targeted current-image load probe for changed backend code and a bounded
  multi-dashboard polling workload. Record image/source scope; this review is not
  a new full release/hour-soak acceptance result. See the implementation report.

Defer WebSockets/SSE, Redis, a new auth/RBAC platform, a general strategy editor,
arbitrary YAML writes, account-wide order controls and a profit-optimization UI.
An aggregate `/dashboard` endpoint is optional only after measurements show that
existing bounded reads need it. Keep separate widget freshness meanwhile.

## Verification performed at the original review baseline

- **81 tests passed**, covering admin HTTP/auth/docs/reference discovery,
  observations, daily cache, Decimal/UTC, Swagger, service availability,
  session transitions and liquidity. Six existing dependency-deprecation warnings.
- `python -m hummingbot.cli.swagger --check` passed.
- Additional local aiohttp probes used mocked storage with actual routing and
  middleware: invalid market/duration both returned generic 422; injected version
  and idempotency conflicts both returned generic 409; GET `/sessions` returned
  404, GET `/commands` 405, and a reconciliation ID through `/commands/{id}` 404.
  These probes verify the HTTP contract, not PostgreSQL conflict execution.
- Inspected the tracked file inventory: no dashboard source/build manifest found.

No new real-PostgreSQL run, browser UI test, load test, OKX request or Cloud Run
deployment was performed. Runtime files and generated API contract are unchanged.
