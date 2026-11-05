# Managed credentials: local environment-boundary E2E

This follow-up covers cold restarts for `EXCHANGE_CREDENTIALS_SOURCE=postgres_kms`
and `OKX_TRADING_ENV=demo|live`, using **local encryption only**. It uses the real
packaged service entrypoint, supervised API/engine processes, native OKX connectors,
recorder and isolated PostgreSQL. An internal-network exchange simulator supplies
REST/WS; neither routing mode contacts OKX. Basic bootstrap authenticates this
scenario. Real Google sign-in, real GCP KMS/IAM and an existing-Demo cutover are
explicitly deferred.

## Coverage

| Cold revision | Required result |
| --- | --- |
| Initial Demo, stopped | Save both connector bundles through the admin API; wait for loaded/Connected acknowledgements; retain ciphertext |
| Live, stopped | Missing Live bundles block trading despite valid dummy environment keys; no private exchange calls before candidate validation; a Live Set returns `409 exchange_account_mismatch`, with only read-only account validation |
| Demo restored | Original bundles load without another Set; explicitly start both markets, deliver spot/perpetual native fills plus duplicate notifications and commit exactly two fills |
| Live, persisted running intent | Remain blocked; no private exchange calls, accepted orders, financial changes or ciphertext changes; never adopt the Demo session into Live |
| Demo, persisted running intent | Original scope resumes quotes; all new private calls carry the Demo header; explicitly Stop |
| Demo, wrong valid-length KEK | Cold decryption blocks trading; admin liveness and owner generation remain stable in a bounded check; no fallback, private exchange calls or financial/ciphertext changes |
| Demo, original KEK restored | Both slots Connected, previously stopped intent stays stopped; reconcile exact fills, fees and positions |

The financial digest covers complete order, fill and position rows. Fill ordering
uses the actual composite primary key. Digests are checked around blocked cold
starts; legitimate resumed quoting is allowed to create new orders. Duplicate
notifications are reconciled by the existing native recorder and final ledger check.

## Candidate and reproducibility

Final run **passes**: `build/e2e-offline/20261105-175542-5adcab/result.json`.
All seven revisions pass; two unique exchange/recorder fills, fees, positions and
three daily snapshots reconcile. Related native regression passes **69 tests**
with three existing dependency warnings in 67.67 seconds. Final test files pass
flake8 and `git diff --check`. No new runtime defect was confirmed in this follow-up.

Runtime source remains `3a3011d6f`; image
`sha256:1bb946c39830f79044b520b47dd4d324419cf7f0d63271363673e2ce137b57a3`,
manifest `sha256:c92d3d6fefabb18e2d4b2cc2ee78a2b6122989f0d115533166763a667945f2cd`.
Only host-side tests and documentation change. Portable
[evidence](admin-managed-environment-evidence.json) records the exact image, fixture
and harness hashes and the final run's revision outcomes.

```sh
python scripts/test_e2e_offline.py --image hummingbot-cloudrun:admin-admission-final \
  --scenario managed-environment --trading-env demo
```

The scenario is also included in `--scenario all`, with an independent database
and simulator per scenario. The targeted result's `release_scope_complete=false`
is intentional: this is additional coverage, not a newly executed full matrix,
upgrade or sixty-minute load gate. Existing load evidence stays attached to its
original unchanged runtime image; it is not presented as a new run here.

## Development failures retained

- `20261105-174341-f25093`: the new fixture set funding polling to 86,400 seconds,
  outside the existing 1–3,600 range. The constructor refused startup before schema
  initialization. Corrected the test configuration to 3,600; no runtime limit changed.
- `20261105-174834-4383b2`: the new digest helper incorrectly ordered `trade_fills`
  by a nonexistent `id` after successfully testing the stopped routing boundary.
  Corrected the helper to its existing composite primary key; no schema change.
- A confirmation attempt was deliberately interrupted to resolve a test-file lint
  error before capturing final source hashes. It is not counted as a pass.

These are test-authoring failures, not newly confirmed trading defects. Runtime
credentials, decryption, account binding, timeout and risk policies were not relaxed.
The existing actual Demo service/database and deployment `.env` were not changed.

## Limits

Two simulated native fills exercise recorder/recovery integrity, not real exchange
matching or liquidity. The bounded wrong-key liveness check does not establish
continuous uptime. Google-shaped fixture tests elsewhere remain offline tests;
neither real Google authentication nor real GCP provider acceptance is claimed.
An actual Live account must not share a Demo ledger merely by changing an env var.
Use separate bot/database scope or the reviewed cutover procedure.
