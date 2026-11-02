# Residual engine-probe investigation

Date: 2026-11-02 (Asia/Saigon). Start from accepted local image `e2d393b1`,
source `a67233dc9`. The preceding [publication investigation](MM_ENGINE_STATUS_PUBLICATION_TDD_PLAN.md)
retains its completed image-specific acceptance and residual 15/6 probe episodes.

- [x] Inspect complete retained timing distributions and sample stacks; separate
  common costs from isolated tails and client timings from server execution.
- [x] Red/Green the redundant observer ownership round trip. Preserve standby,
  first acquisition, lost/closed/invalidated ownership, fresh control reads,
  final recovery check and every per-order/transport fence. No authority cache.
- [x] Verify query count and changed-control rejection with real PostgreSQL;
  run broader native/PG checks appropriate to the final source.
- [x] Profile real executor reports and changing-price/PnL semantics. Change
  executor code only if a repeatable material cost justifies it; reuse existing
  per-report snapshots rather than introduce persistent valuation caches.
- [x] Run a bounded controlled image diagnostic if source changes; publish
  findings/evidence, scope limits and commits; clean owned test resources.

This is a targeted investigation and source/diagnostic validation. It does not
relabel `e2d393b1` or its completed hour gate as acceptance of changed source.
A new full release claim requires the unchanged final-image release gates.

Outcome: source `717c51727`, diagnostic image `18422eca`; native 2,284 plus 11
subtests, full PG 285 plus focused HTTP/query 2, Swagger, MM lifecycle E2E and
300s ordinary-entrypoint load pass. API p99 61.20ms, zero API errors, 300 matched
fills/fees/positions; zero engine and one recovered API probe in this window.
Executor report runtime remains unchanged; no authority/valuation cache added.
[Report/evidence](../../reports/investigations/mm-engine-residual-probes.md).
