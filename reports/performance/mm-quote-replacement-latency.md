# Quote replacement and expired-order recovery — 2026-11-12

Status: revision111 resolves the50037 block and is trading continuously. Its
completed30-minute quote window fails99%. A separate post-only CLOSE retry bug
has a tested local correction, not yet deployed. See the
[connected acceptance and follow-up](../demo/cloudrun-quote-replacement-acceptance.md).
The local and historical artifact results below retain their own scope.

## Changes and their boundaries

- Managed perpetual maker CLOSE executors wake on the matching cancel/completion
  event instead of waiting out the five-second shutdown sleep and another tick.
  Duplicate Stop is idempotent. Original and late partial fills remain retained.
  Native terminal proof and financial commit still precede reuse of capacity.
- Managed LIFE controllers wake on executor publications. Publications coalesce,
  a blocked action-queue handoff cannot erase a newer update, and periodic
  freshness checks remain. Legacy timing, price guards and risk limits stay intact.
- The opt-in offline activity collector measures terminal callbacks, checkpoints
  and stop/prepare/start stages. Metadata errors cannot suppress native callbacks.
  A new parallel status/liquidity reader preserves per-read timing, observation
  age, failures and skew; it does not relax quote qualification.
- Both OKX connectors recognize an exact50037 rejection as well as50036. Cold or
  warm managed recovery may consume the persisted HTTP200 receipt only when the
  client ID matches, the venue ID is empty, current account/environment bindings
  match and no local fill/acknowledgement contradicts it. Intent retirement,
  saved-tracker removal and a runtime audit commit in one transaction.
- Unresolved51603/transport outcomes still pause submission. After five minutes,
  startup and ordinary recovery request investigation. The API operator panel
  returns `needs_attention` / `contact_support`, without inventing a HALT or
  granting a reset. Elapsed time never proves order absence.

The official [OKX error-code reference](https://www.okx.com/docs-v5/en/#error-code)
identifies50037 as request expiry. A51603 read alone is not that evidence.

## Why revision110 was paused

Read-only observation at04:28–04:30UTC and a later status check finds owner483,
control84, revision110 in `bootstrapping_recovery`. A Spot SELL creation has a
saved matching50037 receipt with no venue ID or local fill. The deployed
classifier handles50036 only, leaving that intent uncertain. Startup retries
51603 indefinitely and does not raise its operator flag. This is a recovery
classification/visibility defect, not evidence of a live order on OKX.

The earlier close-first acceptance remains historical evidence:83 native LONG
closing fills and two cold handoffs passed; its regular thirty-minute quote
window failed99% (Spot95.32%, perpetual92.65%). Those numbers are not reassigned
to this patch. The previous twenty last-fill→next-venue-creation pairs had median
1.1915s and maximum5.508s; they did not prove a unique bottleneck or continuous
quote downtime. A new deployed measurement is required for an improvement claim.

## Validation

- Cancel timing: eight RED cases before the fix, covering both sides, late partial
  fills and ACK-before/during-wait. They pass with a250ms event-loop deadline.
  Additional cases cover duplicate/foreign events, native completion, coalescing,
  concurrent publication and real bounded-queue handoff.
- Expiry: four RED50037 classifier/connector cases and nine initial PostgreSQL
  recovery/escalation cases reproduced the defect. The final17-case PostgreSQL
  selection passes, including the actual Spot transport rejection path for both codes,
  successor replay, rollback, idempotency and conflicting financial evidence.
- Main regression:3,483 tests and16 subtests pass. The final trace-metadata suite
  also passes34 tests after its later diagnostic-only correction.
- Complete PostgreSQL regression:738 pass in21m27s. The later17-case expiry
  selection additionally covers the two final native-transport cases.
- The300-second probe image `sha256:2e9245ece3192cd5b4d67de8d740eb970e490e726869687759f235a431d54dae`
  matches the11 runtime files at that run. A later presentation-only correction
  preserves explicit cold Start from stopped while attention is requested; its
  RED case and16 operator/API regression cases pass. The trading-engine sources
  are unchanged; the final image is checked separately below. It is based on the verified revision109
  runtime; this does not identify it as a new Cloud Run deployment.
- Final image `sha256:5c574a65373b2ae29a66c40c4937dd1c6f2b8579de9636774c15c2a84dec6325`
  matches all11 final runtime files. Compared with the300-second probe image,
  only `hummingbot/cli/admin_operations.py` changes: stopped cold Start remains
  available to initialize proof while the attention panel still requests support.
  Its runtime smoke and packaged automatic-recovery E2E pass: lost accepted ACK
  resolves in2.97s, quotes return, the owner is preserved and duplicate submissions
  remain zero. This is an isolated-network simulator, not a Cloud deployment.
- Packaged300-second activity probe passes:20 reads/s,100.01 market updates/s,
  1 fill/s, API read p95=59.33ms and p99=532.06ms, no budget violations or
  unexpected HTTP errors. All300 native fills/fees and positions reconcile;
  four actual closing fills cover both hedge legs. Managed-key disconnect
  cleanup succeeds for both connectors and verifies flat exposure. This is an
  isolated simulator with local encryption/signed offline identities, not OKX
  Demo or real Google/KMS acceptance. The diagnostic profile does not close a
  full load/soak gate or the connected thirty-minute quote target.
- Four matched executor identities connect terminal callbacks to committed
  financial checkpoints in386–430ms. The next perpetual controller iteration
  starts10–40ms after those commits. These are four offline observations, not
  a production p99 or an end-to-end venue replacement guarantee.
- The earlier120-second probe is retained as failed: only the LONG close filled
  before the cutoff; the SHORT close appeared near the end after its opening
  quote's second sixty-second refresh. The longer probe keeps the same policies
  and mandatory two-leg fill check.

The first packaged diagnostic failed startup because new trace metadata named
`phase` collided with the trace envelope. That test-only defect was reproduced
by a unit test and fixed; its failed evidence is retained and not counted as a
pass. It does not describe the currently deployed Cloud Run runtime.

## Evidence

Local evidence is under `build/mm-quote-replacement-20261112/`, with source hashes
in `source-identity.json` and private connected observations in
`cloud-observation/`. Diagnostic attempts are retained separately:
`build/mm-load/20261112-115044-eb18db/` (failed wrapper) and
`build/mm-load/20261112-115453-1939e2/` (corrected wrapper, incomplete two-leg
exercise), followed by `build/mm-load/20261112-120013-9f630c/` (300-second
probe, both-leg closes and cleanup pass).

The source/evidence summary is `build/mm-quote-replacement-20261112/qualification.json`.
Final-image E2E evidence is `build/e2e-offline/20261112-121246-e9c730/`.
Test logs are retained at `/tmp/hbot-quote-expiry-regression-final.log`,
`/tmp/hbot-expiry-recovery-complete-r2.log`,
`/tmp/hbot-quote-expiry-postgres-final.log` and
`/tmp/hbot-profile-trace-field-green.log`. The pre-commit checks pass.
