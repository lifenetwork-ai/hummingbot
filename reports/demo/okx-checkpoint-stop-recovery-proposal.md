# Demo strategy-stop checkpoint recovery — operator review

The previously approved expiry-intent repair was applied after fresh venue and DB
checks. The service started in stopped state with no HALT. Its subsequent first
start failed with `RecoveryRequired`: the retained strategy checkpoint contained
`_is_stop_triggered=true`, so initialization refused to resume and HALT was set
again. The bot is now stopped, no owner holds the database, and no submission is
pending. No new Demo order or fill was created by that failed start.

The private backup taken immediately after the earlier reconstruction has the
same checkpoint with `_is_stop_triggered=false`; the private backup after the
expired-submission shutdown has it `true`. Both have zero saved peak PnL and no
exited controllers, and the inspected checkpoint has no `mm_drawdown_breach`.
No drawdown/risk event was found in the locally retained MM events. The V2
strategy also sets this flag during normal `on_stop` cleanup. These observations
suggest the incident shutdown set it, but they do not prove no unrecorded risk
condition occurred. Clearing a strategy stop can remove a risk latch, so this
requires a separate explicit operator decision; the prior approval did not
include resetting this checkpoint.

A fresh read-only OKX Demo preflight found no pending orders and matched the
previously reconciled BTC cash and perpetual quantities. A private backup was
taken after stopping the bot. The proposed transaction, **not applied**, acquires
the existing owner advisory lock and checks stopped/acknowledged/no-owner, HALT,
no pending creates/account mutations, exact recovery-state fingerprint and
unchanged hashes of orders, fills, positions, order statuses and market states.
It sets only the checkpoint `_is_stop_triggered` and the incident HALT to false,
removes the incident reason, and leaves all other checkpoint and economic data.
A disposable clone dry run passed, preserved all listed fingerprints and rejected
replay. Fresh venue checks and a new backup are required before applying it.

Exact SQL: `build/checkpoint-poll-release-20261103/reviewed-checkpoint-recovery.sql`.
SHA-256: `d87b218164dfb07108ef6f00e193776a8a2e27bcca4458e2ee9638325b41864f`.
Dry-run evidence: `build/checkpoint-poll-release-20261103/checkpoint-recovery-dryrun.json`.
See [PostgreSQL recovery](../../docs/postgres-runtime.md): reset requires an
explicit operator-reviewed database maintenance change after reconciliation.
