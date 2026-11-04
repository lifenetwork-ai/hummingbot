# Final checkpoint-fix image: full local release gates

Latest MM candidate: `354fec1e` passes its own unchanged standalone hour with
p99 126.88/76.41 ms, zero unexpected API errors/stale and 3,594 exact synthetic
fills. Four measured probes recover without restart; one locally spread-rejected
SELL and the incomplete historical attribution remain explicit. Actual Demo
stays stopped on `086454de` with its ledger preserved.
[Current hour/trace report](mm-partial-quote-final-soak.md),
[evidence](mm-partial-quote-final-soak-evidence.json). The historical image-specific
full R1/R2 results below keep their original scope.

Image `51d4d879d0f0`, source `06ab51b17`, passes the unchanged full local R1
and matched ordinary-entrypoint R2. This is a new measurement on the final
shutdown-checkpoint fix, not an inheritance of the earlier image's load result.

| Window | Duration | Read p99 | Unexpected API errors |
| --- | ---: | ---: | ---: |
| R1 warmup | 300 s | 297.19 ms | 0 |
| R1 mixed | 900 s | 104.56 ms | 0 |
| R1 burst | 90 s | 97.33 ms | 0 |
| R1 soak | 3,600 s | 234.90 ms | 0 |
| R2 warmup | 300 s | 190.56 ms | 0 |
| R2 mixed | 900 s | 401.28 ms | 0 |

All six R1 fault groups pass: feed, volatility defense, owner restart, queued
expiry, database delay and database failure. Independent verification matches
4,885 synthetic fills to recorder entries and positions. The soak has 58 samples,
with a maximum gap of 65.64 s; sampled supervisor/API/engine PIDs stay unchanged.
Adjusted median memory retention grows 14,823,424 bytes (14.14 MiB), below the
unchanged 64 MiB budget. Healthy-window probe failures still occur and recover:
R1 has 20 engine/one API, maximum reported interval 2.761 s; R2 has 15 engine,
maximum interval 1.279 s. No healthy-window child restart is recorded. A probe
interval is not measured quote downtime, and no unique cause is established.

The subsequent [same-image MM correlation diagnostic](../investigations/mm-checkpoint-probe-correlation.md)
captures two recovered probes with continuing tick/controller/order progress.
It adds trading measurements for that600s run; it cannot reconstruct missing
MM activity in these historical release episodes.

All 1,156 packaged runtime/config hashes match, and 1,223 runtime/fixture files
remain unchanged through measurement. Prior source regression results (2,371
native tests plus 11 subtests, 321 PostgreSQL tests and Swagger) still apply to
this same source. Three packaged E2E groups were already passing on this exact
immutable image; their result hashes and unchanged fixture/runner source were
checked before reuse. They were not rerun merely to repeat a passing check.
The idle Demo bot container was stopped during measurement; no clone dry run,
build or other agent test ran alongside these load windows. Raw evidence is in
`build/checkpoint-poll-release-20261103/attempt-7/` and the
[portable evidence](mm-checkpoint-final-release-evidence.json).

## Later REST proof closes the seven-fill gap

After the load gates, a read-only OKX Demo query returned all seven new natural
fills. Independent comparisons match every market/order/trade ID, exact base
quantity (including contract conversion), price, side, maker execution and fee
in its original currency. The prior failed observation is preserved: earlier
fills/history queries returned older rows, whereas this later query returns the
new rows. The cause of the earlier response behavior is not established. No new
orders were submitted to obtain this proof, and no ledger repair was needed.

The existing aggregate/order/account checks remain valid: one spot/six perpetual
fills across six orders, 18 total unique rows, preserved original 11 fills, and
filled-exposure restart. Individual REST trade verification is now complete.
This supplements the earlier connected run on `7dca3ce9`; it does not relabel
that run as executing the later checkpoint code.

The local Demo service is restored on `51d4d879`: owner available, trading
stopped, no HALT or pending account mutations; `/startup` and `/live` are 200,
`/ready` is 503 for intentional idle. Order/fill/position/executor hashes match
the pre-load database. Existing private backups and stopped predecessor
containers are retained. Earlier reconstructed executor/session-event history
remains incomplete. No production trading or Cloud Run deployment is claimed.
