# TDD plan for balance changes by asset

November 19, 2026. The first release implements date and session comparisons,
the read API and the Balances tab. New deployments apply migration 024 before
serving comparisons. Existing daily rows can be read immediately where both
boundaries have cash. Session baselines begin with eligible post-migration
sessions.

Local tests cover cash arithmetic, a retained session baseline, historical account
scope, stale and missing observations, HTTP validation, concurrent PostgreSQL
reads, and a held comparison query while publication and live balances progress.
The dashboard suite covers precision, period changes, account switching and older
backends. External flow history currently reports unverified.

A PostgreSQL rehearsal applied migration 024 to an existing daily cash history
and compared the rows before and after. All 27 focused PostgreSQL tests passed.
The packaged `initial-position-restart` scenario passed on the verified Demo
revision `00185-w56` runtime with the 12 changed backend files layered on top
(local manifest `sha256:2cf1a9fc583aaf5040c801dde531a9a7fc5e56d43d305be4f98dbecfdc7e94b4`).
It checked two simulated fills and matched both cash changes to the exchange
fixture after a clean restart of the same session. The scenario exposed the need
to accept a fresh opening observation from session preparation. The fix retained
the original observation time.

Two 60-second probes on the same packaged candidate passed the existing budgets
with 100,000 historical fills and 20 base reads per second. The second window
added a read per second for each balance comparison period. Both windows completed
60 simulated fills with zero unexpected errors. Base read p99 was 140 ms in the
first window and 108 ms in the second. The extra day and session reads had p99 of
171 ms and 173 ms respectively. The venue ledger recorded four order admissions
and eight cancellations during the second window. This sequential local run
covers the balance comparison probe. Connected Demo acceptance remains open.

```sh
python scripts/test_mm_load.py --image hummingbot-cloudrun:balance-changes-candidate \
  --profile probe --balance-changes-probe --probe-seconds 60 --history-rows 100000 --keep-going
```

## What operators will see

In **Overview → View balances → Balance changes**, operators select a period and see how much of each asset the wallet gained or spent. For example, selling BTC for USDT with zero fees produces these two rows.

| Asset | Opening cash | Closing cash | Change |
| --- | ---: | ---: | ---: |
| BTC | 0.10000000 | 0.08000000 | −0.02000000 BTC |
| USDT | 10,000.00 | 11,300.00 | +1,300.00 USDT |

Each row shows **closing cash − opening cash** in the asset’s native units. Cash includes funds reserved for open orders. Placing and then canceling an unfilled order therefore produces a cash change of zero. A fee paid in BTC appears once through the exchange’s BTC cash balance. Unrealized perpetual PnL can change USDT equity while USDT cash stays constant. Operators can read strategy PnL in **Performance**.

## Existing data sources

- The OKX connector records `cashBal`, `availBal` and `eq` separately. Use `cash_balance` for every asset. Identify each verified account by UID and environment so Spot and Perpetual observations of a shared wallet contribute only once. [OKX account observations](../../hummingbot/connector/okx_account_observations.py) · [API observations](../../hummingbot/cli/admin_observations.py)
- PostgreSQL stores nullable `cash_balance` and the observation timestamp per account, environment, currency, reporting time zone and day. The writer keeps the latest observation for each day. Historical rows are usable wherever they contain cash and a suitable timestamp. An equity-only row leaves cash unknown. [Snapshot model](../../hummingbot/model/daily_balance_snapshot.py) · [Writer and queries](../../hummingbot/model/admin_reporting.py)
- Balances is a panel on Overview. Its summary currently adds `equity − cash` across currencies with different units. Remove that sum in the frontend change. The Daily Statistics chart uses equity and serves a different calculation. [Balances panel](../../dashboard/src/features/overview/AccountBalancesPanel.tsx) · [Daily Statistics series](../../dashboard/src/features/statistics/balanceSeries.ts)

## 1. Define the opening and closing observations in tests

