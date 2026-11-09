# Admin recovery API — TDD implementation

Operator decisions: admin only; preview then explicit confirmation; verified
aggregate repair is permitted; success leaves the bot stopped. The initial
source-implementation scope authorized no live writes. The later, separately
operator-authorized Cloud acceptance is recorded below. Start remains separate.

## Contract and boundaries

- `GET /recovery`: availability, blockers and latest operation.
- `POST /recovery/check`: expected control version and Idempotency-Key; asynchronous
  receipt. Requires stopped/acknowledged intent and a healthy physical owner.
- `POST /recovery/reset`: expected version, successful check ID and confirmation
  token; asynchronous fresh revalidation followed by one atomic repair/audit.
- `GET /recovery/operations/{id}`: bounded public progress/result, no secrets.
- Reuse `admin_commands` receipts, database workers and owner fences. API process
  never queries the exchange or repairs positions. Engine handles a bounded,
  GET-only check with independent readers while its trading core is stopped.
- Scope: dedicated bound OKX account, spot and hedge/cross perpetual, existing
  unambiguous controller position rows. No exchange create/cancel/close mutations.
- Verify bounded complete local history and REST order/fill identities, prices,
  quantities and original fees; reject missing pages or history outside verified
  venue coverage. Verify full account open orders and perpetual exposure.
- Reuse native PositionHold replay. A stored aggregate must match a known prefix
  of its immutable fills before extension; unknown baseline/controller attribution
  or unsupported fee conversion blocks. Preserve fill/order/funding history.
- Block risk stops, malformed risk checkpoints, unknown halt reasons, credential
  operations, pending intents/account mutations, active runtime, unavailable owner,
  changed bindings/roles/versions, stale proof, external orders or unmatched exposure.
- Confirmation binds server-side preview, physical owner/generation, control
  version, credential binding/version and local ledger fingerprint. Revalidate
  immediately before atomic commit; retry cannot replay a completed repair.
- Only the incident HALT/reason and proven technical runtime markers may clear.
  Risk policy, peaks, stop latches, pauses, credentials and financial history remain.
  Store aggregate before/after images and actor/request/operation audit atomically.
- Concurrent Start/Switch/credential mutation is blocked during recovery. Owner
  loss/crash leaves HALT; a new owner requires a new check, not an old proof.
- English UI/error/progress strings. Explicit blocked reasons, next action,
  required confirmation and stopped outcome. No generic force/bypass parameter.

## Checklist

- [x] Red/Green native prefix replay, stable fingerprints, blockers and no data invention.
- [x] Red/Green real PostgreSQL receipts, idempotency, admin reauthorization,
  freshness, owner/version races, transaction rollback and audit preservation.
- [x] Read-only bounded exchange verifier; complete history/identity/fees/exposure
  proof, account binding, page gaps, external orders and network failures.
- [x] Engine coordinator: stopped runtime, bounded work, durable progress,
  cancellation/ownership loss and crash recovery; no trading on success.
- [x] API authentication/authorization, request validation, Swagger schemas and
  English operator/status contract; role read/write cannot recover.
- [x] Offline real-process review/confirm, tampered confirmation, repeat confirm
  and restart; local KMS/fake exchange only. Expiry, missing evidence, transaction
  failure and cancellation are covered separately at PG/native boundaries.
- [x] Broader native/PostgreSQL checks, docs/FE updates and commit.
- [x] Separate reviewed deployment and actual-Demo recovery workflow acceptance
  on revision65. Two-sided liquidity and real GCP KMS qualification are separate.


## Verification evidence

- Initial missing-feature native/PG/API Red results preceded implementation.
  A subsequent hedge-close replay regression reproduced an unsafe leg reversal
  (1 Red); the recovery adapter now rejects a CLOSE larger than its proven leg.
  Native PositionHold behavior is unchanged.
- Native regression: **1,316 passed**, including the CLI suite and the then-current
  replay/venue contracts. The earlier attempt remains recorded as **1,309 passed /
  5 failed**: four explicit role-matrix expectations needed the new admin-only
  routes, and a cleanup test's abbreviated owner row lacked recovery_state.
- PostgreSQL regression: **44 passed**, covering receipts, real service/KMS,
  account/command authorization, availability and active-owner bootstrap.
- Independent fresh causal checks: **4 passed**, including native-process
  SIGKILL/stale position rejection, successful matching checkpoint, migration
  handoff and the new supervised recovery flow. These use disposable PostgreSQL;
  external network access is disabled by the process fixture.
- Bulk snapshot/replay run on bounded workers. Physical owner checks and final
  short transactions remain on the ownership event-loop thread to avoid sharing
  its SQLAlchemy connection concurrently. Recovery only runs with trading stopped.
