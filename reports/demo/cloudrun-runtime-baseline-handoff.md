# Demo runtime baseline and owner handoff — 2026-11-11

Status: baseline and graceful handoff verified; the temporary-profile 30-minute
conditional quote target passed. The stricter zero-unknown runner failed one sample.
Regular catalogue restored and Demo running on revision101; this is not blanket
release approval.

## Historical baseline

The operator authorized Demo qualification and a reviewed legacy baseline. The
one-time maintenance stopped trading and removed the writer before applying the
native baseline binder. It added a runtime checkpoint and marked verified
historical orders as `position_applied`. Order data apart from that application
marker, all fills, financial position rows, credentials and risk checkpoints were
unchanged. The already-terminal attempts and applied journal retained their values.

- 10,375 orders, 650 fills and three position rows were retained.
- Two complete OKX REST scans per market matched all fill identities, quantities,
  prices and original fees: 392 Spot fills and 258 perpetual fills.
- Existing account bindings matched the venue. There were no pending venue orders.
- The reviewed 613-fill Spot checkpoint plus 37 later fills reproduced all five
  financial position fields exactly through the native held-position calculation.
  Perpetual replay from zero also matched exactly. BTC cash conservation and fee /
  volume totals were checked independently.
- A restored, isolated PostgreSQL clone passed the native binder and bootstrap
  check. Changed risk state, financial position, binding, fee, operator control and
  repeated application were rejected atomically.
- Real application recorded audit 183 and journal watermark 19. Start on revision96
  published `cold_recovery_supported=true`.

After another five actual Demo fills, a read-only check found 655 fills, no duplicate
fill keys, no pending orders or unapplied journal work, and a checkpoint financial
hash matching current positions. Native `can_bootstrap` remained true. These are
exchange Demo fills, not offline synthetic fills.

## Shutdown-state presentation

The first rolling observation stopped its monitor when it saw `halted=true`.
Investigation showed the runtime had set the intentional `shutdown_pending` fence;
the next owner resumed automatically. It was not a manually latched failure.

The API previously rendered this verified fence as `contact_support` (or offered
Check Recovery when stopped). Regression tests reproduce that incorrect guidance.
The fix projects a sanitized shutdown flag and renders `wait`, while preserving
`halted=true`, disabling Start/Switch and manual Check/Recover, and keeping Stop
available. Stale ownership remains unavailable. Risk stops, pending mutations,
unsupported baselines and explicit operator-required incidents retain protection.
No admission, ownership, risk or financial-write logic was relaxed.

A second regression test found that retained automatic-recovery progress could
hide a real manual HALT behind `wait`. The final fix gives the real block priority
and permits an eligible stopped admin to Check Recovery, without allowing reset.

Validation: 37 selected API/operator cases and six PostgreSQL projection
cases passed. Another 56 liquidity/observer cases passed. The new liquidity tests
first reproduced two failures before the fix. Targeted pre-commit checks passed.

## Connected handoff and cleanup

| Observation | Result |
| --- | --- |
| Revision97 → 98, status fix rollout | 163 samples, no API errors; sampled running-owner transition bracket 15.43 s |
| Revision98 → 99, same fixed image | 158 samples, no API errors; transition bracket 16.46 s |
| Session / operator intent | Both handoffs preserve session and control61; no manual reset or financial repair |
| Shutdown fence on second handoff | Observed once: `halted=true`, `next_action=wait`, Start/Switch and Check/Recover disabled, Stop available |
| Post-cleanup venue proof | All 663 fills match two full REST scans per market: Spot399 / perpetual264; no pending orders, perpetual flat |

The transition brackets are from the last sampled running old owner to the first
sampled running new owner. They are not precise quote downtime. Full Cloud rollout
latency was longer while the old owner was still running. Probe warnings during
rollouts remain recorded; no ownership-loss or child-restart event was found in
the retained combined log slice.

The cleanup used the existing authenticated Disconnect workflow to cancel orders,
close managed perpetual positions, verify the ledger and remove that credential
bundle. Same-account Set then reconnected it while stopped. Actual audit identity
is Basic bootstrap, not an impersonated Google account. Start65 on revision100
retained a matching financial checkpoint and `cold_recovery_supported=true`.

