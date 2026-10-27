# Operations dashboard integration

This is the FE handoff for the existing LIFE MM admin service. The layout and
interaction design below are proposed; no dashboard application ships in this
repository. Current API behavior is documented in [admin API](admin-api.md) and
[OpenAPI](api/openapi.json). Missing capabilities and proposed backend work are
tracked in the [API/UX review](../reports/admin-api-dashboard-ux-review.md).

The primary questions are: is the service available, is it quoting, what is
blocking it, how much capital/exposure remains, and what should the operator do?
Profit is secondary. Keep account observations, bot performance and command
execution separate.

## Five screens

| Screen | Main content | Existing API, relative to `/api/v1/admin` |
|---|---|---|
| Overview | Service/owner health, requested vs observed session, countdown, current own quotes/spread/coverage, blockers, shared balances and held exposure | `/status`, `/statistics/liquidity`, `/balances`, `/positions` |
| Run configuration | Spot/perpetual/both; fixed profile/random pool; references/weights; duration; read-only budgets/guards; review of requested change | `/profiles`, `/reference-pairs`, `/commands`, `/commands/{id}` |
| Orders and fills | Separate market filters, open/closed orders, fills/fees, order detail drawer | `/orders`, `/orders/{id}`, `/fills` |
| Activity and diagnosis | Session transitions, startup waits/HALT, command outcome, reconciliation scope/differences | `/sessions/{id}/events`, `/status`, `/commands/{id}`, `/reconciliation`, `/reconciliation/check` |
| Daily statistics | Volume, fills, paid fees/rebates, observed funding, shared account balances by reporting date; optional controller performance | `/statistics/daily`, `/performance` |

The top bar stays visible: API connection, Demo/Live, bot/pair, service state,
session state, last observation, and **Stop MM**. Environment identity needs the
backend addition described in the review; until then use an explicit operator
connection label, not an environment guessed from a URL. Treat it as configured,
not exchange-verified. Shared account balances appear once. Spot and perpetual
each have their own quote/exposure cards.

An overview wireframe (not a shipped UI):

```text
Bot / pair    DEMO or LIVE    Service | Owner | Session    [Stop MM]
Last updated / data quality   Current profile   Time remaining

Attention: reason / affected market or side / next action / last retry

Spot liquidity                       Perpetual liquidity
Own bid / ask / spread               Own bid / ask / spread
Remaining own notional per side      Remaining own notional per side
Sampled two-sided coverage / gaps    Sampled two-sided coverage / gaps

Shared account cash/equity           Positions LONG / SHORT + margin
Spot BUY capacity / reservations     Bound drawdown / guard state

Latest command + session events      Daily fee/volume/funding summary
```

