# Strict LIFE quote guards

Guards are opt-in: deployed defaults use `quote_guards.enabled: false`. Existing
budgets, post-only execution, ownership, absolute price/exposure limits and
position-preserving cancellation remain authoritative. No new `.env` option,
service, subscription, cache backend or database migration is required.

## Configuration

Put deviation policy in each market template and manual range in its sideways
profile. Profile/market selection uses existing authenticated admin commands;
`/profiles` exposes the policy and session snapshots retain it across restarts.
This partial example uses illustrative test values around 0.1 USDT. Review actual
instrument thresholds; these are not production risk defaults.

```yaml
markets:
  spot:
    # Retain existing connector, pair, budgets and absolute limits.
    quote_guards:
      enabled: true
      mode: strict
      max_deviation_bps: "200"
      resume_deviation_bps: "100"
      recovery_delay_seconds: "5"
      max_book_levels: 50
      max_own_orders: 200
  perpetual:
    quote_guards:
      enabled: true
      max_deviation_bps: "200"
      resume_deviation_bps: "100"
      recovery_delay_seconds: "5"
profiles:
  sideways:
    scenario: sideways
    anchor: session_start_mid
    sideways_range:
      lower_price: "0.09"
      upper_price: "0.11"
      resume_lower_price: "0.095"
      resume_upper_price: "0.105"
```

`abs(reference / qualified_market_mid - 1) * 10000` must be at or below the outer
deviation limit. Equality is allowed; 100 bps means 1%. Enabled guards require
explicit finite thresholds with `0 <= resume_deviation_bps < max_deviation_bps <
10000`. Recovery defaults to five seconds (allowed 0–3600). Inclusive manual outer
bounds must contain the tighter inclusive resume range and lie within absolute
price limits. A range on a non-sideways profile, shared market template or disabled
guard is rejected. `strict` never uses `initial_anchor` to bypass an empty/own-only market.
Explicit [listing bootstrap](life-mm-listing-bootstrap.md) adds a finite,
durably bound episode with the existing spending/drawdown protection.
The existing catalogue validates every profile against every configured market;
a manual-range profile therefore requires guards enabled on those market templates.

## Bounded market observation

The controller reuses connector books and active, recently terminal and lost
orders from the existing tracker. It reads at most `max_book_levels` levels per
side (default 50, allowed 1–200) and bounds inspected identities with
`max_own_orders` (default 200, allowed 1–1000). Other-pair identities also count
against inspection. Readiness/send checks make no full-book DataFrame, history
query or exchange request.

At matching prices, stable acknowledged unfilled orders contribute their known
remaining base quantity. Pending creates/cancels, partial fills, recent terminal
orders and private updates newer than the book contribute their **full original
amount as a conservative upper bound**. Only depth above the combined bound can
qualify. Smaller ambiguous depth is skipped; smaller stable acknowledged depth
is inconsistent. Thus private fills cannot turn a still-visible original own
amount into apparently external liquidity. Residual must exceed double rounding
noise and meet the venue's minimum base quantity. Perpetual WS snapshot quantities
use the existing contract-to-base conversion, matching REST snapshots and diffs.

Both usable sides, fresh non-crossed depth and consistent bounded observations
are required. Missing competitive acknowledged orders, insufficient filtered
depth or exceeding the identity limit yield `market_price_unavailable`. Enabled
freshness cannot exceed the existing 30-second terminal-order cache; the normal
default remains ten seconds. Recently filled/cancelled orders can conservatively
reduce qualified depth until that cache expires, delaying quotes on thin markets.

Public/private streams have no atomic common snapshot. Known-order filtering is
an observation, not proof of independent fair value. It cannot identify unknown/
manual orders or other accounts' orders, or prove that no exchange update is missing.

## Recovery and API

An enabled guard starts waiting. At least two distinct public book observations
must pass the tighter boundary and the configured delay must elapse. Only the
controller's existing control loop samples recovery. Readiness/final-send checks
can revoke admission but cannot advance recovery or manufacture samples. Invalid
observations reset recovery; restart resets the delay while retaining session
anchors/deadlines. First admission captures `session_start_mid` from qualified
target depth. Repeated checks preserve the existing spread multiplier.

Breaches cancel quotes through the existing position-preserving path. In `both`,
either required market closes shared admission and both quote sets are cancelled.
The existing final transport gate rechecks after throttling; queued creates cannot
bypass a breach. Cancels remain allowed. Recovery cannot clear durable HALT,
renew expiry or restart an operator-stopped session. `/live` stays healthy while
the service progresses; `/ready` waits for trading admission.

`GET /api/v1/admin/status` adds optional `markets[].quote_guard` (null when disabled):

| Field/reason | Meaning |
|---|---|
| `ready`, `reason_code` | Admission and current waiting reason |
| `market_price_unavailable` | No qualified two-sided observation |
| `reference_deviation` | Reference exceeds its market-relative limit |
| `sideways_breakout` | Observed market is outside manual outer bounds |
| `guard_recovering` | Waiting for elapsed time and distinct tighter-bound observations |
| `data_quality`, `detail` | `known_order_filtered` / `unavailable`, plus bounded classification |
| `market_mid`, `deviation_bps` | Decimal strings when observed; null when unavailable |
| `book_observed_at`, `evaluated_at`, `recovery_started_at` | UTC timestamps, without claiming cross-stream synchronization |
| `recovery_valid_samples` | Capped at two; repeated reads cannot increase it |

Generate Swagger with `make swagger`; validate with `make swagger-check`.
Run the local simulator via `python scripts/test_e2e_offline.py --image
hummingbot-cloudrun:local --scenario quote-guards --trading-env demo`.
The short diagnostic fixture explicitly enables guards via `python
scripts/test_mm_load.py --image hummingbot-cloudrun:local --profile probe
--strict-guards`. Its thresholds are test configuration; this does not certify a
sixty-minute/all-enhancements release.
