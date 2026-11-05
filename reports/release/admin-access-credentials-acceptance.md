# Admin access and encrypted credential acceptance

Status: A1–A5 implemented and committed; A6 local verification is in progress.
Actual Google sign-in/GCP KMS resources are unavailable, as confirmed by the operator.
The existing Demo service/database have not been cut over. No production deployment
or real exchange order is part of this acceptance.

Final load on candidate `8cd8052d…` passed warmup/mixed/burst, the unchanged
sixty-minute soak and all five faults. Soak had 72,000 primary plus 18,000 auxiliary
HTTP 200 responses, zero unexpected HTTP errors, p99 450.67 ms, 4,884 reconciled
simulated fills/fees/positions and +28.94 MiB retained growth against 64 MiB.
The signed security workload completed 163 role cycles and 489 expected denials.
Raw evidence: `build/mm-load/20261105-092334-94dc45/result.json`.

Audit review found two denied-request audit writes exceeded bounded DB admission
during the healthy burst window. They were explicitly logged, not successful
mutations silently committed without audit. However, the console formatter omitted
the handler's request/actor ID extras. Two Red cases reproduced that observability
defect. The logging-only fix allows those safe IDs in JSON/text and includes actor ID
on audit-persistence failure, without exporting arbitrary extras or credentials.
The focused Green scope passed 83 tests. A missing test filename caused one earlier
invocation to collect nothing; that invocation is not counted as a pass.
The updated image must be qualified separately; the green run above is retained,
not relabelled as evidence for different image bytes.

The logging update is `c33a73cc9`, image `772ddb88c5f2b0984c896c747ef520de8386571d1969b80d5057fe09f0b34f06`
(1,374,393,886 bytes). Its full native regression passed **2,677 tests plus 11
subtests**. The first PG rerun had one failure while constructing 20,000 fixture
events: statement timeout during FK checks, before the discovery query or index
assertions. Concurrent image compilation was present; an exclusive host cause is
not proven. Setup now has a bounded 60-second transaction-local timeout and restores
the exact original timeout before EXPLAIN. Query budgets/index assertions are
unchanged. The isolated affected test passed; a clean full PG rerun is required.

## Candidate and implementation

- Runtime tag: `hummingbot-cloudrun:admin-credentials-fc3577831`.
- Image config ID: `sha256:8cd8052d5ba1851b992862f531f231aaa0637215c0e5236a122cbc01aa287952`.
- Immutable manifest: `sha256:b3424b541904126a360bcec8e5aab33b687f11af55c00359af37d3af7b5c2235`.
- Size: 1,374,393,248 bytes; original 1.5 GiB image budget passes.
- Runtime phases: `c06c02a60`, `91cc55267`, `69e16e142`, `7c5d99588`, `4f284b9f8`;
  native cleanup fix `4eb311ff5`. FE/runbook handoff: `f98c4711a`.
- Later commits extend tests/docs only. Gate artifacts record their fixture hashes;
  a test-only change does not imply a rebuilt runtime image.

Implemented behavior: Google whitelist roles with fresh authorization, env-controlled
Basic bootstrap disablement, encrypted account-bound PostgreSQL credentials with
local/GCP provider adapters, typed Set/load receipts, guided automatic Disconnect,
same-operation retry/cleanup-key repair, safe status/errors and durable audit reads.
The implementation reuses the existing HTTP handlers, bounded workers, supervisor,
engine ownership, native connectors, tracker and recorder.

## Local verification

| Check | Result | Evidence |
| --- | --- | --- |
| Post-fix native release regression | 2,671 tests plus 11 subtests pass | `/tmp/hbot-admin-release-native2.log` |
| Clean post-fix PostgreSQL suite | 439 pass in 837.56 seconds | `/tmp/hbot-admin-release-postgres3.log` |
| Supervised provider delay and permission denial | 2 additional tests pass; API stays live, no env fallback/restart loop | `/tmp/hbot-admin-provider-fault-process.log` |
| Lost post-commit HTTP response and error/audit correlation | 2 focused tests pass; one receipt/version/accepted audit on replay | `/tmp/hbot-admin-response-correlation3.log` |
| Signed identity/provider-fault/history fixture checks | 35 pass | `/tmp/hbot-admin-kms-marker-green.log` |
| Final-image offline E2E, including upgrade/takeover/rollback | 17/17 scenarios pass | `build/e2e-offline/20261105-083127-b83ad5/result.json` |
| Native cancel/close/removal/restart probe | Pass; 62 exact simulated fills/fees, flat perpetual, preserved spot, removed envelopes | `build/mm-load/20261105-082520-d84e71/result.json` |
| Same probe with loaded-key provider outage | Pass; same native cleanup and restart proof | `build/mm-load/20261105-090816-2b0e09/result.json` |
| Original 600-second retention/startup gate | Pass; +18.40 MiB RSS against 32 MiB budget, 12,000/12,000 reads, zero errors, p99 693.95 ms; 3 successful control transitions, no contract-size startup error | `build/memory-review/20261105-091131/result.json` |
| Original full-load profiles and unchanged 60-minute zero-error soak | Running with signed identities, fresh roles, 100,000 audit rows and loaded-key provider outage | `/tmp/hbot-admin-release-load.log` |

