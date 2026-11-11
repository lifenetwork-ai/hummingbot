# Actual OKX Demo evidence

[All reports](../README.md) · [Latest Demo/release](../release/demo-release-closeout.md)

2026-11-11 follow-up: [verified runtime baseline and owner handoff](cloudrun-runtime-baseline-handoff.md).
The legacy ledger now supports cold recovery. Revision100 passed the conditional
quote target but retained one unknown sample. The subsequent
[perpetual cap / regular-profile qualification](cloudrun-perpetual-cap-qualification.md)
records the operator-approved 0.05/0.05/0.1 BTC policy. Older results below keep
their own image scope.

**Earlier API state at03:48 UTC on 2026-11-10: revision77, RUNNING Until stopped, same session/control53/53, no HALT; startup/live/ready200. Historical revision74→75 rollout is attributed to CI. New CI artifact gates remain separate.**
The [latest closeout](../release/demo-release-closeout.md) qualifies actual
perpetual BUY/SELL handoff and a complete30-minute conditional quote target on
revision73 (spot99.7700%/perpetual99.7133%, zero unknown). Temporary settings are
disclosed and then removed. Automatic close/remove/Set,384 stopped actual fills,
original fees/positions and flat hedge cold reload pass. No HALT. Full offline
load/Random soak3600/six faults/retention on regular image `4198f2fa…` pass.
Revision75 image/rollout verification remains open.

Follow-up: Cloud Build/audit attributes revision74→75 to the delayed automatic
deployment of older commit `f7da0d1e6`; no new runtime defect is reproduced.
Overlapping-owner E2E and91 native controls pass. Merged CI artifacts require
separate verification; they do not inherit image4198 release gates. See [closeout investigation](../release/demo-release-closeout.md#later-revision-transition-under-investigation).

## Earlier connected scopes

The following records describe their original revisions, not the current
running state. Revision55 was left running at that earlier handoff:

**Revision55 bounded acceptance (2vCPU/1GiB).**
The separately approved100/200USDT policy re-arm (audit39) preserves all PnL peaks
and financial history.115 samples pass the new600-second observation, control16/16;
70 fills match REST, including two new natural spot fills. Final readback has two
spot SELL orders and no perpetual quotes because its existing gross cap remains.
Startup probe/controller warnings remain explicit. This is not a full soak,
two-sided SLO or new perpetual-fill qualification.
[Applied policy and bounded result](cloudrun-drawdown-policy-recovery-proposal.md).

The old revision52 failure and separately approved three-row checkpoint recovery
(audit36) retain their scope. The revision54 attempt then stopped on15USDT spot
drawdown after89 healthy samples; that old600-second attempt remains failed.
Its68 fills matched REST and the old risk stop was only re-armed after the separate
100/200USDT approval. No cap was raised or perpetual position forcibly closed.
[Incident and proposed recovery](../platform/cloudrun-demo-abrupt-restart.md).

[Cloud Run Google lifecycle acceptance](cloudrun-google-credentials-acceptance.md)
now includes the revision52 continuous-Demo follow-up: both/follow_market,
Until stopped,112 samples over604s and21 new individually REST-matched fills.
The bot was left running at that earlier handoff. Zero application ERRORs/spread rejections;
four probe warnings and a33.2s sampled spot quote gap remain explicit. This does
not close the99% two-sided objective or full soak. See
[continuous evidence](cloudrun-continuous-demo-evidence.json).

The earlier revision48 lifecycle
passes real email whitelist/login, Google-controlled Set/Start/Disconnect/Set-again/
Stop and5 exact REST-matched fills on revision48. Final bot stopped, both slotsv2
Connected, venue orders0/perpetual flat. Seventeen protected quote rejections and
three probe timeouts remain explicit. The subsequent local TDD fix retires stale
configured entries for repricing and reserves shutting-down levels; changed-image
Cloud qualification remains open and no unique historical trigger is claimed.
This is separate from local load/soak and dashboard acceptance.

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
