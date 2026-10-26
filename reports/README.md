# Reports and verification results

This directory contains implementation history, audits, benchmark observations
and sanitized acceptance artifacts. Each report retains its original image,
source revision, environment and verification limits. A passing historical run
does not certify a later image. Operational guides and plans live in [docs/](../docs/README.md).

| Report | Associated artifact or scope |
|---|---|
| [LIFE / OKX progress](implementation/LIFE_OKX_PROGRESS.md) | Phase history and Red/Green verification |
| [Short diagnostic iteration](life-mm-short-repro-review.md) | [CPU/probe evidence](life-mm-short-repro-evidence.json); datetime-only serialization and nested UTC fix; timeout cluster remains open |
| [Extended traced load review](life-mm-trace-load-review.md) | [Portable trace evidence](life-mm-trace-load-evidence.json); mixed passed, sixty-minute soak failed p99/timeouts |
| [MM logic-flow review](life-mm-logic-review.md) | [TDD/image/load evidence](life-mm-logic-review-evidence.json) for control, exposure, reconciliation and funding |
| [LIFE / OKX audit](LIFE_OKX_IMPLEMENTATION_AUDIT.md) | Historical source inventory and reuse review |
| [PostgreSQL verification](postgres-verification.md) | Port, packaging and release evidence |
| [PostgreSQL performance review](postgres-performance-review.md) | SQL, cache and recovery measurements |
| [Image optimization](cloudrun-image-optimization.md) | Image/package sizes and startup comparisons |
| [Load and soak results](life-mm-load-tests.md) | [Original acceptance JSON](life-mm-load-acceptance.json) |
| [MM optimization review](life-mm-optimization-review.md) | Cache, SQL and task-lifecycle follow-up |
| [OKX Demo acceptance](okx-demo-acceptance.md) | [Protocol JSON](okx-demo-acceptance.json), [natural-fill JSON](okx-demo-fill-acceptance.json) |
| [Decimal precision evidence](postgres-decimal-precision.json) | Exact decimal storage migration and verification |
| [Funding backfill evidence](funding-backfill-evidence.json) | Implementation, image E2E/load probe and Demo acceptance scope |

Keep portable result summaries here. Large raw logs, ledgers, dumps and per-run
artifacts belong under ignored `build/`; credentials and private account setup
remain outside the checkout. Future acceptance exporters should write summaries
to `reports/` and update the corresponding plan checklist only after verification.
