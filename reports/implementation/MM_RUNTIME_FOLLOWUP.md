# Runtime follow-up remediation

Date: 2026-11-07 (Asia/Saigon). Source phases follow
[this plan](../../docs/plans/MM_RUNTIME_FOLLOWUP_REMEDIATION_TDD_PLAN.md) and the
[read-only review](../investigations/mm-runtime-followup-code-review.md).
Only local source/native/PostgreSQL verification is performed. No existing Demo,
real exchange, Google/GCP or production change; no new image/hour qualification.

## R1 — Availability boundaries

Risk checkpoint/status collection and typed rendering now have a narrow protected
boundary. Unexpected view failures produce null observations with scoped incidents;
healthy observations clear them. Ownership/SQL failures and cancellation propagate.
Actual risk checkpoint writes and cleanup remain outside the optional boundary.

Stored outflow projection requires a valid policy, account/environment/pair binding
and integer coverage timestamp. Malformed stopped checkpoints remain retained and
the owner/control service stays available. A running intent with invalid stored
risk data uses the existing durable HALT before admission; raw checkpoints and
unrelated recovery fields remain intact. Required HALT-write failure loses
authority and propagates. Cold-HALT reconciliation is not bypassed.

The Stop path also uses protected drawdown observation capture before real cleanup;
a rendering error cannot prevent cleanup, while a real cleanup failure still
propagates. Semantic invalid-drawdown projections keep one continuing incident
instead of logging false recovery on every poll.

Fresh checkpoint incidents produce the English operator state **Risk data needs
review**, disable Start/Switch and retain Stop. Stale/render-only incidents do not
become cached risk authority. A null risk observation means unavailable, not disabled.

Initial native Red:12 failed/3 passed; extra checkpoint/Stop Red:7 failed;
operator view Red:2 failed/2 passed after correcting a test-only `actions` field
name. Thirty native + five real-PG cases are added. Related native87 and PG10
pass; focused suites overlap final broad results. Logs:
`/tmp/hbot-runtime-r1-red.log`, `/tmp/hbot-runtime-r1-extra-red.log`,
`/tmp/hbot-runtime-r1-operator-red-corrected.log`, `/tmp/hbot-runtime-r1-ui-green.log`,
`/tmp/hbot-runtime-r1-postgres.log`. Formatting/lint checks pass.

R2/R3 and final frozen-source regression remain in progress; this R1 report does
not claim those phases complete or inherit the earlier image's load acceptance.
