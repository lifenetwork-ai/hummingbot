# Packaged supervisor-diagnostics release — 2026-10-30

Status: in progress. Source commit `e9a1645b7`; packaged image
`sha256:dd602fb2238df0237f2c0aaa532407f31e47cdfbbd25575452b55a021765874c`
is built (1,367,454,040 bytes). Both supervisor module hashes match the reviewed
source. All 17 offline E2E groups pass, including previous-image e526 upgrade/rollback.
Evidence: `build/e2e-offline/20261030-212044-749aa2/result.json`. Shipping
supervisor/API/engine crash/hang recovery also passes at
`build/service-supervision/20261030-215710-5520a1/result.json`. The complete load
run is active at `build/mm-load/20261030-215951-3d41e4`; ordinary control is pending.

The operator requested a newly packaged image, full load including a continuous
60-minute soak, and ordinary-entrypoint comparison. This scope preserves strict
budgets and separately reports trading continuity during degraded reporting.
Historical e526 failures and the unproven original liveness cause remain recorded.

The Mac began with its lid open on battery. An idle-sleep assertion is active
during the tests; it does not protect against physically closing the lid. All
exchange/DB fixtures are isolated and use dummy keys. The persistent Demo remains
stopped on e526 and is not replaced.

The all-load invocation uses the existing `--keep-going` option so that a latency
violation does not discard later windows; any violation still fails the final
result. No aggregate slowdown is silently relabelled as a strict healthy pass.

See the [acceptance checklist](../docs/plans/OKX_DEMO_CURRENT_IMAGE_ACCEPTANCE.md).
