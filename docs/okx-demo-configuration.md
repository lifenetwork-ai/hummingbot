# Explicit OKX Demo preset

Use this preset to exercise the existing PostgreSQL service and admin API with
BTC-USDT spot/perpetual before LIFE is listed. The default LIFE catalogue and
shared environment defaults remain separate. The preset contains example paper
limits; select actual LIFE policies separately when its instruments are available.

| File | Purpose |
| --- | --- |
| [Demo catalogue](../deploy/cloudrun/demo/life_mm_profiles.yaml) | Three profiles, both markets, explicit enabled policies and limits |
| [Demo environment example](../deploy/cloudrun/demo/env.example) | PostgreSQL, migrations, admin API and dedicated Demo credential names |
| [Demo image layer](../deploy/cloudrun/Dockerfile.demo) | Inherits the existing runtime and packages this catalogue at its normal path |

## Enable switches

Process environment overrides local dotenv values, which override shared code
defaults. Cloud Run uses injected values instead of reading dotenv files.

| Switch | Enabled | Disabled |
| --- | --- | --- |
| `ENABLE_AUTO_MIGRATION` | Runs startup SQL before trading admission | Validates the existing schema without applying migrations |
| `ADMIN_API_ENABLED` | Exposes Basic Auth control/reporting/Swagger endpoints; credentials required | Health endpoints remain separate; admin endpoints are unavailable |
| `quote_guards.enabled` | Qualifies outside depth and enforces reference deviation/recovery | Ordinary price, balance, inventory and ownership checks still apply |
| `inventory_skew.enabled` | Changes spot BUY/SELL quantities from account cash inventory | Uses the ordinary configured allocation |
| `spread_policy.fee_floor` | Widens insufficient spreads using estimated maker fees | Applies no optional fee floor |
| `spread_policy.volatility.enabled` | Scales spreads using completed-candle NATR | Uses configured fixed spreads, with fee floor if enabled |

For this preset, missing optional candles selects its explicit static fallback;
the process continues and the fee floor remains enabled. A strict guard with no
qualified two-sided depth waits for recovery. Inventory skew can suppress a side.
Fee coverage of proposals does not guarantee realized profit.

`max_global_drawdown_quote` / `max_controller_drawdown_quote` enable their limits
when positive and disable them when null. `spot_outflow` enables its spending
policy when populated and disables it when null, subject to existing durable
policy bindings. Its `fee_allowance_rate` is a reservation allowance, not the
exchange's actual fee rate. All fields are configuration of the existing engine.

## Availability while policies are enabled

Track service availability and trading admission separately. Guard waits retain
the running process and admin control while their required DB path is healthy.
`/live` can remain healthy while `/ready` reports that trading is not admitted.
Status exposes the reason rather than promising continuous quote uptime.

- A quote-guard breach cancels quotes and waits for qualified recovery. In `both`,
  required-market readiness can close both quote sets.
- This preset's optional-candle failure uses static spreads; the fee floor and
  all mandatory feed/ownership checks still apply. Unknown fee basis waits.
- Inventory skew may intentionally remove one quote side; unavailable optional
  inputs use neutral sizing while mandatory balance checks remain authoritative.
- A covered spot outflow cap blocks BUY; funded SELL and cancellation remain
  subject to their existing checks. Binding/provenance failures require review.
- A drawdown breach triggers the existing scoped stop/HALT behavior. Restart does
  not automatically erase a bound loss peak or unresolved effects.
- Lost DB ownership, recorder failure or unsafe cleanup can still require a
  controlled process exit. The existing durable recovery rules apply afterward.

The E6 [availability/load evidence](../reports/life-mm-e6-verification.md)
describes the tested runtime artifact and fault scopes. This configuration
example does not change those runtime rules or certify a new image digest.

### What to do when quoting is blocked

Read `GET /api/v1/admin/status` first: process/session phase, market readiness,
`quote_guard`, `spread_policy`, `inventory_skew`, `spot_outflow`, service incidents
and their freshness/reason fields. A command receipt alone does not imply admission.