On the qualification session, four completed perpetual orders were independently
matched to OKX REST and original fills/fees. Both BUY and SELL have a native
POSITION_HOLD transition followed by a same-level replacement before the original
60-second refresh deadline. All four report maker executions and have matching
fresh account-position observations. Replacement age from the original entry was
18.11 s (BUY) and 56.21 s (SELL); these are not submission RTTs. Stored close times
have whole-second precision. This proves actual fill handoff separately from the
rolling owner handoff.

## Thirty-minute connected quote observation

Revision100, image `66c9f8e14aa4…`, was stable from 01:15:04 to 01:45:05 UTC.
The window contains 895 samples, a 1,798.76 s sampled span and a maximum 5.23 s
sampling gap. It used the explicitly temporary profile recorded in the JSON
companion; hard position caps, fee floors and other guards remained enabled.

| Market | Two-sided | Eligible gaps | Unknown | Guard excluded | Conservative rate |
| --- | ---: | ---: | ---: | ---: | ---: |
| Spot | 880 | 5 | 1 | 9 | 880/886 = 99.323% |
| Perpetual | 128 | 1 | 0 | 766 | 128/129 = 99.225% |

The approved conditional 99% target passes even counting the unknown as failure.
The original stricter runner requires zero unknown and remains **failed**. No
sample was relabeled after seeing the result. Perpetual was eligible in only
14.41% of all samples: short exposure approached its unchanged 0.005 BTC cap.
This is not 30 minutes of continuous two-sided trading or regular-profile acceptance.
Eligible missing-side events were isolated single samples; where surrounded by
two-sided observations, brackets were about four seconds. Other intervals have
no exact downtime estimate. Maximum reported no-quote time was 6.05 s Spot and
215.11 s perpetual, including guard-blocked time, not all an engine stall.

The unknown Spot sample at 01:41:28 UTC had fresh data but no admission reason.
Code inspection and a failing test showed that the liquidity observer copied only
the controller reason, omitting the shared service reason (`exposure_limit` in the
contemporaneous status). The final observer fix uses that reason when waiting,
preserves local/stale-data reasons, and ignores old global reasons while running.
It performs no extra exchange I/O and does not change admission or risk behavior.
This fix was **not** present in revision100 and does not retroactively pass its gate.

The bounded log slice contains 12 warnings and no ERROR: one probe timeout of
300.905 ms (maximum probe gap 1.306 s), one local spread rejection, four WebSocket
1006 closures and quote-absence warnings. No owner change, child restart,
ownership loss or service stop was observed during the window. Sampling cannot
prove zero engine delay between observations.

After Stop, all 716 fills (412 Spot / 304 perpetual) matched two full REST scans
per market. The original 650 fills were unchanged. Native journal replay of 20
new Spot fill-bearing orders matched all five position fields exactly; perpetual
full replay, BTC cash conservation, fees and volume also matched exactly.
There were zero pending orders, duplicate fill keys or unapplied journal entries;
the checkpoint hash matched and native cold bootstrap remained valid. No further
financial repair or HALT reset was needed.

## Final running state

Revision101 (`b7d81b87a245…`) contains the final four-source-file observer/status
patch over base commit `4f44d4259`. Packaged files matched the tested workspace.
The regular catalogue hash was verified before Start67. It reports running,
no HALT and `cold_recovery_supported=true`; owner generation is468. Read-only
checkpoint verification still matches all positions and finds no duplicate fills
or unapplied journal entries.

The final observation has two Spot SELL orders; Spot BUY is size/allocation-blocked.
Perpetual has no working quotes because both sides report `inventory_limit`.
This is the regular profile enforcing existing exposure limits, not proof that
both markets are quoting. No cap was increased to make that observation pass.
The full 30-minute window was on revision100, not this final image.

## Remaining scope

- A fresh zero-unknown window on that final image remains unqualified. The measured
  conditional target above belongs specifically to revision100 and its temporary profile.
- Broader connected fault injection, including actual kernel OOM, is still open.

Private snapshots, full venue responses, audit receipts and sample streams are
retained under `build/cloudrun-runtime-baseline-20261111/`; credentials are excluded
from this public report. The JSON companion records exact image/source hashes.
