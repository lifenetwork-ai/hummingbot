# Session history and sampled liquidity

Both endpoints use the existing admin Basic Auth and handler-generated OpenAPI.

- `GET /api/v1/admin/sessions/{session_id}/events`: newest-first keyset pagination, `limit` 1–500, optional `event_type` and `source`. Cursors bind all filters and bot/session identity. `history_complete` indicates that a session-start configuration was recorded; it does not imply exact historical market coverage. History begins at migration 016.
- `GET /api/v1/admin/statistics/liquidity`: current session observations, optional `session_id` and `market=all|spot|perpetual`. Historical sessions without a current checkpoint return unavailable. Use existing fills/fees/daily endpoints for financial history.

Events include immutable resolved configuration at creation, profile/step transitions,
stop/expiry and observed guard changes. Control transitions share their original
transaction and fencing. Duplicate command retries and owner takeover do not create
duplicate transitions. Authenticated username or scheduler/observer source is
recorded; credentials are never part of the event payload. Identical guard ticks
create no event. Global HALT remains visible after controllers have stopped.

Liquidity uses existing exchange-acknowledged limit orders in OPEN/PARTIALLY_FILLED
state. Remaining quantities determine notionals; pending creation/cancellation and
unacknowledged orders are excluded. Counters use consecutive fresh owner samples
within five seconds and persistent bid/ask identities for two-sided coverage.
They measure sampled observations, not guaranteed continuous exchange availability.
Zero quotes on a valid sample means zero two-sided coverage. Feed gaps, restart,
owner or profile generation changes are unknown intervals. Elapsed time includes
unknown intervals, so gaps cannot inflate the reported uptime fraction. Stop/expiry
freezes elapsed time. Only spot/perpetual counters are retained, without book history.

Stale owners or observations hide quote values and extend unknown time; optional
projection errors report unavailable and do not change trading intent. API reads
reuse PostgreSQL/status snapshots and issue no exchange calls. Events take two
bounded queries per page, independent of result count; indexes cover keyset and
single-filter pagination. Liquidity takes one existing state read. There is no
new worker, external cache, ledger or exchange client.

## Working orders, fills and missing-order warnings

A quote is a resting buy or sell limit order placed by the bot. A fill is an
execution against an order. Working orders can provide liquidity without fills.
The operator UI should say **Working orders**, rather than require understanding
of the word quote. API machine fields retain `quote_activity`.

`GET /status.quote_activity` and each `/statistics/liquidity.markets[market].quote_activity`
contain the same sampled market observations. GET reads add no exchange requests
or extra state queries. Spot and perpetual have separate activity and BUY/SELL
reasons; the top operator summary reflects missing or one-sided orders separately
from the engine's `running`/`phase` fields.

| State | Meaning for operators |
|---|---|
| `orders_working` | Acknowledged working buy and sell limit orders are present |
| `one_sided` | Working orders exist on only one side; inspect the other side |
| `no_quotes` | A fresh sample has no acknowledged working limit orders |
| `unavailable` | Current orders cannot be confirmed; retain last-good values as stale |
| `idle` | Session is stopping, stopped, expired or not yet actively admitted |

`fill_observation_basis=current_working_orders` is deliberately limited to the
current working orders. `no_fills_on_working_orders` means none of those orders
has an observed executed base amount. It does **not** mean there have been no
earlier session/account fills: filled/cancelled orders may already have left the
working tracker. Use `/fills` for recorded history. A no-fill observation is not
a service failure and does not trigger a missing-order warning.

Each side has its observed `order_count`, English detail, reason codes and
`proposed_count` from the last actual controller evaluation. A proposal is not
exchange acceptance. Missing-side reasons include insufficient quote/base funds,
inventory/allocation constraints, spending-guard denial, size/price/spread checks,
active executors and cooldown. `spot_outflow_blocked` is a guard denial, not proof
that the spending ceiling was reached: inspect `/status.spot_outflow`, including
coverage, pending terminal orders and the last BUY denial, for its specific cause.

Proposal diagnostics expire after five seconds. Their original UTC
`proposal_observed_at` stays visible; an outdated or absent diagnosis becomes
`quote_reason_unavailable` instead of repeating an old balance denial as current.
Fresh controller readiness reasons remain useful during guard/data waits.

`MM_NO_QUOTE_WARNING_SECONDS` is a process environment setting, default **120**;
valid values are `0` (disable warnings) or 5–86,400 seconds. It cannot be changed
by the command API. It controls monitoring only. During a running, started session,
`no_quote_seconds` accumulates only consecutive valid samples of complete order
absence within five seconds, for the same owner and session generation. Any
working side, unknown sample, gap, restart or generation change resets that
continuous window. Stop/expiry clears a current warning. API reads do not advance
the sampled duration. A brief refresh gap can show `no_quotes` before it warrants
a warning; do not show an error toast on each poll.

At the threshold, `warning_active=true` and the existing service logger emits
**WARNING** `mm_no_quotes_warning`, with bot/session identity and a market-specific
`scope`. Repeated warnings are limited to once per minute per market. Clearing the
warning emits **INFO** `mm_no_quotes_warning_cleared`; its `reason_code` may be
`unavailable` or `idle`, so this log alone does not prove that orders returned.
These are API/log signals, not email/Slack notifications or automatic repair.

All diagnostic paths reuse existing quote decisions and the order tracker. They
do not restart sessions, reset deadlines/HALT, change limits or submit trades.
