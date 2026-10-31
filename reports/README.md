# Reports and verification results

Start here; operational instructions and active plans are in [docs](../docs/README.md).

## Current result

- [Release closeout](release/mm-release-closeout.md): image `5973410d`, full R1
  and matched shipping R2 pass. Earlier failures remain recorded.
- [Historical-cause review](investigations/mm-historical-cause-review.md): latest
  diagnostic fix and explicit H1/H2/H3 attribution limits; accepted R1/R2 unchanged.
- [Implementation progress](implementation/LIFE_OKX_PROGRESS.md): chronological
  delivery journal, with a current summary at the top.

## Browse by topic

| Folder | Contents |
| --- | --- |
| [release](release/README.md) | Current closeout, supervision acceptance and earlier image-specific release attempts |
| [investigations](investigations/README.md) | Latency, probe failures, reporting SQL, incident capture and test-instrumentation fixes |
| [demo](demo/README.md) | OKX Demo preflight, sessions, natural fills, restart and funding evidence |
| [implementation](implementation/README.md) | Feature delivery, audits, API/UX review and progress journal |
| [platform](platform/README.md) | PostgreSQL, Docker packaging, logging and Cloud Run readiness |
| [performance](performance/README.md) | Load/soak, memory, optimization and synthetic economic stress |

Keep each report and its portable evidence together. JSON evidence is preserved
byte-for-byte; paths and commands recorded inside it describe the original run
layout. To locate a moved artifact, use its unchanged filename in the topic index.
A historical pass does not certify a newer image, and old pending checkpoints
do not override the current closeout. Cloud and FE work retain their own scope.

Raw logs, ledgers and dumps stay under ignored `build/`; credentials and private
account configuration stay outside the checkout. Future reports belong in the
appropriate topic folder, with a link in that folder's index. Keep this main
index focused on current results and topic navigation.
