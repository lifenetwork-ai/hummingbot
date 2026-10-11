# O.8 baseline and hot-path diagnosis — 2026-10-11

## Verdict and scope

**Diagnostic steps 1–2 are complete. O.8.4/O.8.7 remain open.** No production code, frozen budget or acceptance manifest changed in this increment. The existing full candidate remains historical evidence: **2,206 passed / 7 failed**; it was not rerun here. This report identifies where to make the next change, and does not claim a performance fix, complete offline acceptance or exchange readiness.

[Baseline and run index](evidence/life_o8_profile_baseline.json) preserves the prior evidence digest, seven failing selectors, budget digest and ten diagnostic runs. Every production source hash covered by the previous lazy-journal candidate still matches. All ten runs use the same diagnostic/helper source hashes, and each verifies that those sources stayed unchanged during its run.

## Reproduction and measurement boundary

The new `test.hummingbot.strategy_v2.life_liquidity.o8_profile` CLI executes the **same** `exercise_settled_history` helper as the mandatory pytest scale case. The fixture, real protected executor/filter/final-send path, independent watchdog, heartbeat, five permission checks, ownership assertions and all budget/worker deadlines are retained. Pytest uses no-op observation contexts by default.

Run each configuration sequentially in a fresh process:

```sh
conda run -n hummingbot python -m test.hummingbot.strategy_v2.life_liquidity.o8_profile \
  --history 10000 --variant journals --mode timing \
  --output-dir /tmp/life-o8-profile-timing-example

conda run -n hummingbot python -m test.hummingbot.strategy_v2.life_liquidity.o8_profile \
  --history 10000 --variant journals --mode profile \
  --output-dir /tmp/life-o8-profile-cpu-example
```

Output directories must be new. The CLI returns nonzero when workload assertions fail; failures remain in the JSON report. `timing` records stage wall/process CPU clocks. `profile` additionally records per-stage main-thread cProfile, successful `Path.read_bytes`/`read_limited` calls and returned bytes, fsync duration and GC pauses. Fixture seeding/migration clocks are separate and excluded from hot-path cProfile. No instrumentation is installed into normal pytest or production operation.

The index links portable per-run reports with function rankings, critical callers, source hashes and errors. Raw `.prof` paths/digests refer to local `/tmp` artifacts; rerunning the CLI regenerates them. Read counts describe bytes returned to Python, **not physical disk traffic**. Async stages include other tasks running on the same event-loop thread while the measured coroutine awaits. Cumulative function times overlap and must not be summed; coroutine call counts can include resumptions. Worker CPU and total IPC time are not measured by main-thread cProfile. Process peak RSS includes fixture setup and is not an active-set memory proof.

There is one sample per configuration. Host scheduling materially affects wall time: these measurements establish the bottleneck/call structure, not a causal speed comparison against earlier runs. No full suite or lint job ran concurrently with these ten samples. An earlier smoke test overlapped lint and is excluded from timing evidence.

## Stage-clock measurements, without cProfile/I/O wrappers

| Settled history | Joint poll ms | Propose + filter/dispatch/final send ms | Maximum loop lag ms | Cancel start ms | Outcome |
| --- | ---: | ---: | ---: | ---: | --- |
| 0 | 8.7 | 92.6 | 98.6 | 12.2 | Pass |
| 1,000 | 742.1 | 3,682.6 | 3,937.9 | 268.6 | Fail |
| 10,000 | 7,631.6 | 39,705.3 | 40,688.8 | 728.6 | Fail |
| Frozen ceiling | 2,000 | 1,000 | 500 | 250 | Unchanged |

At 10k, propose takes 5,458.0 ms and filter/dispatch/final-send takes 34,247.2 ms. Process CPU for the latter is 22,716.7 ms, so this is not solely elapsed waiting for storage or host scheduling. Attribution readiness measured alone with a warm cut takes 54.8 ms; that observation does not waive the cache-miss work performed later in the full sender.

## CPU/I/O diagnosis

The following figures are from separate **profile-mode** samples and are not acceptance timings:

| Filter/dispatch/final-send stage | 1,000 history | 10,000 history |
| --- | ---: | ---: |
| Stage wall time | 2,743.1 ms | 23,611.8 ms |
| `IntentWAL.all_records()` | 35 calls / 1,501.5 ms cumulative | 35 calls / 11,902.7 ms cumulative |
| `_decode_record` | 37,002 calls | 370,002 calls |
| `_freeze` self time | 742.9 ms | 6,804.9 ms |
| Lazy `verify_maps` | 112 calls / 81.6 ms cumulative | 112 calls / 958.0 ms cumulative |
| Successful counted reads | 1,507 | 9,283 |
| Returned bytes | 117.0 MiB | 1,161.9 MiB |
| fsync | 14 calls / 0.84 ms | 14 calls / 2.13 ms |
| GC pause time | 11.8 ms | 1,219.3 ms |

### Confirmed primary bottleneck: repeated historical materialization

At 10k, `all_records()` accounts for approximately **50%** of the profiled sender stage's elapsed time. Its direct callers are:

| Caller | Calls | Cumulative ms |
| --- | ---: | ---: |
| `HedgeCoordinator._snapshot` | 20 | 6,527.1 |
| `HedgeCoordinator.allows_spot` | 10 | 3,781.2 |
| `ProtectedSpotExecutorSend._authorized` | 2 | 639.3 |
| `QuoteActionPlanner._journal_consistent` | 2 | 636.2 |
| `ProtectedSpotExecutorSend.submit` | 1 | 319.0 |

