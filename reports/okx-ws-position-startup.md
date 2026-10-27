# OKX private position startup ordering

2026-10-28. Resolves the contract-size startup race observed in the
[memory review](mm-memory-retention-review.md). Historical results keep their
original four ERROR records and source/image identities.

## Cause and change

The OKX perpetual connector translates `*-SWAP` symbols with a shortcut that
bypasses the base connector's lazy instrument lookup. A private position frame
could therefore reach quantity conversion before rule polling populated ctVal.
Independently, lazy symbol lookup published the symbol map without contract sizes.

Non-zero positions missing contract metadata now await the existing lazy lookup
and initialization lock. That lookup publishes validated contract sizes with its
symbol map in one event-loop turn. Warm position updates reuse metadata. Zero-size
close events remain applicable without a contract size. No inferred notional/price
quantity, background worker, deferred-event cache or new environment setting is
introduced. Full trading-rule/account readiness is still required for order admission.

The position event version advances before this metadata await, so an older REST
snapshot cannot erase the pending/new WS position. Stop cancels the lookup through
the existing listener lifecycle; cancellation does not publish partial metadata,
and a subsequent start can fetch and apply a fresh snapshot. Malformed sizes are
not published; valid neighboring instruments remain usable. Existing quantity
validation and real error logging remain in place.

## Red/Green evidence

The first Red run was interrupted because the stop test waited for a metadata
request that the old shortcut never issued. The bounded rerun finished with eight
failures. Tests now clean up outstanding tasks on all paths. The final connector
selection passes **154 tests + 9 subtests**, and the focused startup selection
passes **12 tests**. They cover cold startup, warm reconnect without another GET,
Stop during a cold fetch plus retry, REST/WS ordering, invalid sizes, and processing
the following account frame without a five-second error backoff.

The existing missing-metadata quantity fixture now explicitly has a warm symbol
map, representing inconsistent metadata; its quantity guard still raises. An
initial implementation rejected an entire response containing one malformed
instrument. The existing ignore-bad-rule regression caught that; validation now
skips just the invalid instrument and preserves valid neighbors.

Full native regression passes **1,703 tests + 11 subtests**. Final PostgreSQL and built
image verification are recorded below. No real OKX account or
Cloud Run deployment is used by these local simulator checks.

## Final image verification — passed

Full PostgreSQL regression: **219 passed**. The current amd64 image
`2468c5f49687` byte-matches six changed runtime Python sources, including the
position handler. The 300-second internal-network simulator run exercised both
markets, 30-second random steps, explicit Stop/Start, fixed/random Switch and a
final Stop. It seeded 100,000 orders and 100,000 fills and ran with 1 CPU / 2 GiB
for the bot on an arm64 host with amd64 emulation.

- **0 contract-size startup errors; 0 structured ERROR logs**, including retained
  runtime logs after cleanup. Real error handlers/severity were not suppressed.
- All **6,000 original + 1,500 auxiliary reads** completed, with zero unexpected
  HTTP errors. Original p95/p99: **72.8 / 301.4 ms**; auxiliary: **62.8 / 141.7 ms**.
- **298/298 fills** agreed on the independent recorder/exchange comparison;
  positions matched. A pre-completion checkpoint recorded nine step starts and
  all Start/Stop/Switch receipts succeeded, with no HALT.
- Peak RSS: **322.8 MiB**; first/last sample median growth: **3.5 MiB**. After Stop
  and the diagnostic collection, old strategies/providers were **0/0**, with two
  remaining service/probe tasks. No forced GC was used during the load window.

The diagnostic runner now supports `--check-position-startup` to repeat the
specific full-log regression gate. This is a scoped lifecycle/load check, not a
new unchanged hour-long release gate or real-exchange reconnect verification:

```sh
python scripts/test_mm_memory.py --image hummingbot-cloudrun:local --seconds 300 --check-position-startup
```

See [portable evidence](okx-ws-position-startup-evidence.json) and the
[completed plan](../docs/plans/OKX_WS_POSITION_STARTUP_TDD_PLAN.md). Raw artifacts
remain in `build/memory-review/20261028-031943/` and `build/ws-startup-review/`.
The historical ten-minute run retains its four errors; the differing windows
are not a controlled RSS/latency comparison. No database migration, API/YAML
change, extra production task or real order was required by this fix.
