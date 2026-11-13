# Quote replacement and expired-order recovery — 2026-11-12

Connected status: revision119/709700c70 completes901 samples with quality pass
and the original99% gate failed (Spot89.61%, perpetual87.59%). No ERROR/restart/
shutdown timeout is observed, but position evidence and deadline margin remain
open. The scoped per-leg absence-proof correction below is local, not deployed.

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
`build/revision118-quote-qualification-20261112/`. This poll correction was subsequently deployed as revision119/709700c70.
Its connected window and the new local absence-proof correction are recorded
below; no new env setting is required.


## Per-leg absence proof follow-up

The completed revision119 window is quality-valid but still fails the unchanged
quote gate. Keep901 samples, all guard/unknown states,146 recorded perpetual
fills and49 probe transitions. One shutdown core takes7.947s, including4.874s
proof after checkpoint, without a logged deadline failure. Two unavailable
position samples recur after fills. See the [connected record](../demo/cloudrun-quote-replacement-acceptance.md#revision119-connected-window-and-per-leg-absence-proof)
for exact timing, coverage, workload restrictions and remaining gaps.

The custom pair-level WS fence can discard a successful REST response proving
SHORT absent merely because LONG changed while the request was pending. A RED
native/controller test preserves newer LONG but fails absent-SHORT admission;
the reverse direction is covered too. Another RED demonstrates concurrent REST
position callers, which can otherwise let an older response arrive last.
These reproduce mechanisms, not a unique predicate captured in the live samples.

The correction keeps whole-pair position application/snapshot fencing unchanged.
Independent in-memory absence evidence requires a complete successful scoped
CROSS/HEDGE response, correct instrument, unique sides, finite fields and no WS
change of that specific leg. Same-leg WS invalidates proof before metadata waits;
net/unknown sides invalidate both. REST presence clears obsolete absence proof.
Missing, stale or future absence proof cannot use a fresh whole-pair timestamp.
Five RED assertions also demonstrate that a fresh pair timestamp can suppress
refresh after leg evidence has become invalid. Refresh scheduling now checks the
in-memory venue evidence before skipping a native refresh; regular polling cadence
and admission age limits remain unchanged. Native REST readers share a lock,
including cancellation/retry. There is no
partial native quantity application, DB repair or pricing/cap/age relaxation.

Twenty-one new native cases cover both directions, same-leg zero/nonzero WS,
malformed/failed responses, fresh-pair/invalid-leg evidence and serial-reader
cancellation. Two additional REDs exercise malformed WS after version change: old
quantity is retained but its observation is invalidated before parsing. The logs
are `/tmp/hbot-position-absence-race-red.log` and
`/tmp/hbot-position-absence-malformed-ws-red.log`, plus the refresh assertions in
`/tmp/hbot-position-proof-refresh-red.log`; final canonical
regression passes3,562 tests plus16 subtests in
`/tmp/hbot-position-absence-refresh-native-final.log`. The51 focused cases overlap that
total. Fixtures publish absence proof only for actually empty legs. Hooks pass.

Final amd64 image
`sha256:d380cd6f94330d8c1f5f8dda628f292179404f5e8819be172cb9b71239dfc49c`
matches8 runtime sources. Packaged LONG/SHORT pass lost ACK, partial application
once, original fees, maker replacement and cold restart. Receipts:
`build/e2e-offline/20261112-195606-e37e27/result.json` and
`build/e2e-offline/20261112-195606-0938bc/result.json`; manifest
`sha256:28fdb1934486ffc7081f79c76f0e802ac81a9f82fb9c137d28c6c013bca01ce8`.
This is scoped simulator/local-encryption qualification, not a new full load/soak,
real Google/GCP KMS or per-fill native OKX reconciliation. Evidence/source
identities are under `build/revision119-quote-qualification-20261112/`.

The absence-proof correction is not deployed and requires no new env setting.
Repeat connected qualification after deployment. Ordinary replacement gaps,
profile-handoff unknowns and proof deadline margin remain separate open items.

## Scoped uncertain outcomes: local qualification, 2026-11-14

Implemented an experimental, default-false `MM_SCOPED_RECOVERY_ENABLED` path.
Supported managed Spot BUY uncertainty retains its exact native ID and full
possible base/quote effect, with original fee assumptions. Funded Spot cash
SELLs may continue under both submission gates. Other BUYs and every Perpetual
mutation remain paused. Unsupported/legacy fees, foreign bot/account scope,
missing fresh cash, or bounded count/notional failures retain the account pause.
No cloud env, risk limit or financial history is changed by this implementation.

The reconciler never repeats POST or interprets50013/51603 as rejection. Unknown
IDs run in background after cold restore; native executors retain original terms
and cannot reopen a canceled/restored entry. Partial-cancel transfer waits for
fresh account coverage, preserves original fees, and applies once. Stop and
running-owner SIGTERM handoff retain the unresolved journal obligation without
inventing a terminal outcome or erasing its tracker. Persistence, physical
ownership, current Stop and actual risk HALT remain authoritative.

Reservations include other possibly-sent requests without racing their running
transport, resident SELLs and terminal debits newer than the account sample.
Original SELL base fees cannot shrink when a current estimate changes. Bounds
are64 uncertain effects,64 resident tracker entries and the existing Spot
outflow policy notional; unsupported activity pauses rather than expanding caps.
Per-intent backoff is bounded, pruned and honored before another worker cycle.

Evidence under `build/scoped-recovery-20261113/`:

- Preserve `owner-red.txt`, `cold-red.txt`, and the initial whole-service RED at
  `build/e2e-offline/20261114-004249-d43a1b/result.json`. The latter exposed an
  exact-proposal-amount comparison that rejected real venue quantization;
  reservation/restore now use immutable native amounts and prices.
- Final native regression:3,726 passed plus16 subtests. Broader PostgreSQL
  ownership/outflow/commit-outcome selection:145 passed; final automatic/scoped
  selection:58 passed (overlapping runs, not an additive test total).
- Final scoped50013/51603 service case at2 CPU/1GiB passes with two unresolved
  BUYs, funded working SELLs, no duplicate POST, clean Stop/cold Start and a
  running owner handoff preserving the session. Original startup(200s),
  uncertainty observation(60s) and PID1 shutdown(9.5s) budgets are unchanged.
  Explicit shutdown takes1.138/1.502/1.117s in this run.
  Receipt: `build/e2e-offline/20261114-005756-0d1d2c/result.json`.
- Final feature-on lost-ACK/positive-proof case also passes on that same image:
  `build/e2e-offline/20261114-005928-12750d/result.json`.
- The preceding candidate also passes feature-on lost ACK/positive proof and
  both feature-off transport cases (busy/51603 and lost ACK). Their receipts are
  `build/e2e-offline/20261114-005330-7195b8/result.json`,
  `build/e2e-offline/20261114-005428-0aab39/result.json` and
  `build/e2e-offline/20261114-005336-4689f9/result.json`.
- Final image config digest:`c5b6db1dee4029209bc3ac5544fb75ec8e6ed5b3eec53bd4ae6fad2980d87cf5`;
  registry manifest:`94ee2394d7feb33fde7e86749b94d366782ca114d68afb3dcbcb5beca5d5db88`.
  All13 runtime source hashes match the packaged image. OpenAPI and generated
  TypeScript refresh/check pass. Dashboard typecheck remains blocked by existing
  missing `SessionCountdownRing` and `mock/overviewPreviewData` imports plus their
  consequent implicit-any error in `MandateBar.tsx`; no dashboard UI is changed.
- Pre-commit checks pass. Four unused intermediate images created by this task
  are removed; retain the final test image, deployed base and other projects'
  images. Exact IDs are in `build/scoped-recovery-20261113/image-cleanup.txt`.

Injected transport errors and retained-proof warnings are expected in these
scenarios; this is not a zero-warning/log claim or a latency benchmark. Connected
30-minute acceptance, populated-quarantine OOM/Disconnect/profile-switch cases,
general concurrent openings and Perpetual CLOSE remain open. Keep the flag false
until the intended rollout subset is separately approved and qualified. Existing
99% quote/probe gates are not closed by these local tests.

### Populated-quarantine lifecycle follow-up — 2026-11-14

The new Disconnect service RED reproduces an engine restart: changing the selected
key to `revoking` removes it from the ordinary active binding hash, preventing
the supported unknown BUYs from draining into cleanup. Separately, a recovered
order's typed51603 escaped cancellation as a generic cleanup failure. The fix
retains the original binding only for an exact current accepted/applying
Disconnect receipt, stopped control, matching versions/account and present
ciphertext, under the existing physical/financial checkpoint fences. Ordinary
admission still excludes revoking keys. Cleanup reports
`waiting_for_dependency/cleanup_orders_pending`, retains intent/key and retries
the same operation; no fabricated cancellation or successful credential removal.

A second reservation defect counted a positively proven unsent cached SELL as a
possible debit, potentially blocking subsequent funded sells. Skip it only for
the current owner's exact native object with no sent-intent marker, exchange ID,
fills or executed amount. FAILED status alone, a copied identity, late ACK or a
positive fill does not qualify. Other unknown/resident reservations remain.

On the final candidate, native regression passes3,728 plus16 subtests;
PostgreSQL automatic/scoped/credential/cleanup selection passes173. The final
direct handoff assertion also verifies ordinary bootstrap remains blocked for a
revoking key, the stopped handoff succeeds, and a risk stop cannot use that path.
These are overlapping selections, not an additive unique-test total. Pre-commit
and unchanged OpenAPI checks pass. Evidence: `build/scoped-faults-20261114/`.

| Service case | Independent result | Receipt under `build/e2e-offline/` |
| --- | --- | --- |
| Kernel OOM,2 CPU/1GiB | `oom_kill`0→1; engine PID33→260; API PID34 unchanged; two unknown BUYs retained, one partial SELL fill/original fee applied once; resumes same session without HALT or repeated POST. | `20261114-012303-452ad7/result.json` |
| Managed Disconnect,2 CPU/1GiB | Same owner and operation survive repeated dependency waits; encrypted key stays revoking; zero false succeeded audit/erasure. | `20261114-012505-3aba0f/result.json` |
| Timed Random profile rollover,2 CPU/1GiB | Session/control/owner unchanged, generation advances, funded SELL returns with both unknown BUYs reserved. | `20261114-012618-189f25/result.json` |
| Scoped positive lost ACK | Native GET proof restores both sides, no POST duplication or owner restart. | `20261114-012731-2330df/result.json` |
| Ordinary cleanup visibility regression,flag false | Native close readback, exact original fees, flat positions and successful credential removal still pass. | `20261114-012825-157574/result.json` |

Retain initial failure receipts: Disconnect `20261114-010908-3de539`, invalid
read-only rollover request `20261114-010929-658cdc`, OOM observer exit
`20261114-011156-4a5996`, and stale pre-restart observation
`20261114-011559-cc3868`. The last two are harness failures, not evidence of a
trading failure. The corrected observer requires kernel OOM proof and a running
successor generation; PID exit137 alone or a prior status snapshot cannot pass.
The finite allocator stays in the fixture's hard cgroup, never the host.

Final image config:`034be2223bee3aa868f07aad199fd015f94c1fb7066914055d17c5c2de37aa3d`;
manifest:`1bbd10485f71863582df5621013ac1d0fcc86dd96542411c4b6e018c4a6f6350`.
All14 runtime source hashes match. No new migration or schema/API shape change.
This closes the three local lifecycle cases, not their connected counterparts,
every crash boundary or an OOM during Disconnect itself. The cloud flag stays
false; connected stable-revision acceptance and the original quote/probe gates
remain open. No cloud service, account policy or financial row was changed.
One unused intermediate image from this follow-up is removed; the final image,
its baseline and other projects' images remain. IDs: `image-cleanup.txt` in the
same private evidence directory.

### Bounded scoped fault matrix — 2026-11-14

This follow-up adds tests only; the production runtime, transport, financial
application and reservation rules are unchanged from `e43fde2ab`. The supported
scope remains funded Spot cash SELLs while managed Spot BUYs are uncertain.
The Cloud Run flag remains false. No Cloud configuration, credentials, risk
policy or financial rows are changed by this offline qualification.

| Boundary | Evidence and required invariant |
| --- | --- |
| Concurrent sends | Two actual native SELL create coroutines overlap while the first HTTP reply is held. Only the first crosses transport; the second cannot spend the same unreserved cash. The uncertain BUY remains durable. |
| Final send fence | Balance loss/staleness, unsupported base fee, reduced reservation bound, Stop, risk HALT, payload mutation and changed credential identity after authentication all prevent POST. The existing risk HALT is preserved. |
| Late ACK | Duplicate private ACKs update the native order but do not clear its uncertain attempt/reservation. Only complete positive REST proof releases that order; a different51603 intent stays reserved. |
| Late/duplicate fills | A private partial fill arrives during an older REST snapshot. Keep the whole uncertainty until complete proof; duplicate REST/WS trades converge to one fill, one journal event and one native hold with the original `.01` USDT fee. |
| Verification receipt | Interrupt before or after recording the proof receipt, then retry with another unknown order still present. Financial work is not applied twice, and the remaining unknown is never released by association. |
| Physical DB response loss | A loopback TCP proxy drops either the actual verification COMMIT or only its reply. Independent PostgreSQL reads distinguish uncertain versus terminal receipts, with one already-durable unapplied fill in both cases and the other order still uncertain. The failed physical owner cannot submit after network restoration. |
| Delayed proof | Stop, risk HALT, physical owner termination/takeover and the persistence-failure latch win over a delayed successful proof. Loss of owner/persistence prevents the fill writer too. |
| Crash reconstruction | SIGKILL at send/fill/hold is exercised with the flag false and true. The successor reconstructs one position and one original fee, consumes its journal once and never re-POSTs. |
| Reservation bounds | Exactly64 unknown BUYs retain their full native/base/quote amounts. The65th order and a notional cap below the exact sum reject; resident capacity also counts other pairs and cached obligations. Existing stale/fee/legacy/context negatives remain. |

The broader automatic/scoped/ownership/unknown-COMMIT PostgreSQL selection
passes156 cases. The final matrix-specific PostgreSQL run passes11 cases. Its physical COMMIT
tests use a subprocess and the actual DB wire, not a mocked COMMIT return.
The separate native reservation/proof/terminal-read/status selection passes93
tests. These selections overlap the broader PostgreSQL regression; do not add
their totals as unique coverage. Hooks pass for the changed Python files.
Evidence: `build/scoped-matrix-20261114/` retains focused/broad logs, hook receipts
and source/image matching. Early fixture-wiring/decimal-format assertions and
an incorrect ACK-only release expectation are test corrections, not new runtime
bug claims; the successful final receipts are retained separately.

The independently repeated full-service `scoped-uncertainty` case passes on the
same previously qualified image (all14 runtime source hashes match). At2CPU /
1GiB, two unknown BUYs remain intact through explicit Stop/cold Start and a
running SIGTERM/owner replacement, with funded SELL, health200, readiness503
while proof is incomplete and no duplicate POST/HALT. Three startup timings
are11.913/11.070/11.595s; shutdown timings are1.115/1.228/1.429s. These fixture
timings are not a connected quote-latency result. Receipt:
`build/e2e-offline/20261114-021100-84a019/result.json`, whose
`release_scope_complete` remains false.

Connected equivalents, one stable30-minute observation and the original99%
quote/probe gates remain open. This finite matrix does not qualify every crash
interleaving, OOM during Disconnect, general concurrent openings or Perpetual
CLOSE admission under uncertainty. No extra image was built for test-only edits.
