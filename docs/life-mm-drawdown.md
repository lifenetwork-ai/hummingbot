# Managed MM drawdown

The PostgreSQL MM service can use the existing V2 runner's drawdown thresholds.
Both limits are optional and disabled in the default catalogue. Configure them
at the top of `life_mm_profiles.yaml`, alongside `max_total_amount_quote`:

```yaml
# Examples for testing only; choose limits for the actual strategy and capital.
max_global_drawdown_quote: "10"
max_controller_drawdown_quote: "5"
```

Amounts are positive finite USDT values; `null` disables that limit before the
policy is first armed. At least one enabled limit activates managed drawdown.
Zero, negative, NaN and infinity are rejected. The supported market scope is the
catalogue's stable OKX spot/perpetual controller IDs and their USDT pairs.
The service generates the runner metadata; do not configure a second runner or
add an inactive trading controller to observe a retained position.

## Measurement and coverage

The metric is `covered_v2_report_drawdown`: peak covered bot trading-report PnL
minus current covered report PnL. It is **not account equity loss or session profit**.
The global report sums both configured controller identities, even while only
one market is selected. Each controller also has its own peak. Equality with a
limit is permitted; exceeding either limit stops new risk for the entire MM bot.

The initial peak has the existing runner's zero floor, and includes existing
recorded report history. A new session does not establish a new baseline. For
example, first observation `-2` means a drawdown of `2` from zero. An existing
historical loss may therefore breach a newly armed limit immediately. A later
PnL of `20` establishes a peak of `20`; a PnL of `9` has a drawdown of `11`.

The service reuses executor reports, streamed historical executor performance
and recorded `PositionHold` exposure. Active/cached executor net PnL already
includes their reported trade fees. Managed held-position reports subtract the
separately recorded quote-fee total once; holding an executor must not erase its
trade fees. Funding remains **excluded** and is reported by the existing funding
and daily APIs. Initial wallet inventory, transfers and manual/account-wide
trades do not become bot report PnL automatically.

Historical executor performance is grouped by the existing controller ID. Before
first arming, establish that those IDs belong to the intended bot history; this
phase does not add account/environment attribution or reinterpret old executor
configurations. Once armed, the stable controller/market policy cannot be changed
through ordinary trading commands.

This preserves the upstream valuation conventions: active executor PnL uses its
existing executable-side price/quantity calculations; held positions use the
current midpoint. Base-denominated fees and quantity rounding can produce a
difference during handoff. Historical executor PnL/fee columns retain their
existing Float precision; PostgreSQL held-position amounts retain Numeric
precision. This adapter does not claim an exact account ledger or add a second
PnL calculation engine. Select limits with these report semantics in mind.

## Waiting, stopping and restarting

Report and risk-decision observations must be at most three seconds old. Required
marks must be positive, finite and within the most conservative configured market
data age. Both conditions are checked again at the existing final send gate.
Missing, errored or stale coverage is unavailable, never a fresh zero or a new
peak. Quotes are cancelled with filled inventory/positions retained; the process
and admin API remain available and admission resumes after valid observations
return. Session anchors and deadlines are unchanged.

After disabling a market with a retained nonzero position, the existing provider
keeps a public, non-trading order-book subscription for valuation. Read-only
initialization retries classified transient failures and is cancelled during
shutdown. No mode/leverage/order writes are needed for this observer. Unknown
recorded exposure outside the declared scope blocks admission and requires
operator review rather than silently omitting a position.

Confirmed breach uses the existing bounded, position-preserving service shutdown
and durable recovery HALT. It cancels partially filled remainders without placing
market closes. Normal manual-kill restart, profile switching, random steps,
spot/perpetual/both selection, stop/start and process restart cannot clear the
breach. Required checkpoint/persistence failures remain critical and cannot be
downgraded into optional reporting errors.

The existing `bot_state.recovery_state.strategy_checkpoints` stores policy,
stable scope, monotonic peaks and stop/breach state under `life_mm_profiles.yaml`.
No new table, migration, ledger, cache or reset endpoint is needed. Peak writes
occur only on changed state. Ownership fencing and command-version checks prevent
superseded startup from binding its old policy.

Once armed, the thresholds and scope are bound to that bot's existing checkpoint.
Normal MM commands can select profiles or markets under the same policy, but
cannot disable/change the limits, rename a scoped controller, change its pair or
switch to a legacy runner to bypass it. Policy changes require explicit reviewed
maintenance with the service stopped and exposure/history reconciled. Do not
delete checkpoints or clear HALT simply to resume quoting after a breach.

## Admin API

`GET /api/v1/admin/status` adds optional `drawdown`; the existing authenticated
`/profiles` response exposes the catalogue limits. Swagger includes these DTOs.
Amounts serialize as decimal strings and observation times as UTC timestamps.
When disabled, `drawdown` is `null`. No new command or authentication route exists.

The status includes readiness, reason, scope, global/controller peaks, observed
PnL/drawdown, limits, `operator_action_required` and explicit fee/funding coverage.
Stopped or stale observations preserve known peaks and confirmed breach, while
current PnL/drawdown are `null`. Important reasons are:

| Reason | Meaning |
|---|---|
| `drawdown_report_unavailable` | Missing, errored or old report/risk observation; wait for valid coverage |
| `drawdown_valuation_unavailable` | Required active/retained position mark is unavailable or stale |
| `drawdown_position_scope_unavailable` | Recorded exposure is outside declared coverage; operator review |
| `drawdown_checkpoint_unavailable` | Policy not bound yet, or required checkpoint write failed; inspect service HALT/incidents |
| `drawdown_checkpoint_invalid` | Persisted peak/coverage cannot be validated; operator review |
| `drawdown_global_limit` | Confirmed global limit breach; durable HALT |
| `drawdown_controller_limit` | Confirmed controller limit breach; durable HALT |
| `drawdown_not_running` | Preserved checkpoint without a current trading observation |

Local verification commands:

```sh
make test-cloudrun-regression
make test-postgres
python scripts/test_e2e_offline.py --image hummingbot-cloudrun:e2a --scenario drawdown --trading-env demo
python scripts/test_mm_load.py --image hummingbot-cloudrun:e2a --profile probe --drawdown --strict-guards
```

The probe explicitly enables test-only drawdown/guard thresholds. It does not
enable production defaults or replace the final E6 sixty-minute acceptance gate.
