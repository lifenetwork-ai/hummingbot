# Plans and remaining verification

Updated 2026-11-07. Start with the [current image result](../../reports/release/mm-runtime-followup-load.md)
and [operating guides](../README.md). Detailed completed TDD plans are in the
[Git history](../../reports/README.md#historical-records); historical unchecked
boxes retain their original image/date scope and are not current release blockers
unless explicitly listed below. Removing historical files does not mark an unresolved item complete.

## Current state

- [x] PostgreSQL/Cloud Run source port, three MM scenarios, fixed/Random sessions,
  spot/perpetual/both, operator APIs and risk controls are implemented.
- [x] Latest source write-fence projection: 2,780 native + 11 subtests and full
  PG556 pass, nine new cases. Fresh authority remains, unused proof JSON is omitted;
  its component measurements do not qualify a new image.
- [x] Earlier runtime `d9488143d`: ownership-read TDD preserves fresh authority,
  write transactions, I/O deadlines and bounded pool bookkeeping. The image below
  separately qualifies that earlier runtime.
- [x] Image `e00cef38…`: unchanged full baseline, 60-minute soak and five faults;
  four targeted E2E groups on first attempts, enhancement diagnostic pass.
- [x] Soak p99 410.20ms, zero unexpected healthy errors, 28.59MiB retained growth,
  4,884 exact simulated fills/fees/positions. Five soak engine probes, one mixed
  API probe and one engine probe between healthy windows recover.
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

Image `e00cef38…` separately qualifies the ownership-read runtime; rollback
`837e7997…` retains its original gate. The rollback image's first 9.5s PID1 shutdown
poll timeout remains unexplained; its unchanged-image/unchanged-budget retry
passed. That evidence remains in Git `ff6b85ded` at the current report/evidence
paths. The new image passes initial-position restart on its first attempt;
capture the Docker/PID exit-state timeline if the earlier failure recurs.
The full PG attempt and focused correction remain in the [source report](../../reports/implementation/MM_RUNTIME_FOLLOWUP.md#probe-follow-up-fill-autoflush--2026-11-07).

The [ownership-read source](../../reports/implementation/MM_RUNTIME_FOLLOWUP.md#residual-probes-ownership-reads--2026-11-07)
reproduces and removes a redundant read-COMMIT delay for idle scopes. It does not
uniquely explain every old probe. Its new image's unchanged hour gate passes;
soak probes fall 7→5 while soak p99/retention regress within budgets. Keep the
component improvement and mixed full-system observations separate.
The later [write-fence projection](../../reports/implementation/MM_RUNTIME_FOLLOWUP.md#fresh-write-fence-identity-projection--2026-11-07)
passes source regressions but still needs separate image qualification before
replacing the accepted candidate; no new connected or cloud test is implied.

## Retained plans

- [PostgreSQL / Cloud Run](POSTGRES_CLOUD_RUN_PORT_PLAN.md)
- [Admin identity and encrypted credentials](ADMIN_ACCESS_OKX_CREDENTIALS_TDD_PLAN.md)
- [Actual OKX Demo versus offline image acceptance](OKX_DEMO_CURRENT_IMAGE_ACCEPTANCE.md)
- [Latest runtime remediation checklist](MM_RUNTIME_FOLLOWUP_REMEDIATION_TDD_PLAN.md)

Production deployment and FE browser acceptance remain separate from these local
checks. Their procedures are in [Cloud Run operations](../cloudrun.md) and
[FE integration](../fe-integration.md).