| Reported condition | Response |
| --- | --- |
| Stale feed, `market_price_unavailable`, guard recovery | Check connectivity, book freshness and outside depth; the normal loop retries/requalifies automatically |
| Reference deviation | Inspect the selected reference pairs, model anchor and configured boundaries; correct an erroneous configuration through the normal reviewed configuration workflow |
| Candle fallback | Verify explicit static spreads are effective; optional candles can recover on the same controller |
| Unknown fee basis | Inspect fee/currency observations; new quotes wait until the basis is usable |
| Spot capacity exhausted | Inspect recorded outflow and reserved BUY amounts; capacity is reevaluated as the rolling window/cashflow changes and terminal orders are proven |
| Only one spot quote side | Inspect observed cash inventory and skew target; zero allocation may be an intentional policy result |
| Drawdown or unresolved-effect HALT | Stop/review exposure, orders, fills and fees against exchange truth; follow the existing operator-reviewed recovery procedure |

Funding a wallet does not erase recorded trading outflow. A local cancel request
alone does not release uncertain reservations. Restart does not reset HALT or
bound policy/peak state. There is no generic admin reset-HALT endpoint. Follow
[ownership/recovery operations](postgres-runtime.md) before any maintenance;
resolve provenance and pending effects rather than changing only the halt flag.

For a future thin LIFE listing, choose the bounded bootstrap policy before that
session using its guide and required risk bindings. The BTC strict preset does
not synthesize outside liquidity or automatically turn bootstrap on when blocked.

## Effective behavior

| Setting | Demo value and effect |
| --- | --- |
| Environment | `demo`; dedicated Demo credentials for both connectors |
| Persistence | PostgreSQL, automatic migration enabled, `BOT_ID=hummingbot-demo` |
| Control | Basic Auth admin API enabled; username/password required |
| Markets | BTC-USDT on `okx` and `okx_perpetual`; API selects spot/perpetual/both |
| Quote allocation | 100 USDT per market, combined maximum 200 USDT |
| Follow market | BTC-USDT 60%, ETH-USDT 40% |
| Follow token | AVAX-USDT 100% |
| Sideways | Captures `session_start_mid`; no price-specific absolute range |
| Random | Equal selection among the three profiles, ten-minute steps |
| Sessions | Default 30 minutes; accepted duration 60–7200 seconds |
| Quote guard | Strict, 200-bps deviation limit, 100-bps resume boundary, five-second recovery delay |
| Drawdown | Global 25 USDT, per controller 15 USDT from covered V2 PnL peaks |
| Spot outflow | 500 USDT per rolling hour, with a 1% fee allowance for admission |
| Maker fee floor | Enabled on both markets |
| NATR | Enabled, completed one-minute candles, length 14, target 0.01, multiplier 0.5–3 |
| Candle fallback | Explicit 0.1%/0.2% spreads; fee floor still applies |
| Spot inventory skew | Enabled; target 50% BTC share of observed BTC+USDT cash value, range multiplier 1 |
| Perpetual | HEDGE/CROSS, leverage 1; long/short/gross caps 0.005/0.005/0.008 BTC |

`total_amount_quote` limits proposals; the spending cap, actual account balance,
inventory/exposure limits, post-only checks and ownership fence remain independent.
The drawdown limits are not wallet-equity stops. Inventory skew uses account cash,
so an imbalanced Demo wallet can reduce one quote side to zero. Adjust its target
for the test wallet if two-sided quoting is required. A lack of fills is possible:
the fee floor and spreads remain enabled and the bot never forces a match.

BTC uses strict external-depth qualification. Listing bootstrap is intentionally
absent from this liquid-market preset; use the existing
[bootstrap guide](life-mm-listing-bootstrap.md) and offline simulator for
empty/own-only LIFE books. Perpetual has no spot-cash inventory skew.

## Build and run locally

