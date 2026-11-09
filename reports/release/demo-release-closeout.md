# Demo and release closeout — 2026-11-10

Qualification is in progress. The69fa11070 cohort passes native, PostgreSQL,
all29 image E2E and the thirty-minute quote target, then exposes a fresh-close
REST visibility race during post-window cleanup. Its successor is under TDD and
needs fresh image/release qualification. No full load/soak is accepted here yet.
This report supersedes no historical failure or image scope.

## Candidate

- Runtime source: `69fa11070a242c21f7d916008e1656c73a81502c`.
- Regular image: `8d4a4ffc…`, registry digest `sha256:11005d1c1ac1f59acd563c395accd5a20d086c227ae61adaa2b65910b91b3d26`.
- Temporary Demo overlay: `ae809eb6…`, digest `sha256:114415592ce86afb0c59a022cbe9787bba5cee1cf7037a45a107a25798119f83`.
- All 1,092 packaged Python sources match; changed runtime modules import from
  their verified Python files. The runtime image is 1,375,508,713 bytes.
- Canonical native: **3,072 passed +16 subtests**. Full disposable PostgreSQL:
  **605 passed**. Runtime and measurement fixtures are frozen separately.
- Packaged E2E: **all29 groups pass in one `all` execution**, including the
  distinct-image upgrade/takeover/rollback scenario. Its preceding comparison
  `3f6c17db…` has known defects and is not an approved production rollback.

## Confirmed defects and TDD

| Defect | Reproduction and correction |
| --- | --- |
| Orphan PostgreSQL session retains ownership after the client exits | A real proxy blackholes both directions and retains the upstream session. Old code retains the advisory lock beyond 35 seconds. A 30-second server idle-session limit permits safe takeover; healthy owner liveness and idle reader reconnection have separate controls. Takeover still preserves unresolved-effect HALT. |
| Flat perpetual SHORT checkpoint overwrites LONG accounting | Native and PostgreSQL store/reload/store tests reproduce two flat summaries becoming BUY. Preserve each flat hedge leg's original side, keeping its independent volume, fees and realized PnL. Nonzero direction and spot netting are unchanged. |
| Recovery rejects provable spot fees paid in BTC | Red tests reproduce rejection. Convert the original base fee at that verified fill's price only when it exactly equals the persisted quote fee. Preserve gross quantity; unsupported assets and perpetual fee forms remain blocked. |
| Immediate cleanup close readback mistakes temporary REST absence for an unexplained failure | Actual Demo and a new old-image Linux test return51603 before the close is visible, despite actual fills. Native Red5/control1; retain one order identity and poll within the existing20-second deadline. Related Green56; successor image gates remain open. |

Commits: `c1653aa11`, `6d49d4b8e`, `69fa11070`. Separate earlier harness fixes
corrected an observational recovery-check race and a two-read inventory assertion;
those failures are not classified as trading runtime defects.

## Actual Demo incident and recovery

Revision70 lost ownership after three-second PostgreSQL I/O deadlines around
21:11 UTC /04:11 local. The old idle server session retained the lock after the
client exited. New engine ownership was blocked; API observations became unknown.
The engine failed closed and unresolved effects retained HALT. The thirty-minute
attempt failed and is retained. The preceding revision68 window was interrupted
by a CI rollout around minute25 and is also not a full-window pass.

Under delegated Demo incident authority, Stop was acknowledged, service instances
were temporarily reduced to zero, the DB was backed up and five known pending
orders were canceled with independent terminal readback and maintenance audits.
An exact transaction was first rehearsed on a disposable restore, including full
rollback on drift, rejection of a risk HALT and rejection of repeat application.

At22:15 UTC maintenance audit107 appended three REST-proven missing fills,
updated eight terminal order projections and repaired three aggregate positions.
All258 original fills, order identities/history, keys, funding and risk
policy/peaks/latches were preserved. The transaction removed only the incident's
technical HALT and cleared the quiesced runtime marker. Independent readback
verified261 actual fills, exact original fees, unique identities, no pending
orders, hedge quantities and fee-deducted BTC cash0.964246143944.

