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

## Revision150 source deployment and connected follow-up

The full commit tag `3d59c383fd1e79aa3b7e0f59c352d367fa805882` independently
matches the deployed digest
`sha256:3044c394d7271f550c85643731d3844e8a1427665adedef04d7194c6e9e24a64`
on `hummingbot-demo-00150-nfn`, at100% traffic. Ready is10:48:06 ICT on2026-11-15.
Non-image revision settings match149. API catalogue/checkpoint limits are
2,500/controller and5,000/global; the original PnL peaks, session identity,
control/ack90 and until-stopped duration survive deployment. Initial
startup/live/ready all return200.

A GET-only observation from 10:52:17 to 11:22:18 ICT completes 901 samples on
one revision/session/control. Collection quality passes: wall span 1,800.889 s,
maximum sample gap 2.278 s and no failed API reads. The unchanged conservative
classifier retains unknown observations as failures and guards separately.

| Market | Two-sided samples | Eligible gaps | Guard excluded | Unknown | Conservative result |
| --- | ---: | ---: | ---: | ---: | --- |
| Spot | 459 | 13 | 420 | 9 | 459/481 = 95.43%; fails 99% |
| Perpetual | 0 | 0 | 892 | 9 | No confirmed eligible samples; unqualified |

Spot's conditional fraction excluding unknowns is 459/472 = 97.25%; it also
fails 99%. The 13 gap samples show submission pending (3), cancel confirmation
(1), executor activity (6), executor report (2) and proposal confirmation (1).
These are sampled stages, not continuous gap durations or a proved unique
bottleneck. Spot guards include order size, allocation and rolling outflow;
Perpetual BUY remains inventory blocked. No guard, threshold or classifier is
relaxed for acceptance.

Complete bounded recorded-history pagination returns 222 orders (162 Spot,
60 Perpetual) and 99 Spot fills (62 BUY, 37 SELL) in the same half-open window.
These are recorded orders/fills, not a count of fully filled orders or all
exchange activity. Final independent OKX GET matches both account bindings and
confirms two live Spot SELLs and two Perpetual SELLs on the long leg at 72,938.67,
the existing fee-inclusive breakeven quote.

Random advances through generations 2/3/4/5, including a repeated sideways
selection. At 10:57:27, 11:07:27 and 11:17:28, one sample per transition reports
`halted=true`, `next_action=wait` and explicit temporary shutdown-fence guidance;
each clears next sample. Running returns after approximately 10–12 s. All
nine unknown transition samples remain in the conservative denominator. There
is no observed drawdown breach or operator-required technical HALT; no recovery
command or live mutation is performed by this read-only follow-up.

At 11:22:49, startup/live/ready are all 200 and the bot remains running on150,
control/ack90, without HALT. At 11:22:51, native financial/binding hashes match,
owner537 remains unchanged, watermark advances to66145 and pending mutations,
pending creations, unapplied fills and uncertain submissions are zero. Original
PnL peaks and 2,500/5,000 limits remain exact. Initial watermark was65720 at
10:57:02; the watermark is journal activity, not a fill count.

The final bounded log review retains 56 WARNING/no application ERROR, restart
or ownership loss in the observation: 17 loop-stall/recovered pairs,15 slow-stage
records,5 probe warnings and2 protective `spread_policy_quote_rejected` events.
In-window probes occur at 10:57:38,11:03:04,11:05:58,11:09:42 and11:17:39;
elapsed time is300.822–301.060 ms with maximum probe gap1.305–1.306 s. All
recover. Three earlier warnings at10:49:21,10:49:27 and10:51:03 remain separate
(300.816–301.004 ms). Repeated diagnostic records are not automatically separate
slow operations. Application INFO is still excluded by the project rule; complete
submission-stage correlation and a unique probe cause remain unqualified.

Final service readback preserves 100% traffic and the complete original service
spec. The bot is left running. This is a successful source-policy deployment
and observed continued trading, not a passed two-sided quote or probe-performance
acceptance. The earlier 95.06% result remains in its original window; this is
not a controlled performance comparison.

## Source correction for the false recovery-checkpoint label

