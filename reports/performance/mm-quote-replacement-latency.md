# Quote replacement and expired-order recovery — 2026-11-12

Connected status: revision118/1e5d15bda completes897 samples with quality pass
and the original99% gate failed (Spot94.50%, perpetual94.03%). No ERROR/restart/
shutdown timeout is observed; probe and API latency tails remain. The native
position-poll scheduling correction below is locally qualified, not deployed.

Earlier: revision112 includes the post-only CLOSE correction and native readback
confirms fresh-price replacement. Its completed901-sample window still fails99%
(Spot86.84%, perpetual94.74%). Probe causality remains open. See the
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

## Terminal publication and production diagnostics follow-up

This patch is locally qualified and is now deployed on revision116 through the
squashed commit349026ff4 and merge2e06d191a. Diagnostics are explicitly enabled.
Its initial check encountered a profile-switch shutdown/recovery incident;
see the [connected investigation](../demo/cloudrun-quote-replacement-acceptance.md#revision116-diagnostics-and-drained-shutdown-fence).
This does not replace revision112's failed connected quote target or establish
a unique production probe root cause. The additional drained-fence correction
is now deployed as5c36bb7bd on revision117: the old fence clears without manual
mutation and the next scheduled profile change returns to quotes. This short
connected check retains its unavailable samples and does not close the30-minute
gate; see the [revision117 record](../demo/cloudrun-quote-replacement-acceptance.md#revision117-verified-fence-recovery-and-next-profile-change).

A managed executor's terminal notification now schedules the existing strategy
report path after a50ms coalescing window, without waiting for another engine
tick. Native financial/checkpoint application still precedes controller
publication. Failed application retains the reservation and uses the ordinary
report retry. A queued callback rechecks session/generation, excludes inherited
retired-order/cleanup authority and is consumed by an intervening report or
canceled on shutdown. No independent writer or submission authority is added.

`ENGINE_DIAGNOSTICS_ENABLED` defaults to false; explicitly set it true on a new
Cloud Run revision to collect production evidence. The optimization works with
the flag off. Bounded WARNING packets correlate heartbeat, controller/report,
submission/cancel/proof/terminal, checkpoint, ownership and SQL cursor stages.
They contain hashed identities and sanitized stack summaries, without SQL,
parameters, credentials or locals. Network wait and loop-stall warnings have
separate budgets. Capture races and lifetime maxima remain explicit limitations;
SQL cursor timings exclude connection checkout and COMMIT. Telemetry failure
cannot replace a business return/exception or prevent healthy engine startup.
See [deployment settings](../../docs/cloudrun.md#opt-in-engine-timing-diagnostics).

Qualification of the final source:

- Native regression:3,523 tests plus16 subtests pass. Related PostgreSQL selection:
  62 pass, including two real diagnostic/ownership/transaction tests; those two
  also pass a final rerun and are not counted twice. The full PostgreSQL suite
  was not repeated in this follow-up. RED evidence includes terminal publication,
  retired context inheritance, healthy-heartbeat slow stages and constructor
  failure; it is retained alongside the passing runs.
- Final amd64 image
  `sha256:1840a4093123fae8df103b7d5b1b561a1dd44aec6654ddabc9ac6f0986f56219`
  matches all12 changed runtime source files. Packaged LONG and SHORT cases pass
  with diagnostics enabled: passive repricing after native post-only cancellation,
  partial application once, lost ACK without duplicate POST, cold restart, fee
  outage/recovery and full close returning to OPEN. Evidence:
  `build/e2e-offline/20261112-152719-28e65c/` and
  `build/e2e-offline/20261112-152719-47adbf/`; registry manifest
  `sha256:d9a2cd6ada9379819afdca07d0b22b98f80df751fab326deb2f33ed46236ed55`.
- The same final image passes a300-second diagnostic load with2CPU/1GiB and
  100,000 historical orders/fills. All6,000 primary and600 auxiliary reads
  succeed,20 primary reads/s,100.00 market updates/s and1 fill/s. Primary read
  p95=68.46ms/p99=564.64ms; peak reported working-set RSS=776,470,528 bytes.
  All300 measured native fills match the recorder, positions match, two funding
  bills are identified and four closing fills cover both hedge legs. Cleanup
  adds its separate fill, deduplicates301 total fills/fees and verifies flat
  exposure. Budget violations and unexpected HTTP errors are zero. Evidence:
  `build/mm-load/20261112-152927-1c7d3a/`.
- Diagnostic packets are emitted by the packaged engine, including SQL hooks.
  Across the complete load container lifetime, including preparation/cleanup,
  there are seven stall/recovery pairs and seven slow-stage packets. Both hedge
  E2Es each record two pairs and two slow-stage packets. Largest decoded packet
  is22,898 bytes, within24KB. These are local observations under amd64 emulation
  on arm64, not Cloud probe counts, a production latency comparison or evidence
  of continuous quoting during each gap.
- A synthetic empty-call microbenchmark reports an approximately128ns disabled
  wrapper increment and7.88microseconds total enabled call time. This establishes
  only instrumentation cost in that microbenchmark, not MM latency or a p99 gate.

The qualification index, runtime source identity and overhead measurements are
under `build/mm-runtime-diagnostics-20261112/`. This uses an isolated exchange,
signed offline Google identities and local key encryption. It does not close a
full soak, actual Google/GCP KMS, or the original connected thirty-minute99%
quote gate. Diagnostics and the additional drained-fence correction are deployed
on revision117. Next: investigate the remaining shutdown/probe timing and the
retained liquidity test failure, then repeat the unchanged connected target.

## Shutdown proof sharing, freshness fixtures and executor identity

This additional implementation is local; deployed revision117 still runs5c36bb7bd.
Its unchanged30-minute baseline is recorded in
[connected acceptance](../demo/cloudrun-quote-replacement-acceptance.md#revision117-thirty-minute-baseline-during-local-follow-up).
It does not qualify this new local image or close the99% target.

Three concrete findings drive this patch:

- The HTTP liquidity fixture publishes once before server setup and mixes host
  Python time with Docker PostgreSQL time. Controlled delayed setup reproduces
  unavailable with heartbeat6.004778s and snapshot6.004782s old, matching the
  API's correct5s rejection. Fixtures now publish after setup/before reads using
  DB time. Old/future heartbeat/snapshot boundary cases and credential identity,
  version and future-time rejection remain. The original failing run did not
  capture the triggering predicate;40 in-process and16 cold-process replays pass
  without reconstructing that exact failure. A100-sample clock probe also does
  not demonstrate a persistent clock offset. Do not relabel that historical
  failure as a proved production bug or assert its exact cause from injection.
- Shutdown proof reads connectors serially. An eight-order two-connector test
  blocks Spot's tail waiting for Perpetual progress: old code times out before
  Perpetual can start. Both success and remote-failure cases are RED. New code
  starts connector readers with one shared semaphore of3, retains the two fresh
  status/fill-proof phases and applies fills before terminal status. It cancels
  and awaits all sibling readers/workers on error/cancellation. The test verifies
  cross-connector progress, at most3 active reads, fresh proof of every order in
  both phases, and no surviving work on failure. This removes a demonstrated
  avoidable wait; it does not establish the sole cause of the old Cloud timeout.
- A full regression discovers101 actions but100 queued reservations. New executor
  IDs previously hashed a shared timestamp plus only100,000 possible nonces, so
  repeated IDs overwrite the ID-keyed queue. Forcing that nonce reproduces the
  defect deterministically. New IDs hash timestamp plus a128-bit secure nonce;
  Base58/SHA256 format and explicit/serialized existing IDs remain unchanged.
  The same-tick256-config test and the unchanged100-reservation bound pass.

Seven fixed shutdown diagnostic stages cover total/core/strategy/hold/cancel and
proof before/after checkpoint. Timeout ERROR carries at most8 active names and
elapsed times, without raw IDs/arguments. Telemetry failure is contained. Native
PostgreSQL tests hold each stage and verify the existing ownership/recovery fence,
including the real8s/eight-order case. No deadline, account/proof, risk, quantity
or pricing policy is relaxed; no Cloud DB maintenance or trading mutation is used.

Final native regression:3,536 tests plus16 subtests pass. The full chosen proof
pool PostgreSQL regression passes243 tests; an80-case follow-up after the ID
change also passes, including recovery, COMMIT ambiguity, shutdown and the two
freshness routes. These selections overlap and are not added as unique tests.
Earlier REDs and the initial full-regression ID failure are retained.

Final amd64 image
`sha256:8f9e9188e86c3bfa5df818dfa81e9b065b1f346c8abee13d3e79fbfb1b46c90b`
matches all6 changed runtime sources and directly passes the256-ID fixed-legacy-RNG
burst. Diagnostics are enabled. Both final-image packaged LONG/SHORT cases pass:
partial application once, lost ACK without duplicate POST, cold restart, original
fees/PnL, post-only cancel/reprice, fee outage/recovery and full close returning
to OPEN. Evidence is `build/e2e-offline/20261112-174409-bed4b2/` (LONG) and
`build/e2e-offline/20261112-174410-81dfe2/` (SHORT), registry manifest
`sha256:e833bedca2cb23c43a5ae48c0a424eec8481be8d1f24cc08f9818704179d748f`.
The two earlier intermediate image results are retained separately.
This is simulator/local-encryption qualification, not actual Google/GCP KMS,
a new full load/soak or new per-fill OKX Demo reconciliation.

The final17:49 local API check still sees Cloud revision117 running, control84,
no error/HALT/pending mutation. No local runtime change is pushed or deployed.
After source deployment, verify stage timing and the unchanged connected target;
the old8s incident still has no retrospectively proved unique cause.

Evidence/source identities and intermediate artifacts are under
`build/mm-shutdown-freshness-followup-20261112/`. Logs include
`/tmp/hbot-liquidity-startup-age-red.log`,
`/tmp/hbot-shutdown-cross-connector-red.log`,
`/tmp/hbot-shutdown-eight-second-proof.log`,
`/tmp/hbot-executor-identity-collision-red.log`,
`/tmp/hbot-shutdown-proof-identity-native-final.log`,
`/tmp/hbot-proof-pool-postgres-final.log` and
`/tmp/hbot-shutdown-proof-identity-postgres-final.log`.


## Native position-poll scheduling follow-up

The revision118 connected window remains a failed quote gate; its exact counts,
probe correlations, API tail and three successful scheduled shutdowns are in the
[connected record](../demo/cloudrun-quote-replacement-acceptance.md#revision118-connected-window-and-position-poll-follow-up).
No failed or guard-constrained sample is removed to improve that result.

A local RED test uses the native connector poll with history deliberately waiting.
The first positions read completes, then the controller requests a fresh read as
its snapshot expires. The existing general notifier is already set; because the
poll waits for all four account branches, the second position read cannot run.
This is a scheduling mismatch with close-first freshness requirements. It does
not establish the sole cause of every unavailable position or Cloud probe.

The correction adds one coalescing event serviced inside that same native poll.
History, order status and balance run once; positions can be read again while
those branches await IO. Position reads remain sequential, and requests arriving
during a read remain pending for one follow-up. There is no second persistent
poll worker or REST in controller/send gates. Failure and cancellation cancel
and await every child task. Native response success, WS-version fencing, absence
proof, age thresholds and all financial/owner/quantity/price rules are retained.

Five new native tests cover blocked history, in-flight request bursts and both
reader-failure and cancellation cleanup. The RED remains in
`/tmp/hbot-position-poll-starvation-red.log`. Final unchanged canonical regression
passes3,541 tests plus16 subtests;35 focused cases overlap this total. Logs:
`/tmp/hbot-native-position-poll-final.log` and
`/tmp/hbot-position-poll-starvation-green.log`. Hooks pass.

Final amd64 image
`sha256:9cf62faa837a1fce9bcbe2ef02c7a3b67bdf85f602388c6e7f569b759b6d7c36`
matches8 relevant runtime sources. Packaged LONG/SHORT pass on this exact image,
including lost ACK, partial application once, original fees, fee outage/recovery,
maker replacement and cold restart. Receipts:
`build/e2e-offline/20261112-183259-20b023/result.json` and
`build/e2e-offline/20261112-183259-950bdc/result.json`; manifest
`sha256:f1d605bbdd03e5f7a138d5e61e76d31838497ccea79c75a53e3adc5552771d97`.
This is scoped simulator/local-encryption qualification, not a new full load/soak,
real Google/GCP KMS or fresh per-fill OKX REST reconciliation.

Evidence/source identities are under
`build/revision118-quote-qualification-20261112/`. This correction is local;
revision118 still runs1e5d15bda. No new env setting is required. Deploy the
correction and repeat the original connected target before claiming improvement.
