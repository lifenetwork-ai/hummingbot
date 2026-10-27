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

Full native regression passes **1,703 tests + 11 subtests**. PostgreSQL and built
image verification are recorded below when completed. No real OKX account or
Cloud Run deployment is used by these local simulator checks.