- Final focused contracts: **61 passed**; final recovery PostgreSQL/real-process
  rerun: **14 passed**; typed API/Swagger contracts: **24 passed**. These overlap
  the broader runs and are not summed as distinct test totals. Pre-commit passes.
- Packaged OpenAPI is regenerated from actual routes/models. No new schema
  migration, authentication bypass, generic force field or exchange write exists.
- During source-only verification, Cloud/Demo deployment and connected acceptance
  remained separate and no financial/HALT state was changed. The later revision65
  section records the separately authorized live acceptance.


## Connected acceptance follow-up — 2026-11-10

- [x] Verify revision64 packages the expected schema/source; Google admin identity
  exposes manage_recovery; stop acknowledgement17/17.
- [x] Preserve a read-only DB backup and current 431-order/101-fill/3-position
  snapshot before requesting recovery.
- [x] Trace the safely blocked Cloud check to missing create-event side on19
  confirmed unfilled canceled orders. TDD3 Red +1 passing negative control.
- [x] Trace exact timestamp comparison to native whole-second event precision;
  TDD3 Red +2 passing out-of-bound controls. Do not rewrite source timestamps.
- [x] Corrected source independently verifies all431 orders,101 original-fee
  fills and both perpetual legs using GET only; no pending orders.
- [x] Deploy follow-up source on revision65, rerun actual Cloud check/confirm,
  verify exact one-row repair, atomic audit and stopped outcome; duplicate confirm
  returns the same receipt without another version increment.
- [x] Independently compare all financial history, bindings and protected risk
  checkpoints before a separate Google Start (control19).
- [x] Complete the30-minute running-Demo observation and final readback:360
  samples, no HTTP/read failure, no HALT/child replacement, final running19/19.
  Current per-side guards prevent a claim of two-sided market coverage.

At the end of revision64 acceptance, the live failed-check receipt remained
failed, with only an explicit Google Stop applied. Local proof was not relabeled
successful Cloud recovery. The revision65 section below records the later,
separately successful actual Cloud check/reset and its exact financial change.


Follow-up validation: native CLI/recovery regression **1,333 passed**; recovery
PostgreSQL/service contracts **16 passed**. Two independent full read-only venue
proofs pass on the existing Demo account; the measured repeat completes in
**34.862 seconds**, within the unchanged120-second recovery budget. Archive
listing validates the retained backup includes bot_state/orders/trade_fills/
positions. Those source/read-only proofs preceded live confirmation; revision65
acceptance below records the later Cloud outcome.


## Revision65 connected recovery

The previous revision64 failed check remains recorded. On image `c98b08a6f…`,
check `dc5e206a-e2af-4756-8720-19663d0c6685` succeeds and reset
`98e9d419-49d5-4c40-b213-617eb33249cd` atomically repairs the reviewed spot row.
Control advances17→18 and remains stopped; duplicate confirmation does not advance
it again. Audit events49–51 identify the authorized Google admin. Independent DB
readback proves all431 orders/101 fills, both perpetual rows, credential bindings
and risk checkpoints unchanged beyond that exact repair. A separate Google Start
succeeds at control19, session `bb70e9f0-1ffd-4b81-946c-5a785043fbd9`.

This supersedes the earlier "live confirmation pending" state; its historical
failed receipt is not relabeled successful. The running observation is tracked
separately from recovery success and from two-sided quote availability.


## Completed running observation

2026-11-10 local01:15:44–01:45:45: **360 samples over1,801.378 seconds**,
all under Google read authorization. No failed read, missing/stale owner
observation, durable HALT or child replacement is observed. Physical ownership
remains generation57; final control/ack19/19 and trading stays running. Final
startup/live/ready endpoints return200; Swagger matches the shipped contract.

One sample is a clearly reported readiness wait (`guard_recovering` on spot,
`session_not_admitted` on perpetual); it self-recovers. The initial analysis
script classified that wait as unknown until the actual reason codes were read.
This classification correction changes no bot code. There are359 policy-constrained
samples per market and one readiness-guard wait, with no two-way-eligible samples;
**two-sided quote SLO is not qualified**. Spot SELL orders are observed on349
samples, with10 permitted/in-progress samples and one readiness wait. Quotes
continue to cancel/refresh; do not equate process uptime with continuous orders.

Independent final readback matches **103 fills (74 spot,29 perpetual)** by exact
identity/quantity/price/original fee; no duplicate fill. There are **two new natural
spot fills** after recovery. Perpetual exposure remains unchanged. No forced
fills, cap increases, position closures or risk-policy resets are used. The
running held-position checkpoint is not represented as a stopped recovery proof.

The one300ms-budget probe timeout occurs during Start before this observation;
exact server/recovery latency is not proven. Logs after the session starts show
no ERROR/restart;32 warning records in the captured interval are perpetual
`mm_no_quotes_warning` under the existing inventory cap. Local helper teardown
leaves the Cloud session running; no final Stop/Revoke is requested.