Use separate success, waiting, operator-attention, stale and unavailable states.
Show text/icon labels as well as color. Validation belongs next to the relevant
field; status changes should be announced without stealing focus or producing a
new toast every poll. These choices follow W3C's
[error identification](https://www.w3.org/WAI/WCAG22/Understanding/error-identification)
and [status-message guidance](https://www.w3.org/WAI/WCAG22/Understanding/status-messages.html).
Dense diagnostics can live in an expandable section; the reason and next action
remain visible in the main card.

## Service health is not a quoting indicator

Do not derive one green “Running” badge from HTTP 200, `/live`, `running`, a command
receipt or one market's `ready` field alone.

| Observed condition | Operator presentation |
|---|---|
| Connection/auth failure | Cannot observe service; retain prior values as stale, not zero |
| Status 503 with `service.state_source=process` | Process responding; durable control state unavailable; no fabricated version/session |
| `owner_available=false` | Owner unavailable; invalidate current-trading interpretation of nested saved observations |
| Requested and observed session IDs/generations differ | Applying change; show old observed and new requested configuration separately |
| Starting/preparing with no `started_at` | Preparing connections/data; session-duration countdown has not started |
| Healthy owner, matching session, `phase=running` | Strategy admitted; actual quote presence still comes from fresh liquidity observations |
| Fresh liquidity with one/no side | One-sided/no quotes observed; inspect per-market and BUY-only guards, balances and caps |
| Readiness wait/recovering guard | Service available; quotes constrained; show reason/retry/recovery timing |
| Stopped/expired | MM idle; continue displaying held exposure and reporting freshness |
| HALT/operator action required | Show reason and runbook; do not offer an unimplemented reset/resume action |

`status.service` carries retry/incident details. Quote guards, spread fallback,
drawdown and spot outflow have separate effects. For example, a spot BUY denial
does not imply funded SELL or perpetual admission is blocked. A disabled optional
policy is labelled **Disabled**, not **Healthy** or **Failed**.

Keep a fallback for unknown phase/reason codes: display the code with a neutral
explanation and observation time instead of crashing or guessing that it is safe.
Backend action guidance is advisory; submitting a command still requires the
normal version/session checks.

Use `server_time` and absolute expiry for a local countdown, periodically resynced;
do not require one HTTP call per displayed second. If the latest status is stale,
label the countdown as based on last observation. Countdown reaching zero does
not prove that shutdown/cancellations completed.

## Start and switch workflow

1. Read status and deployed profiles. Preserve the current `control_version` and
   session ID for submission. Refresh when the review screen opens; keep draft
   edits intact if another operator changes the session.
2. Select Spot, Perpetual or Both from configured support. They share one account;
   budgets shown for markets are allocations, not separate wallets. Until the
   capabilities fix, intersect `market_modes` with actual catalogue templates.
3. Select **One profile** or **Random profiles**. Random is a selection method,
   not a fourth strategy. Display its pool, equal selection probability and step
   duration; consecutive steps may select the same profile.
4. For `follow_market`, provide searchable reference rows and percentage weights
   totalling 100%, serialized as Decimal strings totalling `1`. For `follow_token`,
   use one supported reference at weight `"1"`. Sideways has no reference basket.
   Random uses deployed per-profile baskets, without a request-wide override.
   Discovery freshness is metadata freshness, not proof of current price readiness.
5. Show human-friendly duration inputs bounded by the catalogue. Resolve defaults
   in the review screen and submit explicit seconds. Display budgets, leverage,
   guards and target pair read-only: current commands cannot edit these settings.
6. Show the exact requested mode/profile/references/duration and any difference
   from the active session. Switch creates a replacement session; it is not a
   live patch retaining the previous timer. Held exposure is not automatically
   flattened. For an active session, use `switch`, not another `start`.
7. Generate one `Idempotency-Key` per intentional operation and store its immutable
   payload and returned receipt ID. POST once; poll that receipt until final.

Generate TypeScript types from the checked OpenAPI; use one small client wrapper
for authentication, errors and retries. Do not auto-generate a giant form exposing
every upstream controller field. Keep `expected_version`, IDs and keys out of
normal editable form controls.

`202` means accepted for processing, not that quoting started. This is also the
standard HTTP meaning of [202 Accepted](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Status/202).
Show accepted/applying progress with the current readiness reason, then distinguish
succeeded, failed and superseded. A succeeded command is a past outcome, not a
permanent claim that the bot is currently quoting.

On a network timeout, the operation's outcome is **unknown**. If the receipt ID is
known, poll it; otherwise retry the identical POST with the same key and body.
Never replace the key or silently refresh `expected_version` on that retry. A new
operator decision after a conflict is a new operation. Generic 409 currently
requires refreshing status and surfacing uncertainty; the structured-error change
will support more specific guidance.

After a page reload, recover a retained receipt before creating another operation.
Keep only non-secret draft/receipt metadata locally, scoped by API origin and bot;
clear it when changing connections. Server-side command/session lists remain
needed for reliable cross-browser history. Do not block Stop merely because a
start/switch is pending: refresh state and allow an explicit stop of the current
session, which can supersede the pending command through existing control rules.

## Stop, diagnostics and reconciliation

Use a short, explicit confirmation showing the affected session/markets and held
exposure. State **“Stop MM; positions remain open”**. Keep this action readily
accessible; avoid nested modal confirmations. If a stop request fails or its
outcome is unknown, retain that state rather than showing a success toast.

After a successful stop, keep the positions/orders panels and their timestamps.
`/orders?state=open` is recorder history, not an exchange-wide live snapshot.
Reconciliation is a separate observational check and may be unavailable when no
appropriate connector client remains. Neither it nor Stop automatically closes
positions or clears recovery HALT. Link the
[operator recovery procedure](postgres-runtime.md) for unresolved state.

Show reconciliation execution status and report state separately. A succeeded
receipt with `report.state=mismatch` is a completed check with discrepancies.
Show checked markets, time scope, counts, incompleteness and differences. Match
the returned check ID to the one requested; `/reconciliation` is currently latest
only. Do not present “Account reconciled” when only a bounded spot scope matched.
Offer an explicit Check button; never trigger exchange checks on each dashboard
poll. Respect the one-running-check/30-second spacing policy.

All API requests currently use the configured Basic Auth identity. Session events
record that username where available, not individual humans sharing the login.
Do not claim per-person audit or read-only RBAC that the backend does not implement.

## Financial and chart semantics

- Preserve Decimal strings for calculations and payloads. Round only for display;
  use an appropriate decimal library, not JavaScript floating-point sums for money.
  Chart plotting may use display numbers without changing authoritative amounts.
- Show account cash, available cash, equity and available equity separately; never
  add shared USDT collateral from Spot and Perpetual twice. Perpetual position
  amount is exposure, not owned spot LIFE. Show LONG and SHORT separately.
- Unknown, not observed, stale and not applicable are different from zero. Display
  gaps in chart lines instead of synthesizing zeros or interpolating across outages.
- Daily date filters use `REPORTING_TIMEZONE`, with inclusive `from` and exclusive
  `to`. Keep the reporting timezone visible. Event timestamps are UTC instants,
  rendered in the selected display timezone; changing display timezone does not
  relabel persisted daily balance buckets.
- Daily balances are last observed for a day, not exact midnight NAV. Fees retain
  original currencies/conversion coverage; funding has its own coverage and may
  be null. Controller performance covers stored V2 history plus active exposure;
  it is not an exact account statement or session/day profit calculation.
- Show daily buy/sell volume and paid fees/rebates first. Funding is a separate
  perpetual series. Put controller PnL in a secondary panel with its scope label.
- Current liquidity is sampled own-quote coverage, with unknown time explicit.
  Bid/ask notional totals are not guaranteed depth within a chosen price band.
  Historical OHLC or target/reference curves are not exposed by this API. A small
  client-collected live series must say “since this page opened” and break at
  gaps/session changes; do not imply that it survives reload or is market history.

## Polling and availability

These are initial FE scheduling suggestions, not measured optimal intervals:

| Read | Suggested initial cadence |
|---|---|
| Status + current liquidity | Every 2 seconds on the active overview |
| Pending command receipt | Every 1 second initially, back off if still applying |
| Balances/positions | Every 5 seconds while visible |
| First page of orders/fills/events | Every 5–10 seconds while visible; older pages on demand |
| Daily statistics/controller performance | Every 15–30 seconds while visible; refresh on filter change |
| Profiles/schema | On connection/revision change or explicit refresh |
| Reference search | Debounce input; cancel obsolete requests and paginate |

Share requests across widgets, with at most one in flight for each query key.
Pause nonessential hidden-tab polling, add jitter/backoff on failures and refresh
status on foreground/reconnect. Preserve each widget's last-good data and original
observation time; a fresh HTTP response or cached daily response is not necessarily
a fresh account observation. Ignore responses from obsolete filters/connections.

Do not issue per-row balance, position, fill or reconciliation calls. Open one
order drawer on demand. Cursor changes must reset when filters change. Charts
and history failures should not prevent Stop/status controls from rendering.
The service's optional reporting remains independent of trading admission.

## Connection and browser checks

Retain the current Basic Auth contract and use TLS for the hosted dashboard.
Operator credentials stay out of FE builds, URLs, logs and persistent browser
storage. The direct browser integration uses an explicit Authorization header and
`credentials: "omit"` as described in [admin API](admin-api.md); a gateway route
must specify its own browser authentication and forwarding behavior.

First verify the network/IAM route described in the review. `ADMIN_CORS_ORIGINS=*`
alone does not make internal ingress accessible. Do not add `X-Requested-With`
or other custom request headers without corresponding CORS support; the current
preflight allowlist is Authorization, Content-Type and Idempotency-Key. On 401,
stop automatic command submission and ask the operator to reconnect. A browser
network/CORS failure is not proof of an incorrect password or stopped bot.

Before calling the FE ready, exercise the actual browser against local service
fixtures: invalid form fields, wrong auth, preflight, duplicate clicks, timeout
after acceptance, refresh with a pending command, two operators, stale owner,
503 with process diagnostics, random transition, expiry, stop with held exposure,
partial reconciliation and missing daily data. Then verify the intended hosted
route with trading stopped. This repository's existing Python API tests do not
constitute that browser acceptance.
