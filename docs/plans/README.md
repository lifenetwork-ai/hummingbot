# Plans and remaining verification

Updated 2026-11-07. Start with the [current image result](../../reports/release/mm-runtime-followup-load.md)
and [operating guides](../README.md). Detailed completed TDD plans are in the
[lossless history archive](../../reports/archive/README.md); historical unchecked
boxes retain their original image/date scope and are not current release blockers
unless explicitly listed below. Archival does not mark an unresolved item complete.

## Current state

- [x] PostgreSQL/Cloud Run source port, three MM scenarios, fixed/Random sessions,
  spot/perpetual/both, operator APIs and risk controls are implemented.
- [x] Latest source regression: 2,780 native + 11 subtests, PostgreSQL523, Swagger.
- [x] Image `bf4e1f77…`: unchanged full baseline, 60-minute soak and five faults;
  four targeted E2E groups and enabled-enhancement diagnostic pass.
- [x] Soak p99 306.91 ms, zero unexpected healthy errors, 21.89 MiB retained growth,
  4,885 exact simulated fills/fees/positions. Three healthy probes recover.
- [x] One unused image removed; all existing container references/volumes preserved.

## Remaining external acceptance

- [ ] Real Google sign-in/certificate discovery and real GCP KMS/IAM with synthetic
  credentials, when project/client resources are available. Offline Google/local
  KEK checks do not close this. [Credential plan](ADMIN_ACCESS_OKX_CREDENTIALS_TDD_PLAN.md).
- [ ] Reviewed existing-Demo managed-credential cutover, if that migration is chosen.
  It has not been performed by the new local gates. [Demo status](OKX_DEMO_CURRENT_IMAGE_ACCEPTANCE.md).

## Policy and evidence still unresolved

- [ ] Agree a production quote-coverage SLO separating normal replacement,
  guard-restricted operation and listing bootstrap. HTTP200/PID stability does not
  establish continuous two-sided quoting. [Original open item](../../reports/archive/README.md#record-019).
- [ ] Exclusive attribution of the older p99/owner-publication trigger still lacks
  contemporaneous host/storage/wait proof. Controlled WAL/loop evidence establishes
  mechanisms, not that unique historical cause. [Original investigation](../../reports/archive/README.md#record-013).
- [ ] The actual historical OKX expiry-response shape still lacks the isolated
  witness requested by that plan; simulated transport/parser cases are separate.
  [Original open item](../../reports/archive/README.md#record-024).

The prior controller-image natural-spot/clean-soak/guard-wait boxes are preserved
in the [original image-scoped Demo plan](../../reports/archive/README.md#record-023).
A newer offline pass does not retroactively pass that earlier image or certify a
new connected Demo run. The latest probes remain an attribution limitation, not
an assertion of an unobserved live defect.

## Retained plans

- [PostgreSQL / Cloud Run](POSTGRES_CLOUD_RUN_PORT_PLAN.md)
- [Admin identity and encrypted credentials](ADMIN_ACCESS_OKX_CREDENTIALS_TDD_PLAN.md)
- [Actual OKX Demo versus offline image acceptance](OKX_DEMO_CURRENT_IMAGE_ACCEPTANCE.md)
- [Latest runtime remediation checklist](MM_RUNTIME_FOLLOWUP_REMEDIATION_TDD_PLAN.md)

Production deployment and FE browser acceptance remain separate from these local
checks. Their procedures are in [Cloud Run operations](../cloudrun.md) and
[FE integration](../fe-integration.md).