Native command populations were 202, 11 subtests, 1,705 and 753. Additional/focused
checks overlap prior scopes and are not added to an inflated total. The provider-delay
process test exceeds normal observer freshness but remains within the existing
45-second stopped-load grace; no new production grace/bypass was needed.

### Diagnostic probe results

Both measured windows are 60-second diagnostics, not the long release soak.

| Probe | Reads/s | Market updates/s | Fills/s | Read p95 | Read p99 | Sampled RSS |
| --- | --- | --- | --- | --- | --- | --- |
| Corrected-image managed lifecycle | 20.00 | 100.002 | 1.00 | 63.18 ms | 343.25 ms | 627,216,384 bytes |
| Loaded-key provider unavailable | 20.00 | 100.006 | 1.00 | 56.73 ms | 297.61 ms | 625,537,024 bytes |

Each probe passed its declared window budgets. Disconnect uses the real native
connector/recorder path against the isolated simulator: stop, cancel/read back bot
orders, close LONG and SHORT separately, reconcile all 62 fill/fee records, verify
flat/detached state, erase selected envelopes, then restart without resurrecting
keys or closed exposure. Spot checkpoints remain unchanged. This is not OKX Demo.

The first corrected probe retained one **pre-window** 311.093 ms engine probe timeout
(epoch 1793842006.25, recovered 1793842007.61; measured window
1793842017.39–1793842077.39). No captured probe failure or loop-stall trigger occurred
inside that window. This is not an exclusive probe-root-cause fix or a guarantee
of continuous two-sided quotes. Bounded capture counters and pre-window signals
remain in raw artifacts; the trace does not claim every child span is lossless.

## Load methodology and security boundaries

The optional `--security` wrapper signs Google-shaped fixture tokens and injects only
public certificates into the mounted test entrypoint. Signature, audience, issuer,
expiry and current database role verification remain real. Private signing keys are
not persisted. There is no shipping env flag that bypasses verification.

Accounts and identity bindings are created through real HTTP handlers. Basic is
disabled before measurement, and working exchange environment credentials are removed.
The engine must load real local-encrypted PostgreSQL bundles. Synthetic audit history
contains 100,000 records. A bounded extra workload checks credential/audit reads,
invalid signatures, denied mutation and demotion/re-promotion on the same token.
Original read/fill schedules, duration, error, latency and retention budgets stay intact.

The provider-outage marker is consumed only by the test wrapper after authorized
bundles have loaded. Unwrap is denied throughout normal windows, then restored before
intentional crash/recovery windows. Already loaded clients must continue MM. Separate
supervised tests cover slow/denied cold loads while administration remains usable.
No production outage toggle or plaintext/environment fallback is introduced.

## Failed attempts and diagnosis

| Attempt | Diagnosis and action | Acceptance consequence |
| --- | --- | --- |
| First image `37c36234…`, `build/mm-load/20261105-081017-a5bc18` | Diagnostic load passed, but Disconnect requested singular `/market/ticker`, absent from the native perpetual throttler. `AttributeError` occurred before transport; no close was sent. A Red test using the real throttler reproduced it. `4eb311ff5` reuses registered `/market/tickers` and selects the exact instrument with Decimal parsing. | Overall attempt failed. Rebuilt candidate repeats all required gates. |
| Static offline seed guard | Wrapping the callback with `partial` violated the existing offline-only safety rule. Restored the original callback; retained the guard unchanged. | Harness failure, not a runtime SQL failure. |
| Second full PostgreSQL attempt | Four SQL fixture cases failed before querying because minimal Seeder adapters lacked optional `env`; run stopped with 4 failed/78 passed. Added minimal-adapter regressions and `getattr` compatibility. | Not a clean pass. All four affected cases, 32 native preparation checks and the clean 439-case rerun passed. |
| New HTTP correlation assertions | Test used absent `message` instead of the existing `error`/`detail` contract, then selected generic denial rather than credential failure from the same request. Corrected selectors; no runtime change. | Both failed attempts retained in `/tmp/hbot-admin-response-correlation{,2}.log`; clean rerun passes. |

The original pre-fix native/PG runs (2,628 tests plus 11 subtests; 439 PG tests) and
38 focused crypto/adapter checks are historical evidence, not substitutes for the
post-fix gates above. The full KMS protobuf SDK was deliberately excluded after its
dependency/connector descriptor conflict; the GCP adapter uses official Auth/REST.

## External acceptance still open

- Real Google sign-in, public certificate discovery and deployment OAuth audience.
- Real GCP KMS/IAM using synthetic credentials and the configured runtime identity.
- Separately reviewed cutover of the existing OKX Demo ledger/account binding to
  managed credentials; no prior approval for unrelated Demo maintenance is reused.
- Production ingress, identities and deployment configuration when deployment is requested.

An unavailable external check is not a pass. Python reference removal is not physical
key zeroization; backups can retain earlier encrypted rows. No secret reveal API exists.
See [FE integration](../../docs/fe-integration.md), [credential operations](../../docs/exchange-credentials.md)
and [phase progress](../implementation/ADMIN_ACCESS_CREDENTIALS_PROGRESS.md).
