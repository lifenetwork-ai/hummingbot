# Admin recovery API — TDD implementation

Operator decisions: admin only; preview then explicit confirmation; verified
aggregate repair is permitted; success leaves the bot stopped. No live execution
is authorized by this implementation task. Start remains a separate action.

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
- [ ] Separate review/deployment and actual-Demo recovery acceptance.


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
- Actual Cloud/Demo recovery, packaging and connected acceptance remain separate.
  No live financial/HALT state was changed during this implementation.
