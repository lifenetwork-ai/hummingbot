# Managed local Demo and quote-availability follow-up

Updated 2026-11-09. No production deployment or real-account mutation is implied
by an offline diagnostic. The real Demo read-only checks are recorded separately
in the [reviewable cutover proposal](../demo/managed-local-cutover-proposal.md)
and [preflight evidence](../demo/managed-local-preflight-evidence.json).

## First measured diagnostic

Shipping image `b090430c…`, unchanged existing probe harness, two application CPUs,
10,000 historical rows and a 180-second diagnostic with independent venue samples
and engine tick/controller/order instrumentation. This is not another full release
gate. Raw run: `build/mm-load/20261109-051823-ce2c11`.

- 3,600 main reads, zero unexpected HTTP/semantic errors, p99 `55.82 ms`.
- 180 simulated fills; these are not real OKX Demo fills.
- Both `LIFE-USDT` spot and `LIFE-USDT-SWAP` have BUY/SELL orders on all
  **180/180 independent venue point samples**. Observer errors, clock errors,
  sink errors and dropped venue records are zero. Observer request-start sample
  gap is at most `1,021.08 ms`; the aligned venue timestamps have a maximum gap
  `1,012.12 ms`. These are different clocks/stages of the same samples.
- Within the measured window: 180 strategy ticks, largest start gap `1,179.71 ms`,
  maximum tick duration `4.23 ms`; 360 controller cycles with largest start gap
  `1,007.02 ms` and maximum duration `1.87 ms`.
- Within the window: 16 submission completions, maximum `250.46 ms`, and 16
  cancellation completions, maximum `174.03 ms`. These method timings do not
  independently prove terminal venue readback; venue observations are separate.
- One local `SessionSubmissionBlocked` attempt occurred during setup before the
  measurement interval. It was not an accepted venue order. All measured-window
  submission/cancellation completions returned normally.

Do not use only `mm-activity-summary.json`: the API process overwrites that shared
filename with its zero-event sink summary during shutdown. The actual engine
JSONL, its hashes and the engine metrics sink counter are retained separately;
the last engine sample reports zero dropped records. This is a diagnostic-summary
scope limitation, not evidence that no engine activity occurred.

[Portable evidence](mm-managed-local-quote-activity-evidence.json) keeps exact
window boundaries, counters and source artifact hashes. Sampling cannot establish
unobserved intervals, remote OKX behavior or the unique cause of a historical
probe incident. No new slow-probe root cause is claimed.

## Stricter managed diagnostic

The separate run `build/mm-load/20261109-052609-677acf` uses local-encrypted
managed credentials, offline signed Google identities with Basic disabled,
operator reads, strict guards and volatility defense. Its 180-second API/load
diagnostic passes: 3,600 main and 900 auxiliary reads, zero unexpected errors,
main p99 `505.41 ms`, and 180 simulated fills. This is a different workload from
the first run; their p99 difference is not a paired source-change comparison.

Both markets have two-sided orders on **175/179 venue samples (97.77%)**. The four
empty samples form two groups around `59–60 s` and `122–123 s`. Each follows
recorded `controller.stop_proposal` with `source=executor_refresh`; each is
bracketed by positive samples approximately `3.01 s` apart. Do not turn that
bracket into a claim of exact continuous downtime. Ordinary replacement remains
in the approved policy denominator: this raw fraction is below 99%, and this
short run does not qualify the approved thirty-minute target. The maximum
observer request-start gap is `1.806 s`, with no observer/clock/sink errors.

One engine probe transition, captured in both engine/API incident files, occurs
**12.17 to 10.98 seconds before** the measurement window and recovers in `1.188 s`.
It is one incident, not two. It does not overlap the four measured refresh samples.
Pre-window correlation has controller/tick evidence but no independent venue
observer samples yet, so continuous quoting during that setup incident remains
unproven. The measured 180-second window has zero probe failures.

## Operating policy and connected follow-up

The operator approved the [initial targets](../../docs/fe-integration.md#initial-operating-targets-approved-2026-11-09)
on 2026-11-09. This closes policy agreement, not measured thirty-minute production
qualification. The dedicated Demo service uses the existing environment warning
setting of 20 seconds; no production/repository `.env` is changed.

The exact account-binding maintenance was separately approved and applied. API
Set/load and stopped cold restart now pass on the shipping image with no exchange
key environment fallback. Controlled connected sessions and lifecycle checks
remain in progress and will be reported with their own scope and real fills.
