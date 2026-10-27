# Configuration ownership and restart behavior

Operators control sessions through the admin API. Maintainers change strategy
settings in deployed YAML and service settings in the process environment.
There is no API that edits or overrides either file. The dashboard belongs to a
separate FE repository; use the [integration guide](admin-dashboard-integration.md)
and checked [OpenAPI contract](api/openapi.json).

## What can change, and when

| Setting | Source of truth | API editable? | When a change applies |
|---|---|---|---|
| Start, switch, stop | Durable PostgreSQL command/session | Yes | Accepted intent is applied by the owner; inspect receipt and fresh status |
| Spot, perpetual or both | Session control, constrained by YAML market templates | Yes | New start/switch; never a patch to a running controller |
| One profile or Random | Session control using deployed profile IDs | Yes | New start/switch |
| Session duration | Session control within YAML minimum/maximum; default from YAML | Yes | New start/switch; duration begins at joint readiness |
| Follow pairs and weights | `life_mm_profiles.yaml` profiles | No | New session after the updated YAML is deployed |
| Random pool and step duration | YAML `random.profile_ids`, `step_duration_seconds` | No | New session after deployment |
| Trading pair, budgets, spreads, leverage/margin, price/inventory limits | YAML market templates | No | New session after deployment and normal account/readiness validation |
| Guards, bootstrap, drawdown, outflow, inventory/spread policies | YAML plus existing durable policy bindings | No | New session only where compatible with bound policy; conflicts require technical review |
| Credentials, database, auth/CORS, logging, runtime intervals | Process environment / local `.env` / deployment secrets | No | New process with reviewed environment |
| Swagger availability | `ENV`, default `dev` | No | New process; only `dev`/`development`, case-insensitive, expose UI/schema/assets |
| Exchange live/demo routing | `OKX_TRADING_ENV` and matching credentials | No | Deployment-level account/environment change; not a session switch |

Environment precedence is existing process values, then local dotenv, then shared
defaults. YAML profile settings are not an additional layer of API overrides.
`ENV=dev` does **not** select OKX Demo; the exchange environment is independent.
The production service template explicitly sets `ENV=production`. Basic Auth
remains required for enabled Swagger and all operational endpoints.

For the packaged Cloud Run image, YAML is copied into `conf/` at build time.
Changing a file in the repository requires a new image/deployment before a new
process sees it. This API does not reload profile files in a running process.
If a deployment mounts configuration instead, restart the process to load it.

This API revision includes idempotent migration 017 for session-history indexing.
With `ENABLE_AUTO_MIGRATION=false`, apply `make migrate` before starting the new
image; startup still validates schema compatibility. With it enabled, the normal
startup migration path applies the script before trading admission.

## Requested, deployed and pinned configuration

- `GET /profiles` describes the responding process's deployed catalogue and its
  `catalogue_hash`. It offers settings for a new session.
- `GET /status` describes requested and observed state separately. Its
  `session_configuration_url` points to that session's pinned effective settings.
- `GET /sessions/{id}/configuration` returns the persisted session snapshot or
  its creation event. The response labels current versus creation-time profile
  context. Missing historical configuration is 404, never a substitute from today.

Send `expected_catalogue_hash` from the reviewed profile response with start or
switch. If another revision has different YAML, `catalogue_changed` asks the FE to
refresh and let the operator review again. The precondition is optional for old
clients, so the FE must include it to receive this protection. A matching retry
returns its original receipt even after YAML changes; use the same body and key.

The catalogue hash describes validated effective YAML; the session `config_hash`
also describes the effective request. Neither is a secret or a field for an
operator to type. Put hashes, revision IDs and technical diagnostics in Details.
Configured process/owner environment identity is not exchange-account verification.

## Restart and rollout examples

**Restart while active.** A session follows the saved BTC/ETH basket with a
one-hour deadline. Restart restores that basket, anchors, current random step and
absolute deadline. It does not grant another hour or draw another random step just
because the process restarted. An elapsed deadline follows the existing expiry
policy. Runtime recovery/readiness checks still apply before orders are allowed.

**Deploy different YAML.** The repository now follows AVAX and has a different
budget. An existing persisted session continues to use its old snapshot. The UI
shows Active session settings separately from Available settings. A new reviewed
start/switch uses the new catalogue. Switch replaces the session and starts its
new timer at readiness; it does not flatten held positions.

**Change a bound risk policy.** Some risk, bootstrap and outflow policies retain
durable scope/deadlines/reservations. Deploying different limits may be rejected
with a policy-bound conflict. Stop/start, a restart, a new key or a new API profile
must not be presented as a way to reset HALT or bypass those bindings. Follow the
[technical recovery runbook](postgres-runtime.md); the boss-facing action is
“Contact support,” not a database-edit instruction.

**Read a legacy receipt.** Older stored requests may contain reference or random
overrides. Read models retain them for history and restoration, with
`legacy_overrides` on the session configuration response. Copying those fields
into a new command is rejected with 422, even if null or equal to current YAML.

## Minimal operator form

Offer Market, Profile (or Random), and Duration. Show read-only reference pairs,
weights, budgets and important limits in the review screen. Use clear English
labels and summaries from `/status.operator` and `/status.actions`. All action
availability is advisory; submit with the fresh version/session preconditions.

Do not expose credentials, a YAML editor, arbitrary pairs, reference weights,
random timing or risk-limit sliders as editable API fields. Start/Switch/Stop
receipts represent requested work; current quoting comes from fresh per-market
liquidity observations. Stop means “Stop placing orders; positions remain open.”
