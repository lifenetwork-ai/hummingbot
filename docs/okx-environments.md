# OKX live and Demo routing

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

PostgreSQL runtime connections use UTC and bound idle open transactions to five
seconds. This releases a paused process's uncommitted row locks after its owner
session is lost. The separate committed advisory owner session is unaffected;
loss of either required DB authority keeps new submissions closed.

References, checked on 2026-10-25:
[Global Demo services](https://www.okx.com/docs-v5/en/#overview-demo-trading-services),
[US API](https://app.okx.com/docs-v5/en/),
[EEA API](https://my.okx.com/docs-v5/en/), and
[official port 8443 retirement](https://www.okx.com/en-eu/help/okx-websocket-port-8443-discontinuation-announcement).
OKX documents port 443 as already available and retires public-internet 8443 on
2026-10-31. Regional Demo WS hosts are also listed in that announcement.
