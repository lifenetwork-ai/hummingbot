# Random handover and load-producer investigation — 2026-10-31

Status: producer defect fixed with Red/Green tests; two short diagnostics passed;
full-load confirmation running. Runtime image remains `dd602fb2`, unchanged.
No production risk guard, health grace or acceptance deadline was relaxed.

## Confirmed defect

The load client continued generating new fills after its measurement window,
until it reached the nominal fill quota. At a Random handover an empty book
therefore became `fill_generator: backlog_not_drained` even with no request in
flight. A pending working-order lookup could also initiate a new fill after
generation had ended. The retained prior failure remains a failure:
`build/mm-load/20261030-215951-3d41e4` (3,598 soak fills, 10.153-second drain).

The corrected client closes generation at window end, rechecks that boundary
after the working-order lookup, and awaits already-dispatched fill requests.
It explicitly reports `fill_target_shortfall` and its drain policy. A stuck
request still fails under the original ten-second deadline. Original fixed-run
rate, HTTP error, latency, memory and ledger budgets are unchanged. Random-soak
rates were already declared observational because step admission can pause
quotes; that existing distinction is no longer contradicted by post-window
synthetic fill creation. No missing fills are invented or silently discounted.

Two new regressions failed on the old client, while dispatched/stuck request
controls passed. All four pass after the change; the empty-book case also
checks that zero throughput fails the fixed-run rate gate (including its
existing one-fill tolerance). Related budget/exchange regression: 31 passed.
Expanded quote-guard/volatility characterization: 107 passed. Raw evidence is
under `build/random-handover-20261031`.

## Runtime observation

The opt-in `scripts/test_mm_random_handover.py` reuses the full-load image,
100,000-row fixture, 2-CPU bot, pool/TTL and guards. Only Random step duration
is shortened to 30 seconds for a 180-second diagnostic. Bounded wrappers record
the original guard/refresh decisions without changing returned actions.

First run `build/mm-random-handover/20261031-043633-11a09f` passes: 174 actual
fills, all matching recorder quantities/fees and positions; six-fill shortfall
reported, zero errors, drain 0.029s. Its records include a perpetual cancellation
with `inconsistent_own_depth`: the acknowledged private-order state and public
book do not yet agree. Existing guard tests require waiting in that case and
qualifying fresh public observations before resuming. A new sequence test
confirms that this wait recovers, without bypassing the protection or latching
a permanent HALT. This identifies an observed safety wait, not an OKX network
incident: all exchange traffic is local simulation.

Confirmation `build/mm-random-handover/20261031-044129-a1fcc9` also passes with
174 matching fills, six-fill shortfall, zero errors, 0.102s drain and primary
p99 102.55ms. Its added first-refresh records verify that the observation hook
executes even when no quote fails validation. Each short run includes the initial
fixed spot primer plus seven Random step-start events; it is not an hour-long
load acceptance. The cancellation observed in the first run did not require a
runtime patch; original safety behavior remains intact.

The short run does not prove the exact historical inputs at every cancellation
in the older soak; its probe records must not be retroactively assigned to that
older event. The older sustained-liveness incident is also a separate unresolved
cause, not closed by fixing this client.

## Confirmation

Running: complete the same full warm300/mixed900/burst90/soak3600 and six fault
cases using the corrected client, without the diagnostic quote wrappers. Keep
every attempt, then verify the persistent Demo baseline and clean test fixtures.
