# Plans and remaining verification

Updated 2026-11-07. Start with the [current image result](../../reports/release/mm-runtime-followup-load.md)
and [operating guides](../README.md). Detailed completed TDD plans are in the
[Git history](../../reports/README.md#historical-records); historical unchecked
boxes retain their original image/date scope and are not current release blockers
unless explicitly listed below. Removing historical files does not mark an unresolved item complete.

## Current state

- [x] PostgreSQL/Cloud Run source port, three MM scenarios, fixed/Random sessions,
  spot/perpetual/both, operator APIs and risk controls are implemented.
- [x] Runtime `e030cfcdc`: 2,780 native + 11 subtests pass. Full PG530 pass /
  1 obsolete query-count expectation fail; test-only correction + related rerun9
  pass (all531 distinct cases). Prior source Swagger pass; image checked separately.
- [x] Image `837e7997…`: unchanged full baseline, 60-minute soak and five faults;
  four targeted E2E groups after one disclosed retry, enhancement diagnostic pass.
- [x] Soak p99 360.68 ms, zero unexpected healthy errors, 24.05 MiB retained growth,
  4,884 exact simulated fills/fees/positions. Eight healthy probes recover.
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
  establish continuous two-sided quoting. [Original open item](../../reports/README.md#historical-records) (Git: `docs/plans/MM_RUNTIME_LOGIC_AUDIT_TDD_PLAN.md`).
- [ ] Exclusive attribution of the older p99/owner-publication trigger still lacks
  contemporaneous host/storage/wait proof. Controlled WAL/loop evidence establishes
  mechanisms, not that unique historical cause. [Original investigation](../../reports/README.md#historical-records) (Git: `docs/plans/MM_OWNER_PUBLICATION_TIMEOUT_INVESTIGATION.md`).
- [ ] The actual historical OKX expiry-response shape still lacks the isolated
  witness requested by that plan; simulated transport/parser cases are separate.
  [Original open item](../../reports/README.md#historical-records) (Git: `docs/plans/OKX_EXPIRED_SUBMISSION_TDD_PLAN.md`).

The prior controller-image natural-spot/clean-soak/guard-wait boxes are preserved
in the [original image-scoped Demo plan](../../reports/README.md#historical-records) (Git: `docs/plans/OKX_DEMO_CURRENT_IMAGE_ACCEPTANCE.md`).
A newer offline pass does not retroactively pass that earlier image or certify a
new connected Demo run. The latest probes remain an attribution limitation, not
an assertion of an unobserved live defect.

Image `837e7997…` separately qualifies the changed fill-flush runtime; rollback
`bf4e1f77…` retains its original gate. The first 9.5s PID1 shutdown poll timeout
remains unexplained; an unchanged-image/unchanged-budget retry passes. Preserve its
failure evidence and capture the Docker/PID exit-state timeline if it recurs.
The full PG attempt and focused correction remain in the [source report](../../reports/implementation/MM_RUNTIME_FOLLOWUP.md#probe-follow-up-fill-autoflush--2026-11-07).

## Retained plans

- [PostgreSQL / Cloud Run](POSTGRES_CLOUD_RUN_PORT_PLAN.md)
- [Admin identity and encrypted credentials](ADMIN_ACCESS_OKX_CREDENTIALS_TDD_PLAN.md)
- [Actual OKX Demo versus offline image acceptance](OKX_DEMO_CURRENT_IMAGE_ACCEPTANCE.md)
- [Latest runtime remediation checklist](MM_RUNTIME_FOLLOWUP_REMEDIATION_TDD_PLAN.md)

Production deployment and FE browser acceptance remain separate from these local
checks. Their procedures are in [Cloud Run operations](../cloudrun.md) and
[FE integration](../fe-integration.md).
