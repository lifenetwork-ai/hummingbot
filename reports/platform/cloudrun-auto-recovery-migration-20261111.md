# Cloud Run automatic-recovery schema cutover — 2026-11-11

Status at migration completion: required schema installed and validated; revision 91 Ready with all traffic.
Revision 91 acquired ownership generation 460 by 07:20:02 ICT. Trading remains blocked by the
existing `drawdown_controller_limit`; no risk reset or baseline certification was
performed. The operator explicitly waived a database backup for this maintenance.

Subsequent separately approved policy change and restart on revision 93 are
recorded in [the drawdown report](../demo/cloudrun-drawdown-1000-2000.md).

## Diagnosis

Revision 87 used `ENABLE_AUTO_MIGRATION=false` and exited 78 before ownership with
`schema_incompatible`. Revision 88 enabled automatic migration, but the old writer
still held the physical database owner lock. Read-only validation found exactly:

- Missing `orders.position_applied`.
- Missing `runtime_checkpoints`, `runtime_journal`, `submission_attempts`.
- No `hbot_schema_migrations` history table.

Traffic remained on revision 86 while the new revisions failed startup. An absent
schema with an active owner cannot use compatible read-only migration standby.
This deployment issue was distinct from the earlier funding-observer warning.

## Actions and observed sequencing

Only service `hummingbot-demo` and its dedicated database were in scope. The service
configuration was recorded privately; this is not a database backup. A guarded
service-level manual-zero scaling update used the current etag and preserved the
application template. Its long-running operation reported the existing startup
failure even though the scaling setting was accepted.

Revision 89, created concurrently with the same application image/environment,
automatically installed the packaged scripts after the old writer released the
lock. Database history places all 21 scripts at 07:13:50 ICT, with migration 021
at 07:13:50.928957. An ownership poll still found a lock, but subsequent row readback
identified it as the new revision 89, generation 459, rather than the original
revision 86, generation 458. No manual migration command was executed.

A temporary maintenance-only revision 90 received traffic during verification.
It served explicit maintenance responses without starting the bot or connecting to
PostgreSQL. The original application container configuration and automatic scaling
(min 1 / max 1) were then restored in revision 91. Image, credentials, CPU/memory,
application environment and risk policy were preserved; auto-migration stays true
as selected by the operator.

Read-only validation passed and every recorded migration checksum matches the
packaged local SQL at commit `c8f023a4e`. Existing observed rows: 10,356 orders,
650 fills and 3 positions. No pre/post full financial fingerprint comparison is
claimed: automatic migration finished before the planned manual-migration capture.
The new journal, submission-attempt and checkpoint tables are empty; schema
installation does not certify the old ledger or establish a financial baseline.

## Remaining acceptance

- Completed: revision 91 acquired ownership after revision 89 retired; startup/live
  return 200. Ready returns 503 with `halted`, consistent with the existing risk stop.
- Review the existing drawdown stop separately; migration is not risk-reset authority.
- Verify the historical financial/account baseline before enabling automatic cold
  recovery for the existing ledger.
- Controlled Demo recovery and the 30-minute eligible two-sided quote objective
  remain open. Offline qualification is unchanged.

Private operational receipts are under `build/cloudrun-auto-migration-20261111/`.
No secrets or full service environment are included in this report.

Cloud Run manual scaling ignores min/max settings for traffic-serving revisions;
minimum instances for tag-only revisions are a separate exception. The earlier
attribution of the retained writer solely to revision `min=1` was not established.
See [Cloud Run manual scaling](https://docs.cloud.google.com/run/docs/configuring/services/manual-scaling).

Final readback: `/startup` 200 (`halted`), `/live` 200 (`api_running`),
`/ready` 503 (`halted`). Control version remains 57; no trading Start, HALT clear,
credential change or risk-limit edit was issued. The operator chose no DB backup.
