# Demo drawdown policy: 2,500 / 5,000 USDT

Operator approval on 2026-11-15: “2500/5000 đi”, in response to the current
Spot drawdown breach. The approved limits are 2,500 USDT per controller and
5,000 USDT globally. This changes the tolerated decline from the existing covered
V2 PnL peaks; it does not deposit funds or reset realized loss or the peaks.

Before applying, revision `hummingbot-demo-00147-sh4` was healthy at startup/live
but inherited the `drawdown_controller_limit` HALT from revision146. At 10:19:37
ICT the native cold-start valuation measured Spot drawdown 1,480.91 USDT,
perpetual 397.01 and global 1,325.27. The old 1,000/controller policy still failed.
New-policy replay must pass before clearing the corresponding risk latch.

Scope of the approved maintenance:

- Update the repository Demo YAML and the deployed YAML to 2,500/5,000. The live
  image is an overlay of the exact deployed application image, containing
  only that YAML change; preserve its runtime configuration and code.
- Stop the existing session, quiesce the writer, acquire its exclusive advisory
  fence and reject concurrent state, ledger or deployment changes.
- Change only the matching drawdown policy, stop/breach flags and matching HALT;
  append a maintenance audit identifying the real operator decision. Preserve
  PnL peaks, every financial row, account bindings, credentials and other caps.
- Restore the service, verify native policy binding, then separately Start Demo
  with the prior Random/both/until-stopped settings using the actual authenticated
  admin principal. Read back health, risk, order activity and Cloud Logging.

The GET-only venue review matched both managed accounts, zero pending orders and
the 0.0498 BTC perpetual long. Spot cash reconciled from the independently audited
prefix plus unchanged original fill quantities and fees. This is net cash and
position reconciliation, not a fresh REST verification of every historical fill.
Financial and account-binding checkpoint hashes matched before maintenance.

The final quiesced valuation at 10:24:38 ICT gave Spot drawdown 1,446.73 USDT,
perpetual 402.51 and global 1,296.59. Both new-policy native replays and the
native checkpoint binding passed, retaining the exact previous peaks.

Applied at 10:25:56 ICT under the exclusive owner fence and financial table
write locks. Audit315 records the approved rearm. All other `bot_state` fields
and every non-audit table retained their before/after fingerprints: 44,251
orders, 11,270 fills, 37,198 executor rows and three positions were preserved.
No financial repair or new baseline was needed. Stop and Start use the actual
Basic admin principal, recorded as Basic in audits314/316; no Google principal
was impersonated.

Revision149, `hummingbot-demo-00149-dtx`, is at100% traffic on image
`sha256:0dae744cca6049f28d5be659fb2e02aebb1fb162d2f29825d076a439e7860c5a`.
Its eight original image layers retain identical digests and sizes; the only
added file is the Demo YAML. Container runtime configuration, env, CPU/RAM and
automatic min1/max1 match revision147. The initial packaging permission failure
was caught by running as the real container user, corrected and retained in
local evidence before deployment. The final packaged native parser passes.

Start succeeded at 10:27:27 ICT, control90/ack90. Thirteen GET-only observations
from10:28:00 to10:30:05 keep the same revision/session, running without HALT.
Startup/live/ready all return200; drawdown observations are fresh and report
2,500/5,000. History in the bounded post-Start range contains13 recorded orders
and one Spot SELL fill. Independent OKX GET confirms two live Spot SELL orders
and two live perpetual SELL orders on the long leg, priced at fee-inclusive
breakeven. Spot BUY reports `order_size_rejected`; perpetual BUY reports
`inventory_limit`. These guards and all exposure caps are unchanged. This does
not establish two-sided quote availability.

At10:30:15, native financial/account-binding hashes match, peaks remain exact,
and pending mutations/order creations, unapplied fills and uncertain submissions
are zero. The runtime watermark advanced normally from65238 to65250. Global
covered drawdown at10:30:05 is1,237.22 USDT, below the new5,000 limit.

Validation: 22 native drawdown tests, 15 profile tests, native dry-run/replay and
the packaged parser pass. This is a policy maintenance and short live check,
not a full release rerun or30-minute quote/probe qualification. A startup probe
timeout at10:27:23 (300.932 ms; maximum probe gap1.306 s) recovers without
restart/HALT. The bounded10:26–10:30:20 log review retains seven WARNING/no ERROR
or engine restart: two loop-stall/recovered pairs (364.95 ms at startup and
260.12 ms during trading), one probe timeout and two slow-stage records. Both
slow-stage records include the same4,631.51 ms controller-cycle span; repeated
reporting is not evidence of two separate slow cycles. These observations do
not establish a unique probe cause. Application INFO retention remains
blocked by the previously documented GCP logging permissions.

Evidence is private under `build/drawdown-2500-5000-20261115/`. The repository
YAML records the same approved limits so the next source deployment preserves
the live policy binding. No Git push is performed by this operation.
