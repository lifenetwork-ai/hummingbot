# E5: existing maker fees and completed-candle NATR

Verified 2026-10-27 (Asia/Ho_Chi_Minh), baseline `918ab7e78`. E5 complete.

Reused existing connector fee objects/conversion, PMM dynamic's NATR, candle factory,
shared connector/throttler, PMM Simple allocation, configured-entry PositionExecutor,
quantization, owner/send checks and authenticated status/Swagger. Added a small
validated spread policy and reference-counted optional feed ownership. No new
service, task framework, external client/cache, SQL table, MACD shift or reference
formula. Disabled behavior remains the original fixed-spread path.

Dynamic sizing uses a bounded completed-candle window and cached NATR; send checks
never construct a DataFrame or fetch candles. Outages select explicit static
fallback or wait, retaining any enabled fee floor. Shared optional feeds cannot
make static fallback globally unavailable. Original unmanaged feeds retain their
readiness/lifecycle. Levels remain ordered, quotes stay within existing capital,
price and exposure limits, and current anchors remain fixed.

Red/Green contracts cover base/quote flat and percentage fees, rebates, unknown
currencies, per-leg coverage, ticks/quantity rounding, flat/stale/insufficient or
non-finite candles, caps, absolute limits, shared-feed release and recovery.
Actual tracked terms are checked before intent and again after REST throttling.
SELL rounds upward; quantity scales down to preserve quote allocation. Rounded
amounts that cannot cover flat fees are skipped through the normal proposal path.

Image integration exposed the required perpetual `PositionAction.OPEN` fee argument;
a signature-enforcing Red/Green contract now protects it. A second availability
review found disconnected old candle history delaying recovery for roughly a
hundred extra intervals. A Red/Green test now uses the newest sufficient contiguous
tail. Final image recovery takes **7.20 seconds**, on the same process/controllers,
within an unchanged 30-second scenario bound. No process restart or HALT occurred.
The initial smoke fixture's literal migration count was updated from 15 to 16 for
E4's real packaged migration; no runtime/latency budget was relaxed.

Native full regression: 1554 tests/11 subtests, plus final spread contracts (21,
1559 unique native cases across runs). Full real-PG: 184; final two send-gate tests
cover 185 unique PG cases across runs. All 21 spread contracts also pass with
RuntimeWarning as errors. Scoped hooks/Swagger pass. A native cached-composition
probe including mock overhead measured ~0.26 ms/call over 1000 calls; it is not
container certification and does not replace E6.

Final Linux amd64 image: `sha256:ee3b3b0b40fe29db362d589c767966ae328e8bb9bdf8236525f50b2cd520c4aa`,
1364799073 bytes. Thirty-five runtime/config/schema paths match packaged bytes.
Image ABI/JIT/TLS/SDK/SQL smoke passes. Real service + PostgreSQL + independent
simulator verifies eight both-market post-only quotes, capped multiplier 3 and
wider actual prices after a candle shock, explicit fallback/wait on outage,
7.20-second recovery, authenticated events and clean stop with no unknown effects.
Raw result: `build/e2e-offline/20261027-015200-fa0bb7/result.json`.

See [guide](../../docs/life-mm-controls.md#fee-and-volatility-spreads) and [JSON evidence](life-mm-e5-spread-policy.json).
Fee coverage is an estimate valued through the existing helper, not a realized
profit guarantee. Full all-feature matrix/load/soak and external Demo remain E6.
No external exchange account or deployment was used for E5.