Corrected revision71 starts stopped without HALT at34/34 and both keys Connected.
Audited perpetual Disconnect closes both actual legs in11 fill parts; same-account
Set reconnects version6 at36/36. All272 actual fills reconcile. A real Start/Stop
adds no fills and preserves both flat hedge rows' distinct fee/volume/PnL values.
The new thirty-minute observation uses a separate Start and one fixed revision.
Its first attempt is interrupted after266 seconds by the laptop Cloud SQL proxy's
five-second connection timeout/handshake EOF. Cloud ownership remains healthy;
audited Stop reaches40/40. Stopped REST readback matches276 actual fills and no
pending orders. The private observer's read-only Red/Green contract proves
5 connections/5 reads versus1 connection/5 reads after bounded transport reuse.
Connection/statement deadlines remain5 seconds and future failures still abort;
the runtime image is unchanged. A separate complete window is required.

That separate revision71 window completes1,788 samples over1,799.83 sampled
seconds, maximum sample gap1.75 seconds, with one physical owner and no HALT.
The frozen v2 scorer counts unknowns as failures and passes both markets:

| Market | Working both sides / conservative denominator | Target result | Eligible / all | Guard excluded | Unknown |
| --- | --- | --- | --- | --- | --- |
| Spot | 1,733 /1,745 | 99.3123% | 1,742 /1,788 (97.43%) | 43 | 3 |
| Perpetual | 764 /767 | 99.6089% | 767 /1,788 (42.90%) | 1,021 | 0 |

Ordinary gaps are9 spot/3 perpetual. The collector's older zero-unknown prototype
flag remains false; it is not rewritten. The declared failure-inclusive v2
analysis retains all three unknowns in the denominator. Most perpetual exclusions
are inventory-cap restrictions; this is not unconditional two-sided uptime.
Four actual filled post-only orders prove BUY and SELL handoff/replacement before
60-second refresh, exact REST quantities/prices/original fees and fresh matching
account observations. Stopped readback matches301 actual fills, no duplicates,
no pending orders, hedge quantities and BTC cash0.963459303944.

Post-window Disconnect then reports `cleanup_failed/OKXOrderNotFoundError` at
closing_positions. Independent GET-only proof finds both close orders filled
and no perpetual exposure. Retry of that same operation completes removal, then
Setv7 reconnects the same account; stopped44/44, no HALT. All310 actual fills and
original fees reconcile, pending orders0 and perpetual flat. Existing history,
spot cash and risk checkpoints remain. The old-image full load is intentionally
interrupted after passing warmup/mixed p99 136.67/212.59ms so the correction can be
qualified; it is not a completed soak or a numeric performance failure.

## Limits retained

- The original I/O delay's unique infrastructure trigger remains unproved.
  SQL read activity rises near the event, while Cloud Run minute CPU and memory
  means are modest. This is correlation, not proof of a specific slow query,
  network issue or storage issue. No shared Cloud SQL resources were changed.
- The ordinary recovery estimator still blocks the legacy spot completion-order
  cost basis with `position_baseline_unproven`. This exact incident repair uses
  the verified stopped anchor and actual archived completion witnesses; it does
  not relax the generic API's proof requirements.
- Quote availability is conditional on eligibility. Guard exclusions never count
  as working quotes, unknown/stale observations count against the conservative
  target, and zero eligible samples cannot pass. Temporary spreads/inventory
  targets differ from the baseline; improvements cannot be attributed solely to
  rolling refresh.
- Cloud Demo uses2 CPU/1GiB. Offline full-load application limits use2 CPU/2GiB;
  an offline soak does not establish the Cloud instance's memory capacity.
- Real GCP KMS, actual frontend acceptance and live funds remain outside this
  qualification. Technical operations use the actual Basic or maintenance audit
  principal, not an impersonated Google session. INFO collection is unavailable
  because the current identity lacks `logging.sinks.update`.

Execution checklist: [Demo closeout](../../docs/plans/DEMO_RELEASE_CLOSEOUT.md).
Private backups, exact before/after ledgers, source hashes and raw failed/passing
attempts remain under ignored `build/acceptance-closeout-20261110/`.
