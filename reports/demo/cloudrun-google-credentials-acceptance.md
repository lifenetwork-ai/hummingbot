# Cloud Run Demo — Google identity and credentials lifecycle

2026-11-09, Asia/Ho_Chi_Minh. **The real Google-controlled connection/trading/
disconnect/reconfigure flow passes.** The bot finishes stopped, both saved slots
Connected at version2, pending venue orders0 and perpetual exposure flat.
[Portable evidence](cloudrun-google-credentials-acceptance-evidence.json).

## Scope and actor

Service `hummingbot-demo`, project `lifeai-438107`, region `asia-southeast1`,
OKX Demo with `postgres_kms` and the local KMS provider. Initial preflight/invitation
observed revision47. All32 captured trading observations and the credential-load
cycle use revision `hummingbot-demo-00048-ckn`, image tag
`4ef36ed61d2a71cb1639ab5b46ec20c7599c0c40`, with the same owner UUID/generation36.
That revision was deployed by the operator, not by this test.

Observed limits are **1vCPU/512Mi**, different from the proposed2vCPU/2Gi.
No image, resource, YAML, ingress, auth environment or IAM change was made for
this test. These results are a bounded real-Demo flow check, not a load/release
gate or production performance qualification.

Basic bootstrap was used for read-only preflight and to invite
`trung.nguyen@genefriendway.com` as active admin. The operator completed real Google
sign-in through a private loopback acceptance relay targeting the Cloud API.
The cloud bound the verified subject and committed correlated `account_bound`
and `login` audit events. Every subsequent Set, Start, Stop and Disconnect
mutation uses that Google principal; their audit snapshots carry the requested
email/admin role. The relay/runner uses no Basic fallback. Google tokens remain
only in page/relay memory; no token or exchange secret is included in evidence.

## Executed sequence

| Step | Observed result |
| --- | --- |
| Whitelist/sign-in | One bound active Google admin; `google_admin_ready=true` |
| Set spot then perpetual | Both v1 Connected; owner decrypts/loads saved bundles; bot stays stopped |
| Read-only encrypted-store proof | Both envelopes present; known plaintext key candidates absent |
| Both / follow_market | Timed Start succeeds; spot/perpetual orders and natural fills observed |
| Disconnect perpetual during session | Bot-wide stop/cancel/close; four final proofs true, selected slot removed; spot companion remains |
| Disconnect spot | Same final cleanup proof; both slots Disconnected; account bindings/versions retained |
| Set same Demo keys again | Both v2 Connected, no automatic Start |
| Spot / sideways after reconfigure | Start succeeds; subsequent Stop succeeds |
| Final state | Control/ack11/11, stopped, no error/HALT/pending recovery; both encrypted slots active/Connected |

The trading receipt intentionally has no actor fields in its command payload.
The first helper incorrectly expected `payload.actor_id`, failed after Start,
and its finally block successfully stopped the bot. Its log and evidence are
retained. The corrected helper verifies actor/email through `/audit?operation_id=…`.
This was a helper contract error, not an engine/HTTP failure.

## Financial and exchange proof

All **5 fills** match individual OKX REST trade IDs, exact base quantity/price and
original fee currency/amount, plus all5 per-order cumulative quantities/fees:

- Two natural spot SELL fills:0.00066957 +0.00066944 BTC.
- Two natural perpetual SELL OPEN fills:0.0003 BTC each.
- One market BUY CLOSE fill during Disconnect:0.0006 BTC, flattening that short.

Repeated independent REST/API reads leave the five-row fill fingerprint unchanged;
there is no duplicate fee/position application. Final venue pending SPOT/SWAP
orders are0 and perpetual exposure is0. The spot net holding remains recorded;
Disconnect did not liquidate spot cash. BTC cash agrees exactly:
`0.998960180584 - 0.00133901 = 0.997621170584 BTC`. Original spot fees are USDT;
all original fees were independently checked, not inferred from this cash equation.
Daily reporting is fresh and separates spot/perpetual fees and funding coverage.

The same exchange-side keys work after Disconnect/reconfigure; this removes the
bot's saved bundle, not the OKX API key. No key deletion, DB repair, HALT reset or
configuration/risk override was used.

## Observed warnings and limits

This is **not a zero-error-log run**. There are34 ERROR records for17 locally
blocked `spread_policy_quote_rejected` quotes. Each identity returns definitive
OKX order-not-found51603; none was admitted to the venue. The exact reason for
the repeated fresh spread-policy rejection was not uniquely isolated here.
The protected send path worked; do not treat these records as successful orders
or suppress them merely because the service remained alive.