The earlier147 checkpoint is valid but contains an active drawdown breach.
`recovery_blockers` incorrectly equated `operator_action_required=true` with
invalid checkpoint data. It now uses the native invalid-checkpoint reason while
retaining `recovery_risk_stop`. No risk limit, peak, latch, reset authorization,
financial mutation, MM cycle or schema is changed.

Four native/HTTP and two real PostgreSQL RED failures reproduce the wrong extra
`recovery_checkpoint_invalid` label for controller/global breaches. Final142
native and34 PostgreSQL checks pass. Malformed scope/nonfinite peaks still
report invalid data, a valid inactive bound policy permits technical review,
and actual reset requests for active risk fail before any receipt/audit/state
change. HTTP keeps Recover unavailable and returns the existing risk guidance.
Swagger is unchanged; hooks pass after isort's formatting-only correction.
The same local code is also replayed against the actual quiesced pre-maintenance
checkpoint: it returns only `recovery_risk_stop`, preserves the original state
and performs no live command or DB write.

This source correction is absent from the measured150 image and deployed by151
as verified below.
Evidence for source deployment, connected observation and the separate local
TDD is private under `build/drawdown-policy-postdeploy-20261115/`.

## Revision151 recovery-label deployment verification

Cloud Build for full commit `4b302019a3bb90875588b06f35e513d84f53c0ab` succeeds
at12:02:12 ICT on2026-11-15. Revision `hummingbot-demo-00151-mb4` is Ready at
12:02:05 and serves100% traffic. The immutable revision and the full-commit
artifact independently match digest
`sha256:e265accef80cb39d766fa97706c2d39757554ff7e18a4a34280a3525b18baae2`.
Non-image revision settings match150. During reconciliation the template changed
before the Ready revision changed; final proof uses the immutable revision
digest/Ready/traffic, not the template alone. The initial template-only candidate
is retained separately and is not used as deployment proof.

The original session, until-stopped mode, control/ack90, PnL peaks and2,500/5,000
limits survive the handoff. At12:08:49, read-only native financial/binding hashes
match with owner538, watermark66817 and no pending mutations/creations, unapplied
fills or uncertain submissions. Live OpenAPI paths/components equal the repo.
`GET /recovery` returns the healthy running-state blockers, Recover unavailable
and no false checkpoint-invalid code. No live breach is induced and no recovery
command is sent; active-risk rejection remains qualified by native/HTTP/PG TDD.

A short smoke from12:07:34 to12:10:35 has91 samples, no runtime/error/identity
failures and the same session/control. It begins during guard recovery after
Random advances from generation9 to10; running is observed again at12:07:40.
It does not cover the complete earlier shutdown fence or constitute another
30-minute quote/performance acceptance. Since Ready through12:10:35, complete
bounded recorded-history pagination gives49 orders and14 Spot fills (13 BUY,
one SELL). At12:11:41, startup/live/ready are200 and151 remains running/no HALT.
Independent venue GET at12:11:48 matches both accounts and confirms one Spot
BUY/two Spot SELLs and two closing long-leg Perpetual SELLs at breakeven72,938.67.

The151 log window through12:10:35 retains20 WARNING/no application ERROR,
restart or ownership loss: five stall/recovered pairs, six slow-stage records
and four probes (300.950–301.114 ms; maximum probe gap1.307–1.310 s). Probes
recover. INFO retention and the previous95.43%/Perpetual quote gates remain open;
this read-path correction proves no MM latency improvement.

Separate predecessor150 evidence includes an ERROR at11:57:27, before this
deployment: native `OKXExpiredOrderRejection`, venue code50036. The recorded
order is terminal `OrderFailure`, `creation_pending=false`, zero recorded fill.
The bot is running afterward, with no HALT. This error is retained and is not
attributed to151 or represented as bad credentials by the canned network warning.

All verification is GET/read-only. The bot is left running; no service config,
credential, risk policy or financial row is changed by the verification. Private
evidence: `build/recovery-label-postdeploy-20261115/`.

Latest readback at12:16:20 ICT still has151 running/no HALT, control90 and all
three health endpoints200. Extended retained logs from Ready through that
timestamp have29 WARNING/no application ERROR/restart/ownership loss; the probe
count remains four. Additional stall/recovery records are retained. The own
local read-only SQL proxy is stopped; Cloud Run and trading are left active.
