# Rolling spot USDT outflow

Managed PostgreSQL MM can limit rolling **spot trading cash consumption**. The
default catalogue has `spot_outflow: null`. Enable it with an explicit policy:

```yaml
# Test example only; select a window, cap and fee allowance for the actual bot.
spot_outflow:
  window_seconds: 3600
  max_net_outflow_quote: "100"
  fee_allowance_rate: "0.01"
```

The window is an integer from 1 to 604800 seconds. The cap is a finite positive
USDT amount. The explicit fee allowance is a finite fraction from 0 to 1.
The catalogue must retain a spot USDT market template even when perpetual-only
trading is selected. No environment variable or extra service is required.

## Calculation

Use committed `trade_fills` for OKX spot and the bound pair in the UTC half-open
interval `[database_now - window, database_now)`. Net outflow is:

```text
BUY price × base amount - SELL price × base amount
+ actual USDT fees - actual USDT rebates
```

Original base/third-currency flat fees are reported separately, without an invented
USDT conversion. An original percentage without identified actual amounts is
unavailable for this metric until that observation leaves the window. Transfers,
perpetual fills, margin and funding are excluded. This is not wallet equity or
drawdown; the existing reporting APIs retain those separate observations.

For a proposed BUY, require:

```text
max(0, committed net outflow)
+ remaining reserved BUY limit notional and fee allowance
+ new BUY limit notional and fee allowance
<= cap
```

Pending sells do not fund buys. A known negative net outflow is floored at zero
before adding pending reservations. A BUY uses the greater protection of the
explicit percentage allowance and the validated existing fee estimate: an
estimated USDT rate above the configured allowance is rejected, and known
positive USDT fixed fees are additionally reserved. Unknown estimates block BUY.
The allowance is a conservative estimate, not a guarantee of future venue fees;
new admission closes if actual recorded charges exceed available capacity.

Only existing post-only spot entries are supported under this policy. Perpetual
orders and funded spot sells remain subject to their ordinary account, balance,
exposure, session, recorder and ownership checks. A BUY-only cap/coverage denial
does not revoke the shared MM readiness gate or prevent cancellation. The LIFE
controller filters unavailable BUY proposals; the authoritative gates still run
before durable intent and immediately before REST transport after throttling.

## Reservation and persistence

Reservations use the existing client order ID and quantized limit price/amount.
The same ID is counted once across repeated checks. A committed fill moves its
quantity from pending reservation into recorded cashflow. Tracker execution
quantity alone cannot release capacity before the recorder commit. A debit at
the exact upper window edge remains protected as pending risk until it enters
the window, including during reconstruction without a live reservation.

Local cancellation or failure alone is insufficient to free an unfilled remainder.
Read-only reconciliation reuses the existing connector's authenticated/throttled
order/fill APIs and fill parser. It verifies terminal identity/terms, paginated
fill completeness against cumulative quantity, and stable final order details.
Recovered fills use the existing recorder and primary-key deduplication; no fill
ledger is duplicated. Only proven terminal or durably rejected unsent remainder
is released. Incomplete reads/timeouts retain reservation and block new BUY;
funded sells and cancellation continue. The service observes these reads with
backoff. Shutdown uses its existing bounded position-preserving path.

The policy bounds admitted requests. It cannot retract a request already sent or
undo spending caused by a fill/fee update. A cap does not force market closes,
turn sells into taker orders, or clear recovery HALT.

## Account binding and first activation

The existing `bot_state.recovery_state.spot_outflow` holds one immutable policy,
verified OKX account UID, demo/live environment, pair and coverage-start time.
Verification uses identity already observed by the normal connector. Both adapters
must identify the same account/environment when running together. Changed account,
environment or bound policy requires review; normal commands cannot disable the
policy, switch its pair or bypass it through a legacy runner. Profile changes,
random steps, market selection, clean restart and stop/start retain the window.

During reconstruction, startup retains account-setup authority while ordinary
order permission stays closed. It rechecks durable HALT after initialization;
finishing an observation cannot reopen orders after a binding failure.

An empty spot recorder and reconciled account can start immediately after binding
is committed. Existing unbound spot order history is handled conservatively:
BUY waits one full configured window from the verified binding/reconciliation
point. Old rows are not relabelled as activity of the current credentials. The
status exposes unknown cashflow as `null`, not a fresh zero. Funded sells can
continue under verified identity. The waiting deadline is durable across a clean
restart; unclean/uncertain recovery still retains the normal HALT.

Reconstruction streams at most 250001 rows from the existing market/pair/timestamp
index, with a 250000-fill in-process bound. Overflow is unavailable, never silently
truncated into a usable allowance. Reservations are bounded at 256 identities;
terminal reads handle at most four orders per ordinary observation pass and at
most 100 pages per order. Per-reservation deduplication retains at most 10000 fill
identities; exceeding that bound blocks BUY with explicit coverage failure instead
of growing indefinitely or forgetting prior commits. Known cashflow uses incremental post-commit updates and
heap-based expiry; status/proposals/reservation checks do not scan history or
query the exchange. Existing owner/recorder send-gate SQL remains authoritative.

This delivery assumes one bot/account history per DB. It does not add multi-account
attribution or an automatic policy/HALT reset. Reviewed maintenance must establish
history/exposure provenance before changing that binding.

## API and verification

Authenticated `GET /api/v1/admin/profiles` includes the optional policy.
`GET /api/v1/admin/status` adds `spot_outflow`: policy/binding, used/reserved/
remaining quote amounts, original non-USDT fees, UTC window bounds, coverage,
readiness, pending terminal proofs and the last BUY denial. Money uses decimal
strings; observation time uses UTC `Z`; window fields are UTC epoch milliseconds.
`buy_allowed` describes this spending gate's coverage and positive capacity;
the proposed amount and every ordinary trading gate must still pass. Owner loss
marks the snapshot stale and hides current capacity. A cold stopped process shows
the binding with unknown current amounts until reconstruction.

Common reasons include `spot_outflow_legacy_window_uncovered`,
`spot_outflow_identity_unavailable`, `spot_outflow_binding_mismatch`,
`spot_outflow_fee_unavailable`, `spot_outflow_limit`, `spot_outflow_reconciling`
and `spot_outflow_projection_unavailable`. Identity mismatch retains durable HALT;
ordinary cap exhaustion/read-only observation waits do not terminate the service.

```sh
make test-cloudrun-regression
make test-postgres
python scripts/test_e2e_offline.py --image hummingbot-cloudrun:e2b --scenario spot-outflow --trading-env demo
python scripts/test_mm_load.py --image hummingbot-cloudrun:e2b --profile probe --spot-outflow --drawdown --strict-guards
```

The probe binds an empty test DB before adding yesterday's synthetic history.
This establishes provenance without bypassing legacy warmup. Its explicit test
limits are not production defaults and the short probe does not replace E6.
