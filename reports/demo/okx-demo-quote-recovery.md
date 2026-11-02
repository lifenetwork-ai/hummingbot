# Quote-recovery image connected OKX Demo verification

2026-11-02 (Asia/Saigon). **The connected order/session/restart protocol passes.**
There are **zero new natural fills**, so positive new-fill/fee processing is not
newly accepted. [Evidence](okx-demo-quote-recovery-evidence.json),
[plan](../../docs/plans/MM_RUNTIME_LOGIC_AUDIT_TDD_PLAN.md#quote-recovery-image-full-load-and-connected-demo--requested-2026-11-02).

Image `660bd38b`, manifest `fefa2546`, contains the terminal-confirmed executor
wait fix. Its [local regression and matched short diagnostics](../investigations/mm-quote-recovery.md)
retain their own scope. The separate full-load rerun is not inferred from Demo
success; its first attempt failed the burst API p99 budgets and remains failed.

## Preparation and unchanged policy

Authenticated REST, public/private WS, Futures/Hedge mode and available
instruments pass on the current packaged image. Both keys bind to the same Demo
account. No pending orders, HALT, commands or mutations are found. A read-only
clone verifies the stopped original volume unchanged; its 850 orders, six fills,
three position rows and 21 funding bills remain historical evidence.

The absent local service and DB containers are restored using the existing Demo
volume and private configuration. A private custom-format DB backup precedes
application startup; four order/fill/executor/position fingerprints are unchanged
after stopped startup. Runtime revision uses the candidate image's own metadata.
No manual database repair, seed reapplication, account-mode change or HALT reset
is performed.

Policy stays BTC-USDT spot200 USDT / perpetual400 USDT, three levels at
0.004/0.009/0.018 with50/30/20 allocation and existing guards, fee floor and
volatility defense. Follow-market retains BTC/ETH0.6/0.4; follow-token follows
AVAX. API controls sessions only; private YAML/env are unchanged. Service is
localhost8080, two CPUs/2GiB, original BOT_ID and database history.

## Observed order/session behavior

Each fixed case independently finds recorded post-only quotes, observes at least
65 seconds and Stops. Terminal reads validate all **407** new orders as canceled,
with exact original quantity/price. Independent quote snapshots verify
tick/lot/minimum and configured notional bounds.
These are actual OKX Demo orders, not simulator fills or real-money executions.

| Market mode | Profile | Confirmed terminal orders |
| --- | --- | ---: |
| spot | follow_market | 52 |
| spot | sideways | 12 |
| spot | follow_token | 30 |
| perpetual | follow_market | 34 |
| perpetual | sideways | 10 |
| perpetual | follow_token | 24 |
| both | follow_market | 45 |
| both | sideways | 22 |
| both | follow_token | 112 |

The remaining66 orders belong to Random. All407 order IDs map to structured
creation `session_id`;75 available durable executor associations agree. Every
fixed case has the expected instruments and BUY/SELL terminal orders. Initial
snapshots can show one side during replacement; they are not continuous coverage.
Timestamp-only session attribution is deliberately avoided.

Random advances at the original600-second step while retaining the same global
session expiry over active spot restart. This draw selects `sideways` again,
which is allowed; it does not prove a different-profile Random handover on Demo.
A separate active `both` restart recovers quotes with pinned session/anchors/
deadline/generation and durable exposure. Stop leaves no pending exchange orders.

## Independent final state and limits

No natural fill occurs. Historical fill content and three repeated reads are
unchanged. BTC spot cash delta is exactly zero; perpetual base exposure remains
long0.0084 BTC / short0.0027 BTC and matches the venue. Stored amount, breakeven,
fees, volume and realized PnL remain unchanged across no-fill restarts. These
checks do not claim new positive-fill or new original-fee processing acceptance.

Log review passes: INFO1547, WARNING53, **ERROR0/CRITICAL0**, all nonempty lines
parse as structured JSON. At protocol completion `/startup` and `/live` return200;
`/ready` returns503 while trading is stopped. No HALT, pending mutations or orders.

The first load attempt ran concurrently on the same host and failed burst p99
at1.153s/1.126s with no unexpected API errors. Concurrent Demo is a context factor,
not an established cause. After this independent Demo review, app and DB
containers are temporarily stopped for an unchanged isolated load retry.
Final service restoration is a separate checkpoint, not implied by this report.

Raw evidence: `build/quote-recovery-demo-20261102/`. Private backup/environment
stay under the existing private configuration directory. No credentials are
published. This is BTC Demo verification, not LIFE listing, live trading or
Cloud Run acceptance.