Use a dedicated Demo account and PostgreSQL database with known exposure/history.
The service reconciles existing exchange positions and orders before trading.
An empty DB does not establish provenance for an account's existing positions.
Keep the corresponding DB when preserving positions. Drawdown/outflow policy
bindings persist; changing account, pair, policy or controller IDs requires the
existing reviewed maintenance process, rather than resetting HALT or deleting rows.

Copy the environment example outside the repository and fill its database URL,
admin password and six Demo credential fields. Leave the shared/private files
used by earlier acceptance runs separate:

```sh
mkdir -p "$HOME/.config/hummingbot"
cp deploy/cloudrun/demo/env.example "$HOME/.config/hummingbot/cloudrun-demo.env"
chmod 600 "$HOME/.config/hummingbot/cloudrun-demo.env"
```

The example DB host `host.docker.internal` assumes Docker Desktop and an existing
host PostgreSQL instance. Set a reachable host for the actual Docker network.
Provision the database/role using the existing [PostgreSQL guide](postgres-runtime.md).

Build the normal runtime first if it is unavailable or its runtime source changed.
Then add only the public Demo configuration layer:

```sh
# Existing runtime: make build-cloudrun
make build-cloudrun-demo
docker run --rm --name hummingbot-demo --platform linux/amd64 \
  --read-only --tmpfs /tmp:rw,nosuid,size=256m --cpus 1 --memory 2g \
  --env-file "$HOME/.config/hummingbot/cloudrun-demo.env" \
  --publish 127.0.0.1:8080:8080 hummingbot-cloudrun:demo
```

`CLOUDRUN_IMAGE` selects the base and `CLOUDRUN_DEMO_IMAGE` selects the output tag.
Both image settings and the environment example explicitly choose Demo. Runtime
environment values can override image defaults; retain `OKX_TRADING_ENV=demo`.
The inherited UID, command, SQL and Python runtime are unchanged. No secret or
local `.env` is copied into the image.
The base and Demo tags share runtime layers. Run the Demo image for this preset;
building its base does not start a second bot container.

A fresh DB starts stopped. Inspect `/startup`, `/live`, and the authenticated
`/api/v1/admin/status`; `/ready` waits for active trading. Open the Basic Auth
Swagger UI at `/api/v1/admin/docs`. For a fresh version-zero database, this
starts both markets for 30 minutes; otherwise use the actual `control_version`
from status and a new idempotency key:

```sh
curl --fail-with-body --user demo-admin \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: demo-first-start-001' \
  --data '{"action":"start","expected_version":0,"market_mode":"both","selection":"fixed","profile_id":"follow_market","duration_seconds":1800}' \
  http://127.0.0.1:8080/api/v1/admin/commands
```

`curl` prompts for the admin password. Use `selection: random` instead of the
fixed selection/profile fields to draw from the configured `random.profile_ids`.
Switch/stop require the current session ID and control version; follow the
[admin API contract](admin-api.md). A command receipt is not proof of quote
admission: inspect its completion and the market readiness/reason fields.

## Cloud Run configuration

Use the same Demo image with the existing
[service template and deployment workflow](../deploy/cloudrun/README.md).
Replace the template's live credential names with the six dedicated
`HBOT_OKX_DEMO_*` / `HBOT_OKX_PERPETUAL_DEMO_*` names from the environment example,
and inject their values plus `DATABASE_URL` and admin credentials through
versioned secrets. Set `OKX_TRADING_ENV=demo` and `BOT_ID=hummingbot-demo`
explicitly. Cloud Run receives environment values directly; omit
`ENV_FILE`/`HBOT_ENV_FILE` and use its injected `PORT`.

Build/package validation of this preset does not replace release evidence for
its new image digest. The existing deployment review still requires matching
passing full image E2E evidence. No Cloud Run deployment, automatic trading start
or fresh connected OKX run is performed by creating/building this preset.

See [environment routing](okx-environments.md), [quote guards](life-mm-quote-guards.md),
[drawdown](life-mm-drawdown.md), [spot outflow](life-mm-spot-outflow.md),
[inventory skew](life-mm-inventory-skew.md) and [spreads](life-mm-spread-policy.md)
for exact calculations, durable bindings and recovery behavior.
