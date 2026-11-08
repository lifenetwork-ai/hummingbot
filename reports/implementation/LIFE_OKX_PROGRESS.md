# LIFE / OKX delivery status

Updated 2026-11-08. This concise status replaces the chronological journal; the
[full journal](../README.md#historical-records) (Git: `reports/implementation/LIFE_OKX_PROGRESS.md`) retains every original phase, attempt and result.
Feature/configuration references are in [MM controls](../../docs/life-mm-controls.md),
[FE integration](../../docs/fe-integration.md), [PostgreSQL](../../docs/postgres-runtime.md)
and [credential operations](../../docs/exchange-credentials.md).

## Delivered

- Hummingbot reuse: native connectors, V2 controllers/executors and recorder.
- Three MM scenarios, weighted market basket/token references, YAML-owned profiles,
  fixed/Random sessions including until-stopped, spot/perpetual/both.
- Authenticated operator API, English status/diagnostics, immutable configuration
  ownership, history/statistics/audit and development-only Swagger.
- PostgreSQL persistence, UTC/Decimal precision, idempotent startup migrations,
  durable-before-send, ownership/reconciliation/recovery and background funding.
- Risk/availability, quote guards, drawdown/outflow, listing bootstrap, inventory
  skew, fee/NATR/volatility controls and bounded resource handling.
- Optional Basic bootstrap, Google identities/roles, local/GCP encryption provider
  adapters and recoverable cancel/close/remove credentials operations.

## Current verification

New source-only allocation work: bounded recorder book traversal, disabled DEBUG
formatting and redundant default report DTOs. **972 native passes + 9 subtests**;
paired component costs and shallow-book overhead retained in the
[source report](MM_RUNTIME_FOLLOWUP.md). These changes are not yet packaged or
load-qualified. The accepted image result below retains its original source.

Quote/anchor follow-up: **1,010 native passes + 9 subtests**, 38 new parameter
cases, seven alternating component pairs. Effective-spread calculations avoid
superseded upstream work; anchor fallback avoids whole-book projection while
preserving failure/freshness gates. Source-only; no new image/load acceptance.

The latest full repeat with quantile fixture `41d290393` **passes** all four
healthy windows, Random soak3600 and five faults. Mixed/burst/soak p99
**366.46 / 168.04 / 287.63 ms**; zero unexpected healthy errors, 4,884 exact
simulated fills reconcile and retained growth 23.16 MiB/64 MiB. Mixed 1 / soak 2 engine
probes recover without healthy child replacement; continuous quoting and unique
historical-cause attribution are not established. Host AC power differs from
the preceding failed battery run. All 1,224 packaged hashes remain unchanged.

The failed repeat (mixed main/auxiliary p99 1,315.91/1,169.10 ms, later phases
unrun) remains in Git `41d290393` at the release paths. Source/TDD quantile checks
are 80 before load/81 after one supplementary long-ring integer-precision case.
Existing containers, volumes and image IDs are retained.

Source tests remain separately attributed: reporting-copy `745a86e3c` passes
native 2,788 + 11 subtests / related PG 36; write-fence projection `5c421b121` passes
native 2,780 + 11 / full PG 556. Fresh authority, transactions, strict JSON
normalization and bounded pools remain. Paired component savings and mixed
allocation outcomes are in the [source report](MM_RUNTIME_FOLLOWUP.md).

Earlier fixture: image `a6fe88c0…`, build `745a86e3c`, packages both changes and passes the
unchanged full baseline/soak3600/five faults, four targeted E2E groups,
enhancement90 and a separate activity300 diagnostic, all first attempts. Soak
p99 **406.14 ms**, zero unexpected healthy errors, **24.26 MiB** retained growth;
**4,885** exact simulated fills/fees/positions reconcile. No healthy child
replacement. Eleven soak engine probes and three mixed engine probes recover.
Burst p99 **503.57 ms** and probe frequency regress versus rollback `e00cef38…`;
no uniform speedup or exclusive probe cause is claimed. The activity diagnostic
shows completed cycles and two-sided venue point samples during its two probes,
with non-two-sided refresh observations elsewhere; this is not continuous-quote
proof or baseline soak instrumentation.

Unused `837e7997…` removed after success. Candidate `a6fe88c0…`, rollback `e00cef38…`,
all ten container identities/start states and fourteen volumes are retained.
Previous accepted evidence remains in Git `745a86e3c`; earlier failed attempts
remain in Git `ff6b85ded` under the original report/evidence paths.

- [Current image report](../release/mm-runtime-followup-load.md)
- [Latest source remediation](MM_RUNTIME_FOLLOWUP.md)
- [Remaining acceptance/policy/evidence](../../docs/plans/README.md)

No actual-Demo cutover, real Google/GCP or production deployment is inherited from
these local checks. Historical pass/fail scopes remain in Git history.
