# E2c: bounded listing bootstrap

Date: 2026-10-26 (Asia/Ho_Chi_Minh). Baseline: `9bcdafee8`.
Scope: [E2c](../../docs/plans/LIFE_OKX_MM_ENHANCEMENTS_TDD_PLAN.md), local implementation
and verification. E2c is complete and locally verified.

## Implementation and reuse

The existing LIFE MM controller now supports explicit `bootstrap_then_market` in
addition to the default strict guard. A fresh empty/own-only target book can admit
post-only quotes around the configured anchor, bounded by original anchor band,
absolute/manual price bounds, a finite duration and total market quote allocation.
Weighted reference returns, account/exposure/leverage checks, drawdown, rolling
spot outflow, cancellation and session/owner authorization reuse their existing
implementations. No exchange fills are generated to make qualification pass.

A small policy helper observes bounded known-order-filtered depth and spread.
Only distinct control samples advance qualification. Filled PositionExecutors
still require valuation while transitioning to position hold, even when their
reported trade volume is temporarily zero. Intermittent invalid data
or a process sampling gap resets dwell. Fresh usable market deviation/range
violations block even before qualification. Reaching the book-inspection limit
cannot establish an empty market. Both legs require joint ordinary readiness.

The existing owner recovery row stores immutable account/environment/instrument
policy, the first clock and one-way qualification flag. Initial clock and session
admission commit in one existing row-locked transaction. Restart, revision,
profile/random or session selection cannot renew an episode or rebase its band.
Qualification commits before changed authority. After qualification, loss of
outside depth waits for recovery and never falls back to bootstrap. No reset API,
new schema/table/index, service, market client, fill ledger or cache was added.

Existing pre-intent and post-throttle gates check actual price/quantity/payload
and original allocation, retaining full allocation for partially filled active
orders. Captured bootstrap orders retain their original band/deadline even if
qualification occurs while queued. Existing OKX `expTime` receives that deadline.

PositionExecutor gains an internal configured post-only price option for
bootstrap, avoiding own-book clamping/reanchoring. Default maker behavior is
unchanged. Known zero filled notional/PnL no longer requires book valuation, and
an unfilled managed executor reports its absent average/close marks as unknown. Filled
or held exposure still requires the ordinary fresh mark. The orchestrator now
requires a mark for filled active exposure and held positions; empty unfilled
proposals cannot deadlock initial drawdown readiness.

API/Swagger expose bootstrap state, source label, bounds/budget, UTC deadline,
remaining time, depth/spread and qualification progress. Model/configured anchors
are not labelled independent market fair value. Operational instructions and
illustrative configuration are in the [guide](../../docs/life-mm-controls.md#listing-bootstrap).
Production defaults remain disabled; no production settings or credentials were
changed.

## Red/Green evidence

Ignored logs live under `build/e2c/`. Confirmed failing tests preceded fixes for:

- Missing bounded observation/bootstrap policy helper and complete config validation.
- Empty-book maker price/activation reads and zero-filled executor PnL.
- Full executor-info generation still requiring a missing close mark.
- The first image scenario completed listing/restart/expiry/qualification, then
  failed its final stop: newly market-guarded, unfilled maker executors read
  `NaN` best prices after outside-depth loss. Report generation and shutdown
  raised `Decimal.InvalidOperation`, and the service exited. A Red test covers
  both `NaN` and raised empty-book reads with ordinary maker clamping still
  selected. Its fix reports unknown average/close marks for zero-filled managed
  executors; filled/held valuation permission is unchanged. The original image
  failure is retained under `build/e2e-offline/20261026-231229-f42ac7`.
- Sparse observations incorrectly completing continuous qualification.
- Bounded depth exhaustion incorrectly being eligible for bootstrap.
- Materializing full book DataFrames while checking opposite-side crossing.
- A pending position-hold transition with actual filled exposure being mistaken
  for an unfilled proposal because active report volume was zero.

Regression cases cover raw/quantized outer levels, widened spreads, model-only
manual ranges, fresh weighted basket returns, stale/missing metadata, immutable
own-book anchor, persisted expiry/qualification, actual payload and partial-order
allocation. Real PostgreSQL tests cover rollback, superseded stop, account/HALT,
immutable armed policy, original clock after restart and the tighter final-send
expiry, including qualification while a bootstrap order is queued.

## Verification

Final native regression passed **1483 tests and eleven subtests** (158/11/979/335).
The final focused selection passed 93 tests and two subtests with RuntimeWarning
as errors. The real PostgreSQL full suite passed 166 cases; ten final bootstrap
cases passed after all runtime changes, including five cases added after the full
suite collected (171 unique PostgreSQL cases across runs). Existing broad suites
retain upstream deprecation/mock warnings. Swagger generation/check and scoped
hooks pass.

Final image `sha256:44dfbe9c1c3a692916709ddcb8006220cd8e14fdaf05f1fc57a18c4a2e59007a`
is 1364711274 bytes, Linux amd64; all 29 recorded runtime/config/contract paths
match packaged bytes. The standard full Cloud Run Dockerfile built this artifact.
Runtime ABI/JIT/TLS/SDK/SQL smoke and the listing E2E passed on this image:
`build/e2e-offline/20261026-232643-304142/result.json`. Initial startup/restart
were 8.613s/8.627s, with a 1.024s first clean shutdown. Both spot and perpetual
quoted within allocation/band on empty/own-only books, kept original clocks across
restart/new session, expired/cancelled, qualified with outside depth, waited while
only one market qualified and never rearmed after depth loss. Final stop succeeded
and the same service process remained alive. This case injects zero fills.

The final combined bootstrap/drawdown/outflow sixty-second healthy diagnostic used
1 CPU/2 GiB, 100000 historical orders/fills, about 100 market updates/s, one fill/s
and 20 UI reads/s. It passed unchanged budgets: **p95 57.74ms, p99 296.04ms**, 1200
HTTP 200 reads, zero unexpected errors, sixty unique exchange/recorder fills and
matching positions. Peak RSS was 326938624 bytes. Artifact:
`build/mm-load/20261026-232912-f5d703/result.json`. This healthy workload qualifies
against outside depth; it complements the separate deterministic empty-listing
case. It does not claim to sustain model-only empty-book trading while fills are
forced. No latency threshold or accounting gate was relaxed.

No additional cache or index was justified. The new admission work uses bounded
existing books/trackers and existing owner-row reads; it makes no per-level
history queries or new exchange requests. Qualification/clock changes write only
the existing recovery row. The crossing check uses the first existing book entry,
not a full DataFrame. This is a bounded-work review and short diagnostic, not an
E6 certification of worst-case large outflow expiry/all-feature workloads.

Sanitized artifact/config/source evidence is in
[the JSON report](life-mm-e2c-listing-bootstrap.json). Raw output remains under
ignored `build/e2c/`. Emulated local amd64 measurements are not Cloud Run latency
guarantees. E3–E6 remain open, including the final image matrix, mixed/burst and
sixty-minute soak with all final enhancements. No OKX Demo/live account test or
Cloud Run deployment was performed in this phase.
