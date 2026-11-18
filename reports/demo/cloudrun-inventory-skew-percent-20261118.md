# Demo Spot percentage skew: connected verification

Observed 2026-11-18, Asia/Ho_Chi_Minh (UTC+07). CI deploys exact commit
`ca1b3c7119cc6485dcaefee69f372d95ae0ac6e8` as `hummingbot-demo-00171-9ph`,
Ready with 100% traffic. The resolved image digest is
`sha256:7417b976d1bd7f3f21bb6b276c3c153069f09b7340da22fa38eb52d23fe95abf`.

## Approved policy applied through normal Switch

Fresh process/owner identity matches revision 171 and Demo before mutation.
The old running session retains catalogue `2e4c3995…`; deploying alone does not
change its multiplier policy. A full catalogue comparison proves the new
`51344236…` catalogue differs only at `markets.spot.inventory_skew`: enabled,
target_base_pct=0.5, inventory_range_pct=0.02. All other profile, budget, cap,
drawdown, outflow and Perpetual reduction settings match the pinned catalogue.

Versioned API Switch `31c33145-088d-486b-8bce-138edab28ce2` succeeds at
control/ack 99, session `2601d796-13fa-4f79-b13b-283a6a866b9d`, both markets,
fixed follow_market, until_stopped with no expiry. Its Basic-admin accepted audit
is verified through the normal audit API. The new session's pinned catalogue hash
matches `/profiles`. No manual SQL, HALT reset, credential/config-environment
change or direct exchange order/close call is used.

## Short running observation

Between 03:21:03 and 03:24:02 local, 18 samples span approximately
179.95s. All samples remain fresh, running/no HALT on revision 171/control 99;
startup/live/ready return 200 throughout the sampled window. All 18 Spot samples
observe active portfolio_pct sizing with half-width 0.02 and two working BUY plus
two working SELL orders. Initial BTC weight 50.2413% gives approximately
BUY 0.8793 / SELL 1.1207, rather than suppressing BUY at this small imbalance.

One new recorded Spot SELL fill is observed at 03:23:18 local: 0.00044186 BTC at
64,033.9 USDT. No new BUY or Perpetual fill is observed in this short window.
Perpetual remains two closing SELL quotes at 72,938.67 with BUY inventory-limited;
LONG 0.0498 BTC and its entry price match before/after. Both credential slots remain
Connected. Bot is left running; no Stop is sent.

## Retained limits and diagnostics

The bounded revision 171 application-log read contains no ERROR, child restart,
service-stop or traceback entry. Startup/Switch diagnostics still include one
approximately 300.9 ms probe timeout, two loop stalls/recoveries (maximum 398.2 ms),
and four slow-stage diagnostics. These precede the sampled running window; this
feature is not evidence that the latency issue is resolved.

The deployed frontend bundle `/assets/index-DBy-if2N.js` contains percentage-band
and legacy-multiplier labels. This is artifact verification, not a signed-in
browser acceptance run. The local feature qualification remains 286 backend and
755 frontend tests, plus schema/type/lint/build/hooks checks.

This closes the approved policy-application and short quote-observation scope.
It does not close 30-minute two-sided coverage, sustained fill quality, Perpetual
breakeven-fill handoff, load/soak or production acceptance gates.
Private receipts/samples/proofs: `build/inventory-skew-percent-postdeploy-20261118`.

## Later deployment check: Overview reporting

At 10:25 local, Cloud Build `7e480427-836e-4070-af33-b752e14ec0e0` successfully
deploys `925aa384fae98b18dfd753d12314987b1c3b814a` as revision `00172-w24`,
Ready with 100% traffic. The resolved OCI image label independently matches the
commit; digest `sha256:0e1fb55483e3209213f77b3072e056e1b6a313c5e4c581cfd374907ceb893011`.
The deployed OpenAPI equals the committed specification. Frontend bundle
`index-C5r73G6S.js` contains the new planned-width and unverified-limit labels;
this is artifact verification, not an authenticated browser acceptance test.

The bot is **not trading** at this later check. A persisted technical HALT began
at 05:14:28 local on revision 171, before this deploy. Cloud logs show
`LockNotAvailable` while recording a BUY intent: the ownership `FOR SHARE` fence
waited for a lock on `bot_state` and timed out. Persistence subsequently became
unhealthy; unsent rejection could not finish durably, followed by a child restart
and bootstrap withheld. The transaction holding the conflicting lock is not
identified by these logs. No drawdown-policy increase is indicated by this cause.
`/startup` and `/live` return 200, `/ready` returns 503. During handoff the API
process reports revision 172 while the retained owner observation identifies 171.
By 10:32 both process and owner observations identify 172, fresh at control/ack
99, but the persisted HALT remains. This does not qualify trading or a
30-minute window on the new image.

The real `/status` check also exposes a gap in the Overview change: its SQL read
omits the archived catalogue, so direct full-row unit fixtures missed unavailable
quote budgets and controller market labels. A local follow-up retains only
bounded pinned policy fields in that same SQL projection, bound to session ID
and generation. A real PostgreSQL regression fails before the fix and passes
afterward; 65 unit and 10 PostgreSQL tests pass, including reporting isolation
and excluding a 500 KB unused catalogue payload. This follow-up is not deployed.

No Stop, Recover, Start, manual SQL, environment or risk-policy mutation is sent
during this read-only check. Deployment and incident evidence is retained under
`build/overview-postdeploy-20261118`; technical HALT recovery and running
qualification remain open.
