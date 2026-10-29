# Local Demo API startup

2026-10-29. [Sanitized evidence](local-demo-startup-evidence.json).

The selected local configuration is the PostgreSQL service with OKX Demo routing,
one CPU / 2 GiB, a five-connection pool, `ENV=dev`, JSON/INFO logs and admin API.
HTTP binds only to `127.0.0.1:8080`. No cloud resource was used.

- [x] Confirm existing Demo DB intent is stopped/acknowledged and has no HALT.
- [x] Preserve its private environment/catalogue, bot/controller identity,
  positions, reporting history and durable policy bindings.
- [x] Rebuild after the operator's Docker cleanup removed the previously accepted
  `be3efe46…` image; use the current source rather than the older stopped image.
- [x] Verify the rebuilt amd64 image `143ddfe2…` (1,367,392,175 bytes), all 29
  selected runtime-source hashes and the existing network-disabled runtime smoke.
- [x] Start `hummingbot-local-demo` on the existing `hummingbot-okx-demo` network,
  connected to `hummingbot-okx-demo-db`; read-only root/profile mount, bounded
  tmpfs and rotated Docker logs; restart policy `unless-stopped`.
- [x] Verify healthy stopped owner, auth rejection and authenticated profiles,
  Swagger and schema. No trading command was submitted.

The original private files under `~/.config/hummingbot/` are unchanged:
`okx-volatility-demo.container.env` and `okx-volatility-demo-profiles.yaml`.
The preserved acceptance catalogue has 200 USDT spot / 400 USDT perpetual quote
allocation (combined cap 600); these are Demo settings effective only if a session
is explicitly started, not new listing budget recommendations. Changing to the
public 100/100 example must not silently overwrite existing bound policies.

Local entry points:

- API base: `http://127.0.0.1:8080/api/v1/admin`
- Swagger: `http://127.0.0.1:8080/api/v1/admin/docs`
- Use `ADMIN_API_USERNAME` / `ADMIN_API_PASSWORD` in the private environment file.
  These are application Basic Auth credentials, separate from the OKX passphrase.
- `docker stop hummingbot-local-demo` stops this service; `docker start
  hummingbot-local-demo` starts it again. Restarts restore DB intent, so confirm
  it remains stopped if later sessions have been used. Container stop is not the
  API command to stop quotes while keeping API access alive.

Observed startup: 19 INFO records, no WARNING/ERROR/CRITICAL. `/startup` and `/live`
return 200; `/ready` returns the expected 503 while trading is stopped. Unauthenticated
admin status returns 401; authenticated profiles/docs/schema return 200. The owner
is available, `desired_state=stopped`, `running=false`, control and acknowledged
versions both remain two. No Start, Switch, Stop or trading POST was sent.

Runtime smoke passes ABI imports, Numba JIT, TA-Lib, TLS trust, 63-connector
discovery, 17 SQL assets, CLI and structured-log/redaction checks. Raw artifacts
remain in ignored `build/local-demo/`. The rebuilt image has a new digest; these
checks do not replace a full release E2E/hour gate for that artifact. The earlier
accepted load evidence retains its original image scope.

FE browser integration, any later cloud deployment and the listing-specific
instrument/budget review remain pending. The old mixed-window p99 root cause
remains unconfirmed as recorded in the [availability report](life-mm-availability-remediation.md).
