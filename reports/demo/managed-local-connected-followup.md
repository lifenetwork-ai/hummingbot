# Managed local Demo — reviewed recovery and connected verification

2026-11-09, Asia/Ho_Chi_Minh. **The reviewed recovery and bounded connected
Random/restart/Disconnect checks pass on `4451974e…`.** The bot is left stopped,
without HALT, pending orders or managed perpetual exposure. This is actual OKX
Demo trading; it is separate from simulated load and production acceptance.

[Portable evidence](managed-local-readback-remediation-evidence.json) ·
[Approved maintenance](managed-local-random-recovery-proposal.md) ·
[Earlier failed run](managed-local-connected-evidence.json).

## Change and qualification

The sequential terminal readback path predates Google/KMS configuration. Commit
`aba5d745a` uses at most three scoped async readers, batches the shutdown call site
and drains sibling tasks on failure/cancellation. Both fresh terminal-proof
passes, fills-before-terminal ordering, the eight-second deadline, ownership,
checkpoints and HALT remain. Missing original per-request spans prevent attributing
every part of the old timeout to a unique remote request.

Native2,868 +16 subtests and PostgreSQL557 pass. Image
`sha256:4451974e9d980a9d4eab3522f35eef897343f056b1ef3774651609926d6cbb0c`
is built from `aba5d745a`, 1,374,605,855 bytes. All1,229 packaged files match;
real-signal smoke, paired controlled old/new readback witness and targeted
until-stopped/managed-lifecycle E2E pass on its pinned manifest. These are not a
new all-case release or sixty-minute soak.

The operator separately approved this incident's recovery. At **10:02 local**
(`03:02Z`), with all writers stopped and fresh independent REST/WS/account checks,
maintenance removed only `recovery_state.halt` and appended audit24. Control/ack
remained144/144. All other table fingerprints and bot-state fields matched;
checkpoint/risk flags, history, credentials and positions were not repaired or
reset. Before/after backups and the retained local KEK are outside Git/build.

## Connected results

| Flow | Verified result |
| --- | --- |
| Managed cold load | `postgres_kms`, local KEK; exact candidate image has no exchange-credential env variables |
| Random `both`, until stopped | Steps0/1/2, all three profiles observed in60 samples; same session resumes after active container restart |
| Random Stop | Succeeded; no HALT or runtime shutdown timeout |
| Disconnect perpetual slot | Succeeded in about4.1 seconds; four final proofs true: terminal orders, flat positions, committed ledger, detached consumers |
| Venue readback | Pending SPOT/SWAP orders0; managed perpetual long/short0 |
| Credential removal | Only selected perpetual envelope becomes SQL NULL; companion spot slot remains active |
| Reconfigure/restart | Same Demo key set through API; final spot/perpetual versions2/3 loaded; stopped cold restart succeeds without starting trading |
| Audit | Accepted/completed Set, Disconnect, cleanup progress, two close submissions and reviewed maintenance are available through the audit API |

The seven new perpetual fill parts belong to **two cleanup CLOSE orders**, closing
long0.0100 BTC and short0.0032 BTC. They are market cleanup fills, not natural
maker fills. Three preceding natural spot fills were produced on `b090430c…`.
All ten new fills match their individual trade-ID REST quantity/price/original
fee and five order aggregates. Thirty-three total fill rows remain after repeated
readback/cold restart, with an unchanged fill fingerprint: no duplicate fee or
position application. All23 pre-cutover fills remain value-identical and all3,513
pre-cutover order IDs remain present.

Spot BTC cash remains **0.998960180584 BTC**, exactly matching the saved starting
cash plus the three signed spot quantities minus original BTC fees. Disconnect
did not sell spot holdings. The API key still authenticates on OKX: Disconnect
removed the bot's saved bundle, not the exchange-side key. Final control/ack148/148,
both saved envelopes encrypted/active, no pending recovery and risk-stop false.

## Retained warnings and limits

Candidate logs contain no ERROR or probe-failure records. Four network warnings
concern last-price/server-time fetches. During cleanup there is one optional
`engine_reporting_unavailable`/ValueError incident, **10:05:13.372–10:05:17.054
local** (3.682 seconds), with a matching recovery event. Code inspection and a
classifier witness show the synthetic `credential_cleanup` name is not a known
strategy. The reporting fallback kept cleanup functioning; the incident did not
cause HALT/restart or invalidate terminal/position proof. Removing this reporting
warning was not fixed by the readback work. The subsequent source correction is
recorded below; this historical image/log result remains unchanged.

The initial Disconnect helper incorrectly required two intermediate-stage proof
fields on the final receipt. The failed helper/log is preserved. Inspection of
the existing stage contract and reinspection of the **same completed operation**
verify its four final proof fields, SQL NULL removal and fresh venue-flat result.
No second Disconnect was submitted and no runtime source/assertion was weakened.

Real GCP KMS/IAM remains deferred. Real Google login was verified separately in
the isolated auth helper; these actual trading controls used Basic headlessly.
This short connected run does not qualify a thirty-minute99% quote-coverage target
or continuous quoting between samples. The previous full offline load/soak result
belongs to `b090430c…`; the new candidate still needs its own full release/soak
before production closeout. No live account or Cloud Run deployment was used.

## Subsequent cleanup reporting investigation — 2026-11-09

The synthetic assignment dates to `4f284b9f84` (2026-11-05), before the current
terminal-readback fix. `CleanupExchangeRuntime.prepare()` sets `strategy_name`
to `credential_cleanup`, although cleanup uses the native connectors, clock and
recorder directly and never loads a strategy. `TradingCore.get_status()` passes
that name to `detect_strategy_type()`, which rejects it with ValueError.
`ServiceObservations.capture()` catches the projection failure, publishes known
running booleans and records the incident. Cleanup shutdown clears the name;
the next successful snapshot clears the incident. Thus3.682 seconds measures the
observed warning/recovery interval, not a3.682-second query, event-loop stall or
measured MM interruption. Cleanup intentionally stops ordinary MM first.

A regression test now runs `prepare()` with a real `TradingCore` and real status/
observation methods, capturing during market initialization and after clock start.
The old implementation fails with the same `engine_reporting_unavailable` /
ValueError incident. Earlier cleanup doubles did not exercise this integration.
A separate control test confirms unknown executable strategy names still raise.

The source fix sets `strategy_name=None` for the strategy-free cleanup context.
`strategy_file_name=credentials_cleanup`, ownership scope and operation ID remain;
the existing recorder fallback uses that configuration identity for new cleanup
records. Already recorded/recovered orders retain their original attribution;
no old ledger rows are rewritten. Cancellation, closure, mutation authority,
terminal proofs, deadlines and logging severity are unchanged. Native engine
discovery and the general reporting fallback are unchanged.

The related native suite passes96 tests; disposable PostgreSQL cleanup tests
pass109 (448 unrelated cases deselected). Red/Green log hashes are in the
portable evidence. This source correction has not
yet been packaged into a new image or retested against real Demo. The running
Demo service remains the already verified `4451974e…`, stopped/flat; its historical
warning is retained. Build/test the next release image from the corrected source
before production closeout; do not transfer the predecessor's qualification.