- [x] **Reporting dates.** Default to today. Follow the existing inclusive `from`, exclusive `to` convention with a maximum of 90 days in the reporting time zone. Use the last observation on the day before `from` for opening cash. Use the last observation on the day before `to` for closing cash, or the current verified observation if that day is today. Show both actual observation times. Leave a boundary empty if its day has no observation. Test midnight rollover, a time zone change, a missing day, a last observation well before day-end and an invalid future range.
- [x] **Current session.** Persist the first reliable cash snapshot once per bot, trading session, account and environment, including the actual observation time for each asset. Retries, reconnects, revision changes and cold restarts must preserve that baseline. A new session gets its own baseline. If collection starts after the session begins, label it “Since first observed at …”. For a session that predates the upgrade and lacks opening evidence, show “Session opening balance unavailable” and keep the date view accessible.
- [x] Compare observations from the same verified account and environment. Include every currency present at either boundary. If either observation is missing, cash is null or the current observation is stale, show the reason and latest available observation time, and leave the change empty. Preserve an explicit zero from the exchange. An asset first seen mid-session has an unknown opening balance.
- [ ] Write Decimal tests for the BTC sale above, a negative balance, a one-satoshi change, a sale with a BTC fee, an open order that changes only available cash, and a change in unrealized perpetual PnL with constant cash. Two connectors reporting the same USDT balance must produce one row. Calculate at source precision before formatting.

## 2. Backend and API contract

- [x] Add the proposed read endpoint `GET /api/v1/admin/balances/changes` with the `read` permission. Accept `period=days&from=…&to=…` or `period=session`. Derive account scope on the server from the verified binding. Return the requested period, reporting time zone, account scope, session ID when applicable, and rows containing `currency`, `opening_cash`, `opening_observed_at`, `closing_cash`, `closing_observed_at`, `delta_cash` and `reason_code`. Serialize amounts as decimal strings or null. Cover read authorization, invalid dates, a missing session, account changes and partial data through HTTP.
- [x] For date ranges, use the reporting worker to read only the two boundaries from stored balances and the current balance projection. Dashboard requests should use existing observations and skip both exchange calls and the fill/funding aggregates in `/statistics/daily`. Test the SQL with long histories and concurrent requests.
- [x] Add storage for session baselines. Batch the writes in the existing reporting path and validate the owner and session before committing. Commit the baseline and its asset rows atomically. PostgreSQL tests cover replay, stale owners, a snapshot missing an asset and
  reopening the store for the same session. The broader publication regression
  suite covers rollback at the final fence. On a reporting failure, expose the missing data and allow a safe retry while the engine continues trading. [Current writer](../../hummingbot/model/bot_state_store.py)
- [ ] **External flows.** Show a flow separately when evidence identifies the same account and currency and places the event between the two observations. The raw cash change includes deposits and withdrawals. In the first release, mark flow history as unverified where the source is missing. When an authoritative source is integrated, test deposits, withdrawals, transfers into and out of the trading account, duplicate events and Demo resets. Display verified events as they become available. Publish a flow-adjusted change only with complete history. A confirmed reset breaks continuity across the period. Identifying a reset or trade requires evidence beyond a balance jump.
- [x] Update OpenAPI, regenerate frontend types and document example responses. Test older payloads, existing `/balances` calls and a rollout where the new dashboard reaches an older backend. [Frontend integration contract](../fe-integration.md)

## 3. Dashboard and acceptance

- [x] Add a tab to the existing panel and fetch its data only when opened. Scope the cache by login session, account, environment, period and deployment
  revision, with the trading session ID for session comparisons. The deployment
  supplies the reporting time zone. Refresh the closing observation on **Refresh**. Test period changes and account switching. Reuse the authenticated query client’s
  existing session isolation.
- [ ] Test desktop and mobile layouts with gains, decreases, explicit zero and missing data for individual assets. Show signs and units independently of color. For known deposits or withdrawals, show both the raw change and the recorded flow. Keep totals within a single currency.
- [x] Run backend and PostgreSQL regressions, OpenAPI checks and dashboard lint, typecheck, tests and build. In the packaged end-to-end test, execute fills in the simulator's LIFE/USDT wallet, restart within the same session and verify both cash rows again.
- [x] Use the packaged load probe to measure response latency and order submission/cancellation progress with the balance comparison reads disabled and enabled.
- [ ] After the Demo deployment, reconcile opening and closing BTC and USDT against exchange `cash_balance` and `observed_at` for **those same two observations**. Check a historical day with cash at both boundaries, a day with a missing boundary and a new session with a persisted baseline. Verify a naturally observed order placement, cancellation or fill when available. Record the revision, period, balances, discrepancies and verified flow coverage before closing the checklist.
