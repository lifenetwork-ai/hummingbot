# Actual OKX Demo evidence

[All reports](../README.md) · [Current offline image](../release/mm-runtime-followup-load.md)

The [latest managed-Demo follow-up](managed-local-connected-followup.md) passes
separately reviewed recovery, all three profiles through Random steps0/1/2,
same-session active restart, Stop and cancel/close/remove/reconfigure/stopped
cold restart on `4451974e…`. All23 original fills/3,513 order IDs remain. Three
earlier natural spot fills and seven cleanup fill parts match individual REST;
final bot stopped, pending orders0, perpetual flat, spot cash preserved.

The [earlier failed Random attempt](managed-local-connected-evidence.json) on
`b090430c…` remains failed. [Applied binding](managed-local-cutover-proposal.md)
and [separately approved HALT maintenance](managed-local-random-recovery-proposal.md)
retain exact scopes. One optional reporting warning recovered during cleanup;
new candidate full release/soak and real GCP KMS remain separate verification.
See the [current Demo/image status](../../docs/plans/OKX_DEMO_CURRENT_IMAGE_ACCEPTANCE.md).

- [Checkpoint recovery](okx-checkpoint-recovery.md), [evidence](okx-checkpoint-recovery-evidence.json)
- [Expiry investigation](okx-expired-submission-investigation.md), [approved intent proposal](okx-expiry-incident-recovery-proposal.md), [checkpoint-stop proposal](okx-checkpoint-stop-recovery-proposal.md)
- [Natural-fill recovery](okx-demo-natural-fill.md), [evidence](okx-demo-natural-fill-evidence.json), [aggregate proposal](checkpoint-poll-recovery-proposal.md)
- [Connected quote-recovery session](okx-demo-quote-recovery.md), [evidence](okx-demo-quote-recovery-evidence.json), [natural-fill REST evidence](okx-demo-quote-recovery-natural-fill-evidence.json)
- [Earlier fixed cases](okx-demo-current-image.md), [evidence](okx-demo-current-image-evidence.json)
- [Prior latent-fix Demo](okx-demo-latent-fixes.md), [evidence](okx-demo-latent-fixes-evidence.json)
- [Initial acceptance](okx-demo-acceptance.md), [protocol](okx-demo-acceptance.json), [fills](okx-demo-fill-acceptance.json), [funding](okx-demo-funding.json)
- [Local startup](local-demo-startup.md), [evidence](local-demo-startup-evidence.json), [preset validation](okx-demo-preset-validation.json)

These reports/approvals keep their own account/image/date scope. An old approval
is not authority for a different database repair. Current maintenance procedure is
in [PostgreSQL operations](../../docs/postgres-runtime.md). Superseded navigation
and detailed image plans remain in the [Git history](../README.md#historical-records).