`_snapshot` builds two complete WAL tuples to compare disk and memory. `allows_spot` constructs a dictionary covering all historical intents to check pending makers. These account for 30 of the 35 calls. The archived WAL has one decoded segment cache; repeated whole-history walks cycle through the segments, repeatedly decoding/freezing rows and constructing typed records. Disk partitioning therefore reduced retained decoded history without removing consumer work.

The other five calls include full cross-journal ownership/terminal consistency checks. They cannot simply be replaced with pending-only rows: that would discard coverage checks for terminal reservations and aborted intents. They need a separately verified settled relationship proof before their active checks can be bounded.

### Additional confirmed costs

- Repeated integrity fences still read all referenced bytes. The 112 lazy segment-verification calls are real, but their 958 ms cumulative cost is smaller than the 11,903 ms historical tuple materialization cost in this sample. Do not start by weakening corruption detection or adding a broad shared cache.
- New-reservation financial writes still trigger full validation. `_load_archive_data` runs twice and takes 1,252 ms cumulative in the 10k sender. `_commit`, attribution recovery and budget/accounting histories remain additional work after the largest WAL scans are removed.
- The measured fsync cost is small. Moving fsync alone to another thread is not supported as the first fix by this profile.
- GC contributes in the 10k profiled sample; repeated allocations should be removed before considering any GC tuning. This does not quantify uninstrumented GC costs.

## Worker timeout diagnosis

Both 10k thread/process configurations fail in timing mode and profile mode with `JOINT_HISTORY_WORKER_TIMEOUT`, before final send. Main-thread profiles place the wait at `_prime_reservations` / `decode_reservations`; history encoding via `_prepare_history` has not been reached.

During that await, the independent watchdog occupies the event loop synchronously:

| Profile sample | Joint-poll wall ms | Watchdog cumulative ms |
| --- | ---: | ---: |
| Thread | 1,801.5 | 1,799.7 |
| Process | 1,982.9 | 1,980.7 |

The refresh deadline is still 1,000 ms. The watchdog runs attribution, hedge and carry checks, including its own cold reservation restoration, while the worker is preparing reservation evidence. Moving one decode outside the event loop therefore leaves duplicate main-thread work and a blocked result-consumption path. These measurements do **not** isolate worker completion time or prove that worker computation alone is too slow. They establish that a worker timeout cannot be diagnosed only by increasing pool size or considering process startup. Startup is separately measured at approximately 794 ms in timing mode and 817 ms in profile mode.

## Next implementation order and TDD contracts

1. **Remove the proven redundant hedge WAL materialization first.** Use the existing independently verified immutable WAL comparison rather than two `all_records()` tuples in `_snapshot`. Resolve each observed pending maker by exact intent ID in `allows_spot`, then check wire, claim and allowed state. Preserve disk-byte checks and account/session/risk fences. Red tests must reject corruption, stale disk/memory, missing or terminal pending intents, conflicting wire/claim and wrong session; a healthy 10k case must exercise the hedge permission path without calling the historical materialization API. Legacy and archived representations must agree. No new cross-call permission cache is needed for this change.
2. **Remeasure the same sender/permission/watchdog workload.** Keep these baseline reports immutable. Record a new source snapshot and separate timing/profile samples; require both lower historical materialization work and preserved functional assertions. Do not infer that removing 30 calls alone will meet every budget.
3. **Design and test settled cross-journal relationship proofs.** The quote/executor consistency checks currently validate complete coverage. A cached proof must bind the exact verified WAL/reservation/ownership cuts, including terminal/aborted rows, and be invalidated by every relevant financial/identity change. Missing or conflicting history must continue denying permission. A point lookup alone is insufficient.
4. **Reduce financial-write revalidation and whole-campaign allocations.** Preserve exact Decimal sums, fees/cash flows, deduplication, atomic publication, stale-writer checks and uncertain-state handling. Reserve future settlement/cancel/recovery storage before new-risk admission.
5. **Make watchdog work schedulable before expanding worker duties.** Measure cancellation under slow qualification. Cooperative work must hold no writer lock across an await and recheck account/journal/session/config/risk/clock fences before using a result. An arbitrary external historical-file mutation cannot be detected by checking only the root manifest; any change to the existing integrity contract needs an explicit design and corruption tests. Background audit alone is not equivalent to current warm-corruption denial.
6. **Resolve the full-run carry timeout separately.** These profiles do not establish its cause. Preserve the prior failure and unchanged 2-second deadline until a complete instrumented run qualifies it.
7. **Rerun the full frozen scale matrix and O.8.7 completion gate.** Legacy, segmented, thread, process and journal variants remain required. New no-materialization tests supplement functional/latency gates; they do not replace actual affordable protected send, cancellation, memory/storage proof, fault tests or both pinned coverage comparisons.

This order addresses the measured consumer problem before adding another storage/worker layer. It does not require weakening the current integrity contract as a prerequisite to the first fix.

## Verification of the diagnostic increment

The diagnostic counter tests verify alias reads are counted once, successful byte totals are exact, capacity exceptions propagate, failed stages retain evidence, and normal timing mode installs no I/O/CPU instrumentation. The shared pytest helper still executes all original workload assertions.

Focused verification passed **97 tests, 0 failed, 0 skipped**, with nine warnings, in **14.07 seconds**. This includes the three new diagnostic tests, existing verified-state/lazy-journal/worker suites, the terminal no-recheckpoint regression and normal pytest zero-history legacy/segmented/journal cases. Six warnings are existing Pydantic warnings; three arise from pytest `record_property` with JUnit xunit2. All applicable pre-commit hooks passed and `git diff --check` passed. The baseline index records the command, per-case outcomes and local log/XML digests. Diagnostic workload failures above remain failures and do not become passing tests through the CLI. Full regression/coverage/completion gates were not rerun and remain open.
