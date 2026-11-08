# Actual OKX Demo evidence

[All reports](../README.md) · [Current offline image](../release/mm-runtime-followup-load.md)

The current separately requested managed-Demo follow-up has passed read-only
REST/WS preflight and restored the latest available backup into a new isolated
database. Account binding requires review before managed Set or trading. See the
[cutover proposal](managed-local-cutover-proposal.md). Earlier local load gates
did not operate on the real Demo account or change their image/date scopes. See the
[current Demo/image status](../../docs/plans/OKX_DEMO_CURRENT_IMAGE_ACCEPTANCE.md)
and [last documented stopped-Demo restoration](../performance/mm-admin-worker-lanes.md).

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
