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
