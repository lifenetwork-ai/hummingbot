# Reviewed local Demo managed-credential cutover

Prepared and operator-approved 2026-11-09. Applied exactly as reviewed at
`2026-11-08T22:33:00Z`. Runtime candidate: `b090430c…`.

The original Demo container/volume no longer exists. The latest available backup,
`partial-quote-final-soak-20261104T163450Z/before-offline-soak.dump`, was restored
into a new isolated `hbot-managed-demo-db` container/volume. Existing Google auth
and unrelated containers/volumes were not changed. Existing SQL migrations passed
on the restored clone. No real exchange mutation has been sent.

## Independent read-only checks

- Dedicated Demo credentials authenticate REST and private WS for both connectors.
- Both report the same account as the backup's daily snapshots, site `www`, account
  `900428527479354593`, Futures mode (`acctLv=2`) and Hedge position mode.
- No pending SPOT or SWAP orders.
- Perpetual positions agree: long `0.0100 BTC`, short `0.0032 BTC` (OKX contract
  sizes converted with the BTC contract size). Spot cash `0.99922871628 BTC`
  agrees with the backup's most recent BTC daily cash snapshot. It is not inferred
  from an assumed one-BTC starting balance; the old ledger has reconstructed history.
- Restored bot `okx-demo-btc` is stopped, control/acknowledgement `126/126`,
  `runtime_active=false`, no pending mutation/HALT, saved risk-stop flag false.
- Baseline rows: 3,513 orders, 23 fills, three positions. No managed credentials
  or admin accounts existed in this pre-managed backup.

## Reviewed maintenance and application

The shipping API correctly rejects the first managed key on an existing unbound
ledger with `account_binding_required`. Do not delete history to bypass this.

While the service is stopped, repeat account/order/position checks and retain a
fresh clone backup. In one guarded transaction:

1. Insert exactly two `exchange_credentials` metadata rows scoped to
   `okx-demo-btc / okx / demo`, connectors `okx` and `okx_perpetual`, account
   `www:900428527479354593`, version `1`, state `revoked`, SQL NULL envelope and
   operation ID. `changed_by=maintenance:operator-reviewed-demo-cutover`.
2. Append one maintenance audit event documenting the approved account binding.
3. Recheck immutable order/fill/position fingerprints and stopped/no-HALT state.

No existing order, fill, position, checkpoint, risk limit, HALT or control version
is edited. No API key is stored by maintenance. A rolled-back dry run on the new
clone confirms both scoped NULL-envelope rows satisfy schema constraints and
leaves zero credential rows afterward. After explicit approval, a fresh independent
Demo preflight and immutable fingerprint check passed. The two binding rows and
one maintenance audit were then committed. A fresh pre-cutover dump is retained
outside `build/` in the private `managed-demo-local-20261109` configuration folder.

The existing admin Set APIs subsequently saved both dedicated Demo keys using
local encryption. Both slot versions are `2` and report connected; a clean
exit0/non-OOM cold restart loaded both slots from PostgreSQL while trading stayed
stopped. No exchange API credential variables exist in the service environment;
the KEK is retained outside `build/` across restarts. No candidate plaintext was
present in the stored envelopes. Controlled BTC-USDT sessions follow the requested acceptance work;
they use the existing ledger/exposure and explicitly bounded Demo configuration.
Disconnect, if tested, uses the shipping cancel-and-close flow and therefore
closes bot-managed perpetual positions; it never deletes the API key on OKX.

Stop and report any changed account/exposure, unexpected order, immutable
fingerprint mismatch or new recovery blocker. Approval of this proposal does not
authorize another ledger repair or resetting a risk stop.

The later controlled matrix passed all nine fixed profile/mode cases, with three
natural spot fills independently matched by REST. The subsequent Random step
cleanup failed after active restart and retained a new HALT. The initial cutover
approval does not clear it; see the [separate recovery proposal](managed-local-random-recovery-proposal.md).

The explicit review rule is documented in
[PostgreSQL operations](../../docs/postgres-runtime.md#ownership-and-unresolved-exchange-effects)
and the [managed Set contract](../../docs/exchange-credentials.md#managed-set-and-runtime-loading).
