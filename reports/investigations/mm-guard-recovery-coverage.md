# Protective withdrawal and final-send regression coverage

Date: 2026-11-03 (Asia/Saigon).
[Evidence](mm-guard-recovery-coverage-evidence.json),
[Checklist](../../docs/plans/MM_RUNTIME_LOGIC_AUDIT_TDD_PLAN.md#further-withdrawal-and-send-gate-coverage--requested-2026-11-03).

The review adds **36 cases**: 26 native withdrawal/session lifecycle cases and
10 real-PostgreSQL send-gate cases. No new production defect is demonstrated;
production code, configuration, migrations and the accepted image are unchanged.
This closes the identified coverage gaps, not every possible failure combination.

## Native lifecycle: 26 cases

[Tests](../../test/hummingbot/strategy_v2/controllers/test_quote_withdrawal_lifecycle.py)
reuse the preceding inventory lifecycle harness and real controller, orchestrator,
position executor, tracked order, fill update, cancel event and held-position
aggregation. Recorder persistence, strategy I/O and external account observations
remain mocked. The helper retains reference instrument metadata and supplies
seconds for spot book IDs and milliseconds for perpetual book IDs.

Spot BUY/SELL and perpetual long/short withdraw a working entry for stale books,
an unready provider, invalid account values, an out-of-range reference or revoked
session. Perpetual also covers missing account confirmation. Protection sends a
single position-preserving stop even without the normal executor-report signal.

During a deliberately pending cancellation, environmental inputs become valid
again but explicit session revocation remains binding. No replacement proposal
is admitted until terminal cancellation and explicit matching host admission;
wrong generation cannot grant admission. Late 18-decimal fills and duplicate
trade IDs preserve exact quantity and original USDT fees through repeated hold
reports. Four unfilled cases retire without inventing held exposure. No automatic
market close is requested. These tests stop at replacement proposals, not venue
acknowledgments.

## PostgreSQL and transport boundary: 10 cases

[Tests](../../test/integration/postgres/test_mm_joint_send_readiness.py) use the
real ownership session, durable recorder intent, ServiceHost joint readiness,
controllers and RESTAssistant, with a mocked HTTP transport and disposable PG14.

Eight cases change inputs **inside the throttle wait, after intent persistence**:
spot inventory; perpetual long, short or gross exposure; peer provider readiness;
peer account confirmation; peer freshness; peer controller generation. Excessive
gross includes equal long/short quantities, so a zero net position cannot hide it.
Each blocks the queued spot request before transport, durably rejects its known
unsent intent without HALT, permits owned cancellation while restricted and
permits a fresh request after valid observations return.

Two additional cases distinguish transient restrictions from stronger fences:

- Recorder failure before transport blocks submission. If local intent rejection
  cannot commit, the pending row remains and HALT requires reconciliation; no
  safety state is cleared to pass the test. Owned cancellation still works.
- Shutdown during throttle remains latched, including after a stale readiness
  toggle. Known unsent intent rejection and owned cancellation remain possible.

All database runs use the existing disposable test runner. No Demo volume,
credentials, exchange account, production database or service deployment is used.

## Verification and limits

- Final related native regression: **541 passed plus two subtests**, eight
  dependency warnings. This is the relevant suite, not a new whole-repository gate.
- The focused PostgreSQL module: **10 passed**, six dependency warnings.
- Full PostgreSQL regression: **329 passed**, 36 warnings. That run collected
  before the two additional recorder/shutdown cases were added; the focused
  ten-case module run above passes those two as well. The runs overlap by eight
  cases, rather than representing 339 distinct PostgreSQL cases.
- Five isolated in-memory mutants produce **94 expected failures, zero setup
  errors**: automatic readmission on data recovery (26), unwanted position closure
  (26), waiting for a report before protective cancellation (26), skipping final
  joint readiness (8), and incorrectly blocking cancellation during restriction (8).
  These intentionally broken variants are never written to production source.
- Formatting, lint and secret checks pass. Initial native fixture bring-up errors
  involved reference metadata/timestamp units; they are corrected in the helper
  and are not counted as production defects.

Raw commands, logs, JUnit mutant results and isolated mutation plugin are under
`build/mm-coverage-followup-20261103/`. This work adds source-level regression
evidence; it does not claim a fresh load/soak, connected OKX run or Cloud Run gate.
