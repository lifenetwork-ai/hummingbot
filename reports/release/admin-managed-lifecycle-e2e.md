# Native managed-credential lifecycle E2E

This follow-up adds shipping-image tests for crash recovery during Disconnect,
same-account key rotation and expired-key cleanup repair. REST/WS comes from an
independent internal-network OKX simulator; encrypted storage uses local KEK and
isolated PostgreSQL. Basic bootstrap provides a known audited actor. Real Google,
GCP KMS, OKX Demo and Live accounts are not contacted or changed.

**Complete:** the final grouped run passes **4/4 E2E cases**, and the final related
native regression passes **102 tests** (seven dependency warnings, 5.75 seconds).
No runtime defect was confirmed or runtime source changed by this follow-up.

Final artifact: `build/e2e-offline/20261105-213142-34b8a4/result.json`.
Portable [evidence](admin-managed-lifecycle-evidence.json) retains the exact image,
fixture/harness hashes, outcomes, position-field comparison and development attempts.

| Final case | Unique venue/recorder fills | Physical close attempts | Outcome |
| --- | ---: | ---: | --- |
| Close accepted, SIGKILL before ACK/fill | 5 / 5 | 2 | Same root recovered, both sides flat, spot preserved, no duplicate/rejected close |
| Close fill committed, SIGKILL before verification/removal | 5 / 5 | 2 | Same root recovered, no early key erasure or resurrection |
| Same-account A/B rotation and cold restart | 3 / 3 | Not a cleanup case | B REST/WS/funding, matched reconciliation; all position fields except checkpoint timestamp preserved |
| Expired-key Retry and companion/selected repair | 5 / 5 | 2 | Honest blocking until both keys repaired; same root/fence, child receipts/audit complete |

These are separate isolated databases; the fill counts are not real OKX Demo trades.
All service-container logs are checked for both credential generations' plaintext
canaries. Every final harness hash matches the committed test source.

Runtime source remains `3a3011d6f`; image
`sha256:1bb946c39830f79044b520b47dd4d324419cf7f0d63271363673e2ce137b57a3`,
manifest `sha256:c92d3d6fefabb18e2d4b2cc2ee78a2b6122989f0d115533166763a667945f2cd`.

## Failure stimulus

Four Red tests first exposed missing fixture support. Green checks prove:

- Two fixed public dummy credential generations verify their own HMAC signatures.
  Revoked A cannot authenticate or mutate even with a valid original signature.
- Private WS authenticates generation B and refuses revoked A. Ledger evidence
  records only generation labels/authorization results, never authentication material.
- A close-acceptance barrier stops before fill/ACK. A separate barrier stops fresh
  position verification after a close fill, allowing recorder commit before SIGKILL.
  The barriers are opt-in, explicitly released, and retain at most 16 hit records.
- A fifth Red/Green check proves that an invalid second close can be rejected at
  the venue while accepted-order/fill counts stay unchanged. The new oracle records
  safe order terms for **every physical POST**, including rejected sends. Native
  cleanup must attempt exactly two closes with distinct IDs and the exact original
  reducing direction/size, not merely end with two accepted closes and flat exposure.
- Native-close duplicate notifications use the existing fill/WS path and independent
  venue positions. There is no injected SQL cleanup, fake success callback or
  production bypass. The shipping service entrypoint runs without a test wrapper.

The initial related native regression passed 64 tests, including the first four
Red/Green fixture checks. The final 102-case scope also includes the fifth witness
check, crypto, redaction and economic-replay compatibility. Scopes overlap and are
not added into an inflated total. The fixture
foundation is committed as `de9efad34`. Runtime image remains `1bb946c3…`.

## Crash recovery

Each case uses its own fresh database, creates one spot fill plus LONG and SHORT
perpetual fills, stops normally, then requests native perpetual Disconnect. The
simulator pauses at the selected boundary; the test verifies retained ciphertext
and an incomplete receipt before killing the **whole container** with SIGKILL.
An accepted close can fill while the service is dead. The same container restarts
under a new owner and must read back that close, finish the other side once and
remove only the selected credential bundle after flat/ledger proof.

Both boundaries pass individually. Each has exactly five unique exchange/recorder
fills, matching native fees, two physical close submissions (one LONG/one SHORT),
flat perpetual exposure, preserved spot and retained companion credentials. A second
cold restart proves no position/key resurrection. Audit records exactly one root
acceptance and successful removal, with actor/request correlation. These are
simulated native fills, not real OKX fills.

The cases perform no SQL HALT reset and grant no new trading authority. They do
not seed a separate unrelated risk HALT; its preservation has existing PostgreSQL
test coverage. Completion of Disconnect proves its scoped cleanup, not permission
to restart MM automatically.

## Same-account rotation

The rotation case passes individually: both slots move from A to B through real
Set APIs and idempotent replay. A subsequently expired-key replacement is rejected
with `exchange_credentials_rejected`, leaving ciphertext and history unchanged.
After a full cold restart, the bot remains stopped until explicit Start. The native
private WS reconnects with B; new private REST calls, funding polling and matched
observational reconciliation use B, with no old-key authentication/fallback.

There are exactly three deduplicated exchange/recorder fills. All held-position
fields, including row identity, quantities, entry prices, volume, PnL and fees,
survive another Start/Stop; only the legitimate checkpoint observation timestamp
changes. The test retains both complete snapshots. Each Set has exactly one accepted
and succeeded audit event with request/actor correlation. Credential canaries are
absent from native service logs.

## Expired-key cleanup repair

This case passes individually. Both A keys are expired at the independent venue
after creating native spot/LONG/SHORT exposure. Disconnect stays `action_required`
with `exchange_credentials_rejected`; encrypted bundles remain present and no close
is sent. Idempotent Retry reuses the same root/control fence but cannot turn an
expired key into success.

A stale operation-version repair returns 409 without changing saved ciphertext.
Repairing the companion spot slot to B increments only that slot and still leaves
cleanup blocked on the expired perpetual slot. Repairing the selected perpetual
slot then completes the same root, with both versions at 2, exactly two native
closes and five exact unique fills/fees. Spot and the repaired companion are retained;
the selected bundle is erased only on confirmed completion. Retry and both repair
children finish as `cleanup_completed` under their original correlated actor. A cold
restart preserves stopped state, ledger and selected-slot removal.

## Development attempts retained

- `20261105-211542-7a88b9` stopped at the new rotation test's unsupported
  reconciliation expectation: before Start, there is no active trading connector,
  and both markets correctly report `authenticated_connector_unavailable`. The
  test now explicitly starts and settles both markets before requiring a matched
  observational check. No runtime behavior or acceptance status was changed to
  hide that failure.
- `20261105-211757-b71359` reached and passed rotated REST/WS/funding/matched
  reconciliation, then the new test compared the complete held-position row hash
  after another Start/Stop. The existing checkpoint writes a fresh `timestamp` on
  Stop. The corrected comparison preserves ID and every financial field, permits
  only that observation timestamp to change, and saves both complete snapshots.

## Scope and reproduction

```sh
python scripts/test_e2e_offline.py --image hummingbot-cloudrun:admin-admission-final \
  --scenario managed-lifecycle --trading-env demo
```

The group includes `managed-close-ack-crash`, `managed-close-fill-crash`,
`managed-rotation` and `managed-cleanup-repair`, each on an independent database.
They are also included in `--scenario all`. A targeted group retains
`release_scope_complete=false`: it is not a newly executed full release matrix,
upgrade or sixty-minute load gate. Existing load results retain their original
fixture/source/image scope. No production timeout, risk, pool or cleanup policy is
relaxed by these tests.
