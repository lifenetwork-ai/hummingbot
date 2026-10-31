# Until-stopped session implementation evidence

2026-10-28. Implements [the TDD plan](../../docs/plans/MM_UNTIL_STOPPED_TDD_PLAN.md).

## A — Explicit duration mode and send authority

Five new clock/validation cases first failed, then a 40-test session/catalogue/
operator selection passed. New requests choose `timed` (default) or
`until_stopped`. The latter requires `allow_until_stopped: true` in deployed YAML
and omits duration_seconds. Packaged normal/Demo catalogues grant permission;
loading them does not start trading. Legacy catalogues default permission to
false; old requests default to timed and retain recorded hashes/deadlines.

Fixed continuous sessions have no session or step deadline. Random continuous
sessions have no overall deadline but keep finite, start-anchored step windows.
The final-send projection explicitly requires the pinned permission/mode; missing
deadlines on a timed/malformed session cannot accidentally grant indefinite
authority. Cold restoration skips elapsed random windows and fences old generations.

Runtime tests exposed two existing finite-session assumptions: HALT scheduling
compared against a null overall expiry, and bootstrap admission included null
deadlines in `min()`. Red/Green tests now preserve HALT and the independent finite
bootstrap episode. A 27-test runtime/session/bootstrap selection passed. A fixture
initially called asyncio.run from an async test; making that test synchronous
exposed the actual deadline failure before the production fix.

The real PG transport test exposed a third assumption in REST expTime clamping.
Only actual session/step deadlines now narrow the existing five-second request
deadline; a bootstrap deadline still narrows it too. The selected 14 PG tests
pass with fixed/random continuous sessions and original bootstrap behavior.

## B — PostgreSQL and control lifecycle

Real-PG HTTP tests verify permission disabled/enabled, advertised duration modes,
null overall expiry/remaining time, pinned restart decoding, and switching from
continuous random to a bounded timed session. An identical accepted retry still
returns its original receipt after the responding revision disables new continuous
sessions. Permission validation occurs at new-session creation, after retry lookup.
The pinned active catalogue remains authoritative; current YAML is not substituted.

Cold ownership handover preserves the session. A simulated thirty-day offline
interval advances directly to the current random window and fences the previous
generation. Durable HALT prevents further admission/rollover and new switch intent;
Stop still records durable stopped intent without clearing HALT. Bootstrap tests
exercise actual recorder, pre-intent and REST authorization for fixed/random
continuous sessions, retaining finite episode and request expiration headers.

All 14 selected PG tests passed. The first handover test omitted the explicit
`catalogue=None` argument on its stop helper; the fixture was corrected and rerun.
No schema migration, new worker or external scheduler was added.

## Cold-start boundary regression found by the image test

Two runs of the first image `8740710b4acc` failed after restarting a continuous
random session with short 15-second test windows. The second run retained safe
before/after shutdown evidence: no HALT, pending mutation or incomplete shutdown
remained, and the stopped checkpoint was false. HALT appeared during the new
process's startup when a random boundary cancelled reconciliation in progress;
the replacement startup then correctly refused the unresolved state.

Two new service regressions failed before the fix: a pending strategy startup
must not rotate just for a random boundary, and controller account/reference
initialization must also finish before automatic rotation. Overall timed expiry
still wins, completed initialization still rotates, and expired step send
authority remains closed. The 42-test runtime/session/availability selection then
passed. Explicit Stop/Switch cleanup and real recovery HALT were not weakened.

Automatic rotation now waits for startup completion and then skips directly to
the current window using the original start clock. It does not extend the old
step, clear HALT or retry uncertain mutations. The image test retains the
shutdown summary and runs the original nine timed profile/market cases and a separate fresh-ledger
continuous rollover, actual restart and Stop scenario.

## Test isolation and clock correction

An initial combined run on the fixed image completed continuous rollover/restart/
Stop, then correctly reported an incomplete observational reconciliation: adding
those orders to the existing matrix exceeded its 100-order scope and included an
unresolved local identity. The production reconciliation limit and completeness
rules were not changed. The continuous scenario now owns a separate disposable
ledger (`--scenario until-stopped`), also included by `--scenario all`; the
original `life-mm` matrix and its matched assertion remain unchanged.

One full-PG rerun exposed a pre-existing freshness fixture mixing the host's
heartbeat clock with PostgreSQL observation timestamps. The fixture now uses
`clock_timestamp()` for both; the focused test and final 215-test PG suite pass.
No production freshness limit was relaxed.

## Repeating the isolated functional checks

Build the current Cloud Run image, then run each command against the same image:

```sh
python scripts/test_e2e_offline.py --image hummingbot-cloudrun:local --scenario life-mm --trading-env demo
python scripts/test_e2e_offline.py --image hummingbot-cloudrun:local --scenario until-stopped --trading-env demo
```

Both use the offline simulator on internal Docker networks, not OKX Demo APIs.
The first retains the existing timed matrix, weighted references, stale-source
readiness, matched observational reconciliation, expiry/funding and DB-fault HALT
assertions. The second owns a fresh DB and checks continuous random rollover,
clean process replacement, stable session/start clock, cancellation and Stop.

## Final functional verification scope

- Native regression: **1,673 passed + 11 subtests** (groups 165, 11, 1,057, 440).
- Complete disposable PostgreSQL suite: **215 passed**; 34 existing warnings.
- Swagger generation check, changed-Python hooks and linked-document checks pass.
- Final Linux amd64 image: `sha256:d8a189a096227079a62f619411dbfa65dc902a94c6029c0e4813cf65d58da048`,
  1,366,783,990 bytes; all 24 recorded runtime/schema/YAML files byte-match source.
- Timed image E2E: `build/e2e-offline/20261028-015146-06577b/result.json`, passed.
- Continuous image E2E: `build/e2e-offline/20261028-015612-2226bb/result.json`, passed;
  cold process startup about 8.8 seconds, clean shutdown about 1.3 seconds.

These functional runs use separate DBs and the same image/manifest. They are
scoped scenarios (`release_scope_complete=false`), not a complete upgrade/rollback
release matrix. Thirty days of skipped random windows and a year of fixed
session permission were logical-clock tests, not wall-clock uptime claims.
No real OKX, deployed Cloud Run or browser UI verification was performed.
Older runtime rollback is unsupported for the new persisted fields. FE needs
new generated types and duration controls; this backend repo does not build FE.

## Bounded diagnostic load

The same image passed a 60-second probe with 100,000 historical orders and
100,000 historical fills, 1 CPU / 2 GiB for the bot, amd64 emulation on arm64.
All 1,200 original and 300 auxiliary reads completed; unexpected errors: **0**.
Original reads: p95 **66.8 ms**, p99 **246.3 ms**. Auxiliary reads: p95 **110.2 ms**,
p99 **241.4 ms**. Peak RSS was about **315 MiB**. Start/Switch/Stop completed in
2.56/3.00/1.36 seconds. Recorder/exchange agreed on 60 unique fills and positions;
query-count and exchange-read bounds passed, with no declared budget violations.

This probe uses the existing timed-session workload and is marked diagnostic.
It is not a new sixty-minute release gate or proof of continuous production
uptime. See [portable evidence](mm-until-stopped-evidence.json); raw artifacts
remain in `build/mm-load/20261028-015721-8da860/`.
