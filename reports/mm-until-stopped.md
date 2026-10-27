# Until-stopped session implementation evidence

2026-10-28. Implements [the TDD plan](../docs/plans/MM_UNTIL_STOPPED_TDD_PLAN.md).

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
shutdown summary and repeats the original nine timed profile/market cases plus
continuous rollover, actual restart and Stop.
