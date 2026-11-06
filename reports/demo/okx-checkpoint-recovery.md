# Shutdown checkpoint fix and reviewed Demo recovery

Current follow-up: [final image full release and seven-fill REST proof](../README.md#historical-records) (Git: `reports/release/mm-checkpoint-final-release.md`) now pass. The observations below preserve the earlier partial-verification stage.

The shutdown retry defect is fixed in `06ab51b17`: V2 cleanup sets its stop flag
before awaiting terminal shutdown; if cleanup fails, a subsequent shutdown used
to persist that lifecycle flag as a pre-existing risk stop. A real-PostgreSQL
Red reproduced the overwrite. V2 now retains the flag observed immediately
before cleanup, and both ownership and MM drawdown checkpoints use that value.
A genuine pre-existing risk stop or explicit drawdown breach remains true;
individual stopped controllers and PnL peaks remain recorded. Existing stored
HALT/checkpoints are never automatically reset by this source change.

Validation passes: 2,371 native tests plus 11 subtests, 321 PostgreSQL tests,
Swagger and three packaged E2E groups. Image `51d4d879`, source `06ab51b17`,
matches 1,156 packaged hashes and has been installed locally while trading was
stopped. Its subsequent stopped restart passed; order/fill/position/executor
fingerprints and private configuration hashes stayed unchanged. Current service
is running with trading stopped, no HALT, and private backups retained.

The operator separately approved the exact expiry-intent repair and the later
checkpoint reset. Both were preceded by fresh exchange checks, stopped/no-owner
DB guards, private backups and successful clone dry runs. No order or fill
history was deleted. These approvals do not authorize future automatic resets.

Connected testing on the preceding image `7dca3ce9` then passed nine profile/mode
cases, actual 600-second Random rollover, active spot/both restart and Stop.
Independent session attribution confirms both sides on all expected markets;
636 orders were independently confirmed canceled. No new fills occurred during
that matrix. Six ERROR lines represented three local `SubmissionBlocked` orders:
tracker events and independent 51603 reads were checked. The first log-review
helper failed because it only recognized the older message format; its failed
result was preserved, and current structured messages were independently
classified rather than ignored.

A separately approved temporary 40-USDT-per-market, one-level post-only preset
then received seven natural fills (one spot, six perpetual across six orders)
and passed a filled active restart. The original bounded observation failed its
per-trade REST comparison: `/fills`, filtered and unfiltered, and `/fills-history`
returned older records or no new records. Individual new trade-ID REST proof
therefore remains **incomplete**; the original failure is retained.

Supplemental independent REST order-details checks confirm every filled order's
accumulated quantity, average price, original fee currency and summed fee. Exact
account comparisons also pass: perpetual long 0.0096 BTC / short 0.0030 BTC,
single managed spot net -0.00026412 BTC, fee-adjusted BTC cash, unchanged baseline
11 fills and 18 unique total rows across repeated reads. This aggregate proof
does not replace independent REST proof of each partial trade ID. The original
catalogue was restored, and no exchange orders remain pending.

The [evidence](okx-checkpoint-recovery-evidence.json) preserves image-specific
scope and raw evidence hashes. Full offline load belongs to `7dca3ce9`; the
later `51d4d879` cleanup-only fix has source/packaged/stopped-restart coverage,
not a newly repeated hour-long load. Earlier reconstructed executor/session-event
history is still incomplete. No production or Cloud Run deployment is claimed.
