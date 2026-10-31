# OKX operations

The [environment/credential contract](#environment-and-credentials) applies to
the whole process. The [explicit Demo preset](#demo-preset) packages BTC-USDT
spot/perpetual and enabled example policies for testing before LIFE is listed.
Keep Demo and live accounts, secrets and database history separate.

For policy calculations use [MM controls](life-mm-controls.md); for migrations,
ownership and HALT recovery use [PostgreSQL operations](postgres-runtime.md).
Deployment is documented in [Cloud Run operations](cloudrun.md), and FE session
controls in [FE integration](fe-integration.md).

### Initial exposure and managed MM restart

For an admin-managed MM session, YAML `initial_positions` is a bootstrap seed
for a controller with no durable position rows. Once PostgreSQL holds exist,
restart restores their quantity, cost basis and accumulated fees. A zero or
partial durable snapshot still suppresses the old YAML seed; unknown exposure
continues to require reconciliation. A database read failure cannot authorize
an empty bootstrap. Unmanaged upstream strategies retain explicit YAML position
override behavior. Changing the YAML seed does not repair existing MM exposure;
use the reviewed recovery procedure in [PostgreSQL operations](postgres-runtime.md).

## Environment and credentials

`OKX_TRADING_ENV=live|demo` selects one environment for the entire process,
independent of API `market_mode: spot|perpetual|both`. The compatible default is
`live`. Changing environment requires a process restart, dedicated credentials
and a separate database/BOT_ID deployment. The admin API cannot hot-switch it.

`OKX_SITE=www|app|my` selects Global/US/EEA account registration. The existing
`HBOT_OKX_REGISTRATION_SUB_DOMAIN` is also recognized; conflicting explicit
connector/site selections fail validation. Unknown modes/sites do not fall back.
The existing AWS perpetual variant is retained only for global live routing.

| Site | REST host | Live WS host | Demo WS host |
|---|---|---|---|
| `www` | `www.okx.com` | `ws.okx.com` | `wspap.okx.com` |
| `app` | `us.okx.com` | `wsus.okx.com` | `wsuspap.okx.com` |
| `my` | `eea.okx.com` | `wseea.okx.com` | `wseeapap.okx.com` |

REST reuses the connector's existing supported account host. All Demo REST requests
through the connector factory carry `x-simulated-trading: 1`, including public
instrument reads. Live requests remove that header. Both public/private WS and
business candle feeds use the selected host and default TLS port 443; reconnects
retain the environment. Public reference discovery uses the same process settings.

PostgreSQL/service credential loading requires dedicated variables in Demo mode:

```dotenv
OKX_TRADING_ENV=demo
OKX_SITE=www
HBOT_OKX_DEMO_API_KEY=
HBOT_OKX_DEMO_SECRET_KEY=
HBOT_OKX_DEMO_PASSPHRASE=
HBOT_OKX_PERPETUAL_DEMO_API_KEY=
HBOT_OKX_PERPETUAL_DEMO_SECRET_KEY=
HBOT_OKX_PERPETUAL_DEMO_PASSPHRASE=
```

Populate these with **Demo** API credentials through injected secrets or the
existing dotenv mechanism. Demo mode never falls back to `HBOT_OKX_API_KEY` or
`HBOT_OKX_PERPETUAL_API_KEY` and their live secret/passphrase fields. The runtime
cannot inspect a key's actual exchange permissions or origin; final P6 verifies
the dedicated account. Original file credential storage remains available in file
mode; selecting Demo there likewise requires supplying actual Demo keys.

Local transport tests use only public dummy credentials and a loopback HTTP/WS
server. They instantiate real connectors/auth/data sources and inspect final REST
headers, WS login and repeated connect routes. They do not contact OKX. The isolated
image simulator exercises LIFE listing behavior even when Demo lacks LIFE pairs.

References, checked on 2026-10-25:
[Global Demo services](https://www.okx.com/docs-v5/en/#overview-demo-trading-services),
[US API](https://app.okx.com/docs-v5/en/),
[EEA API](https://my.okx.com/docs-v5/en/), and
[official port 8443 retirement](https://www.okx.com/en-eu/help/okx-websocket-port-8443-discontinuation-announcement).
OKX documents port 443 as already available and retires public-internet 8443 on
2026-10-31. Regional Demo WS hosts are also listed in that announcement.

## Demo preset

Use this preset to exercise the existing PostgreSQL service and admin API with
BTC-USDT spot/perpetual before LIFE is listed. The default LIFE catalogue and
shared environment defaults remain separate. The preset contains example paper
limits; select actual LIFE policies separately when its instruments are available.

The packaged Demo and normal catalogues explicitly set `allow_until_stopped: true`.
This is permission only: omitted API duration mode still means a timed session.
To rotate continuously, select `selection: random`, `duration_mode: until_stopped`
and omit duration_seconds. The YAML pool/step timing and all risk/bootstrap limits
still apply. Older locally copied YAML without this key defaults to disallowing
new continuous sessions; update the deployed/copied catalogue intentionally.

| File | Purpose |
| --- | --- |
| [Demo catalogue](../deploy/cloudrun/demo/life_mm_profiles.yaml) | Three profiles, both markets, explicit enabled policies and limits |
| [Demo environment example](../deploy/cloudrun/demo/env.example) | PostgreSQL, migrations, admin API and dedicated Demo credential names |
| [Demo image layer](../deploy/cloudrun/Dockerfile.demo) | Inherits the existing runtime and packages this catalogue at its normal path |

### Enable switches

Environment precedence and API/YAML ownership follow [the integration contract](fe-integration.md#what-can-change-and-when).

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

### Availability while policies are enabled

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

The E6 [availability/load evidence](../reports/implementation/life-mm-e6-verification.md)
describes the tested runtime artifact and fault scopes. This configuration
example does not change those runtime rules or certify a new image digest.

#### What to do when quoting is blocked

Read `GET /api/v1/admin/status` first: process/session phase, market readiness,
`quote_guard`, `spread_policy`, `inventory_skew`, `spot_outflow`, service incidents
and their freshness/reason fields. A command receipt alone does not imply admission.

| Reported condition | Response |
| --- | --- |
| Stale feed, `market_price_unavailable`, guard recovery | Check connectivity, book freshness and outside depth; the normal loop retries/requalifies automatically |
| Reference deviation | Inspect the selected reference pairs, model anchor and configured boundaries; correct an erroneous configuration through the normal reviewed configuration workflow |
| Candle fallback | Verify explicit static spreads are effective; optional candles can recover on the same controller |
| `volatility_paused` | Keep the service running and inspect fresh NATR, required prices and working-order cancellation. Recovery needs distinct completed candles plus the configured delay; restarting/profile switching does not clear the pause |
| `volatility_checkpoint_pending` | Wait for owner/account identity or durable transition confirmation. Check DB/ownership health if persistent; do not bypass the final-send gate |
| `volatility_pause_policy_bound` | Restore the saved market defense policy in YAML and let it recover, or arrange operator-reviewed maintenance. The API cannot reset this latch |
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

### Effective behavior

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
[bootstrap guide](life-mm-controls.md#listing-bootstrap) and offline simulator for
empty/own-only LIFE books. Perpetual has no spot-cash inventory skew.

### Build and run locally

For local API/FE integration, use one service container (two CPUs / 2 GiB) plus
the existing persistent PostgreSQL container. Keep `ENV=dev` for Swagger,
`OKX_TRADING_ENV=demo`, `DB_POOL_SIZE=5`, admin API enabled and JSON/INFO logging.
Bind HTTP only to `127.0.0.1:8080`; the browser URL and authenticated Swagger path
are listed below. `ADMIN_CORS_ORIGINS=*` is the current local default. The FE still
uses Basic Auth; no application token/login session is issued.

The [current-image Demo evidence](../reports/demo/okx-demo-current-image.md) verifies
this two-CPU runtime with preserved private policies/history, all three profiles
in spot/perpetual/both, real Random rollover and active restart. It records exact
terminal orders and unchanged retained positions, with no natural new fills;
positive new-fill/original-fee processing is explicitly outside that observation.

Start the service stopped, verify API/status first and use an explicit short
timed session only when testing trading. Merely enabling until-stopped permission
in YAML does not require a continuous session. Existing DB desired state must be
checked before starting a replacement service: saved running intent can resume.
Use the current tested runtime rather than an old stopped container's image.

Reuse the matching private environment, catalogue and DB from an existing Demo
account when they already hold positions/policy bindings. Do not overwrite those
files with the public example or reduce/reset their bound limits to simplify FE
testing. Preserve the original `BOT_ID`, controller IDs and policy values. The
public example is for a separately reviewed new setup, not an upgrade command.

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

The Demo preset uses `ADMIN_STATISTICS_CACHE_TTL_SECONDS=15` for daily history
charts. The shared application default remains five seconds. A longer interval
reduces repeated history aggregation and retains the original observation time;
current balances, positions, trading guards and admin controls use separate
paths. Set this through the environment file when preparing a new setup. The
local [measurement report](../reports/investigations/mm-process-isolation-experiment.md) records
the tested resource shape and remaining acceptance limits.

Build the normal runtime first if it is unavailable or its runtime source changed.
Then add only the public Demo configuration layer:

```sh
# Existing runtime: make build-cloudrun
make build-cloudrun-demo
docker run --rm --name hummingbot-demo --platform linux/amd64 \
  --read-only --tmpfs /tmp:rw,nosuid,size=256m --cpus 2 --memory 2g \
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
[admin API contract](fe-integration.md). A command receipt is not proof of quote
admission: inspect its completion and the market readiness/reason fields.

### Cloud Run configuration

Use the same Demo image with the existing
[service template and deployment workflow](cloudrun.md#service-and-configuration).
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
