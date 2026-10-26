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
