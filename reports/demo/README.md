# OKX Demo verification

[All reports](../README.md) · [Current release](../release/mm-release-closeout.md)

Read-only checks, controlled sessions, natural fills and funding observations.
Results belong to the specific image/account environment recorded in each file.

| Report | Evidence and scope |
| --- | --- |
| [Quote-recovery image Demo](okx-demo-quote-recovery.md), [evidence](okx-demo-quote-recovery-evidence.json) | Image660bd38b: nine cases,600s Random step and spot/both active restart,407 independently confirmed canceled orders; no new fills. Separate full-load retry ongoing; Demo paused during load isolation. |
| [Latent-fix image Demo](okx-demo-latent-fixes.md), [evidence](okx-demo-latent-fixes-evidence.json) | Image3f5cbd0f: nine cases, Random step and spot/both active restart, 381 exact canceled post-only orders and unchanged historical fills; no new natural fill |
| [Earlier nine-case OKX Demo](okx-demo-current-image.md), [evidence](okx-demo-current-image-evidence.json) | Image715a read-only preflight, nine profile/mode cases, real Random step/restart and 404 exact canceled post-only orders; no new natural fills |
| [Local Demo startup](local-demo-startup.md), [evidence](local-demo-startup-evidence.json) | Rebuilt runtime smoke, preserved Demo DB/config and authenticated stopped-service API |
| [OKX Demo acceptance](okx-demo-acceptance.md) | [Protocol JSON](okx-demo-acceptance.json), [natural-fill JSON](okx-demo-fill-acceptance.json) |
| [Earlier natural-fill follow-up](okx-demo-natural-fill.md) | [Evidence](okx-demo-natural-fill-evidence.json); six real maker fills, reviewed aggregate repair and passing real fixed-image filled restart |
| [Explicit Demo preset validation](okx-demo-preset-validation.json) | Nine static-metadata selections and runtime/package smoke; no connected or full release gate |
