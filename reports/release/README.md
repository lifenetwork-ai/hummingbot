# Release verification

[All reports](../README.md) · [Current release](../release/mm-release-closeout.md)

Start with the current closeout. Earlier image-specific attempts retain their
original failures and scope; they are not additional open release gates.

| Report | Evidence and scope |
| --- | --- |
| [Current-image closeout](mm-release-closeout.md), [evidence](mm-release-closeout-evidence.json) | Current full R1 passes all healthy windows and six recovery cases;4,884 fills match, soak p99 717ms and zero errors. Matched shipping R2 also passes; earlier failures and unresolved causes preserved. |
| [Runtime supervision](mm-service-supervision.md) | Local acceptance complete: final image/regression/E2E/lifecycle, hour/load/faults and matched ordinary-entrypoint control pass; prior failures retained |
| [Fixed-image full release](e526-release.md) | [Evidence](e526-release-evidence.json); strict full release gates failed; bounded reporting-isolation diagnostics passed |
| [Packaged supervisor-diagnostics release](probe-release-20261030.md) | Image dd602fb2: E2E/recovery/ordinary control pass; Random soak fails fill-producer drain, despite passing API latency |
