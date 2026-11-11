# Demo drawdown policy: 1,000 / 2,000 USDT

Applied on 2026-11-11 with explicit operator approval. Revision
`hummingbot-demo-00093-p5p` runs the updated policy: 1,000 USDT per controller,
2,000 USDT globally. Quote budget remains 100 USDT per market / 200 USDT total;
inventory and position caps are unchanged.

The repository YAML and deployed image were both updated. The image is an exact
overlay on the deployed application, adding only the YAML COPY layer; image
configuration is identical. The database checkpoint was updated separately so
native policy binding accepts the new limits. Merely deploying YAML would leave
the old persisted policy and risk stop in place.

Before maintenance, two complete REST scans matched all 650 fills, quantities,
prices and original fees (392 spot / 258 perpetual); account bindings and
perpetual quantities matched, with no pending venue orders. Stop completed at
control/ack 58. A temporary HTTP-only maintenance revision allowed the previous
engine to release its writer lock. The mutation required the exclusive advisory
lock, no runtime owner and no other database clients.

The transaction changed only the approved checkpoint policy and corresponding
drawdown stop/breach fields, removed the matching HALT, and appended a maintenance
audit. PnL peaks were preserved exactly. Every other table retained its checksum;
no fill, order, position, credential, runtime baseline or other risk cap was
rewritten. Native binding dry-run and 22 existing drawdown tests passed; packaged
YAML parsing passed. No full release rerun is claimed for this configuration edit.

After restoring automatic min1/max1 and the original 2 CPU / 1 GiB resources,
Start succeeded at control 59 using the actual Basic admin principal, recorded
as such in audit. Readback confirmed running, no HALT, fresh drawdown observations
and limits 1,000/2,000. At 07:34:30 ICT, OKX REST independently confirmed two live
spot SELL orders matching the admin API; neither had filled at that observation.
Spot BUY reports `order_size_rejected`; perpetual reports `inventory_limit` on
both sides. These guards were retained, so this is not two-sided quote acceptance.

One engine probe timeout was logged during startup (300.958 ms elapsed; observed
maximum probe gap 1.305 s), followed by successful startup and working spot orders.
Perpetual no-quote warnings correspond to the inventory block. Cold automatic
recovery remains unqualified for the legacy ledger: `cold_recovery_supported=false`.
The new runtime baseline was not fabricated as part of this policy change.

The local YAML change must accompany the next source deployment to avoid
reintroducing the previous limits. No Git push was performed by this operation.

Structured evidence: [policy and readback](cloudrun-drawdown-1000-2000-evidence.json).
