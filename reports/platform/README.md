# Platform verification

[All reports](../README.md) · [Current release](../release/mm-release-closeout.md)

PostgreSQL, image packaging, logging and Cloud Run preparation.
Local verification does not imply a completed cloud deployment.

| Report | Evidence and scope |
| --- | --- |
| [Cloud Run launch preparation](cloudrun-readiness-review.md) | Template/review/project-selection TDD, stopped-startup checklist and pending cloud inputs |
| [PostgreSQL verification](postgres-verification.md) | Port, packaging and release evidence |
| [PostgreSQL performance review](postgres-performance-review.md) | SQL, cache and recovery measurements |
| [Image optimization](cloudrun-image-optimization.md) | Image/package sizes and startup comparisons |
| [Cloud Run logging verification](cloudrun-logging-verification.md) | [Portable evidence](cloudrun-logging-verification.json); severity/UTC/redaction, SQL/warning fixes, rebuilt image and 13 functional groups; no new upgrade/soak or cloud deployment |
| [Decimal precision evidence](postgres-decimal-precision.json) | Exact decimal storage migration and verification |
