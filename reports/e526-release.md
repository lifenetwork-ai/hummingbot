# Fixed-image full release revalidation

Status: **in progress**. The operator requested a complete local release and
continuous-hour soak for the fixed image after the real OKX Demo filled-restart
acceptance. The older complete gate on image715a remains scoped to that image.
Machine-readable [evidence](e526-release-evidence.json) records each stage;
raw logs and large artifacts are under ignored `build/e526-release-current/`.

The tested image is `sha256:e526118b94324ab4a116839c171482fffa4c3e66adc5effa626d2c0242172454`,
cached manifest `sha256:566788f22f8c86f1a7df38088bf23557baf7d1c16cff2c4b411ed2f64e4cb918`,
runtime source commit `783d35c69`. Tests use an isolated simulator and disposable
PostgreSQL; the persistent Demo stays stopped. No Cloud Run deployment is implied.

## Completed preparation and regression

At the start the Mac was on AC power with its lid open. The runtime image and
all ten reviewed module hashes match the native checkout. Native regression
passes **2,047 tests and 11 subtests**; disposable PostgreSQL passes **268 tests**.
`make swagger-check` passes. In the shipping image, the API's startup GC policy
retains automatic collection and reclaims 5,000 later cycles without refreezing.
The four persistent Demo ledger/position fingerprints and stopped control state
were captured for comparison after the run.

## Full image E2E

All **17 E2E groups pass**, including the packaged ABI/JIT/TLS/SDK/size smoke,
seeded filled restart, nine MM mode/profile cases, guarded availability and
compatible previous-image upgrade/rollback preserving economics. Results:
`build/e2e-offline/20261030-141530-9aeb28/result.json`.

## Remaining gates

Shipping-role crash/hang recovery is running. Unchanged warm/mixed/burst/hour/fault tests,
the matched ordinary-entrypoint control and final Demo preservation checks remain
open. No latency, memory, retry, rate, error or continuous-sampling budget has
been relaxed. Exact progress is tracked in the
[acceptance checklist](../docs/plans/OKX_DEMO_CURRENT_IMAGE_ACCEPTANCE.md).