All17 precede acceptance of the first Disconnect at16:03:00.355 local. Three
occur during16:01:52–54 and fourteen during16:02:54–16:03:00, near the60-second
executor refresh. Each identity has connector+executor ERROR records; this is
not34 distinct submissions. These identities have no durable order row because
the guard can reject before intent creation, consistent with their venue absence.
Source review shows failed open orders clear the tracker and retry the same
configured entry price, while policy-driven early refresh is currently gated by
the optional volatility-defense object (absent in this Demo preset). Stale-price
retry is a concrete follow-up hypothesis; missing per-rejection quote/reference/
fee snapshots prevent claiming it as the unique historical trigger. No policy,
threshold, severity or runtime code was changed during this acceptance.

Three individual engine probes exceed their300ms request bound (300.843–301.220ms),
with maximum supervisor observation gaps1.306–1.307 seconds. Later authenticated
status is healthy, loaded owner identity/generation stays the same, and no
`service_failed`, replacement or incomplete cleanup appears in captured warnings/
errors. INFO logs are excluded by the existing IAM-blocked project logging rule,
so missing INFO recovery messages are not independent proof of an incident.
No exclusive CPU/memory/remote cause or exact MM downtime is inferred.

Spot BUY reports `order_size_rejected`; account cash observation shows BTC
weight93.66% against YAML target50%, with buy multiplier0/sell multiplier2.
Rounding/admission therefore suppresses BUY. One no-working-quote warning is
observed; this is not thirty-minute two-sided quote availability qualification.

Historical account funding bills were backfilled before the first cloud session's
date. They are account/environment observations, not charges caused by this
short test or a session PnL figure. Real GCP KMS/IAM, disabling Basic in deployment,
full dashboard/browser acceptance, longer availability and load gates remain
outside this run. The temporary acceptance page is not the FE application.

## FE integration corrections

[FE integration](../../docs/fe-integration.md) now explicitly distinguishes:
credential/control versions and root receipt versions; correlated audit actor
lookup; per-slot removal versus bot-wide cleanup; no automatic Start after Set;
liquidity's market-keyed map and inventory-skew/no-quote state; historical
account funding versus current session activity. Existing endpoints/schemas and
the four final Disconnect proof fields remain sufficient; no backend API change
was needed for this acceptance.

### Subsequent local TDD remediation

The follow-up reproduces a concrete runtime defect: a valid configured entry
becomes invalid after a reference/fee change, but its failure clears the tracked
open order while leaving the executor RUNNING. Its next open attempt reuses the
immutable rejected price. Four initial native cases fail on the original source
(BUY/SELL, spot/perpetual). This confirms the retry defect, not the unique initial
trigger of all17 historical Cloud rejections.

The fresh spread gate now emits the specific `QuoteRefreshRequired` type. A
running configured-entry executor enters the existing POSITION_HOLD shutdown;
the controller reserves its level until terminal bookkeeping and builds a new
entry with current reference/spread/fee checks. A second Red exposed replacement
of SHUTTING_DOWN levels without defense; those levels now remain reserved.
Working valid orders are not continuously canceled to implement this fix.

Known spread refresh blocks log WARNING, with side, price, amount, reference,
readiness and the already-computed effective spread-policy snapshot. The log
does not issue another fee/venue request. Missing controller/policy evaluation
and unknown safety failures retain ERROR; network/ownership/recovery semantics
are unchanged. A fresh replacement can still be blocked if conditions change
again before send. No guard tolerance, refresh interval or risk budget is relaxed.

Native coverage includes real reference/fee changes in both markets/sides,
replacement validity, pending-level exclusion, pending cancellation, retained
partial fills/fees without duplication and preserving an explicit Stop already
in progress. PostgreSQL verifies before-intent and after-throttle rejection with
no transport, durable resolution only of positively unsent intents and no HALT.
Related controller/executor/logging regression: **549 passed +2 subtests**;
selected real PostgreSQL ownership/readiness/defense regression: **86 passed**.
The full Cloud Run native Makefile regression passes **2,877 +16 subtests**;
the16 new lifecycle cases run separately in the549-case check above and are
now included in that Makefile target. Formatting/lint and diff checks pass.

This is a local source fix. Revision48 and its17 rejected identities remain
historical evidence; no new Cloud image is deployed by this remediation.
Changed-image Cloud/Demo acceptance and full load/soak remain separate gates.
