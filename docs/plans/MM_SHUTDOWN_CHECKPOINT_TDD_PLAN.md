# Preserve the risk-stop latch across shutdown retries

A failed shutdown sets the V2 lifecycle stop flag during cleanup. A later cleanup
attempt previously captured that flag as if it were a pre-existing risk stop,
leaving a retained checkpoint that blocked a reviewed recovery restart.

- [x] Red: reproduce two incomplete owner shutdowns overwriting a previously false
  stop latch; keep a genuinely pre-existing true latch unchanged (real PostgreSQL).
- [x] Red: exercise actual V2 failed cleanup twice and verify the original latch
  is retained; ensure tests run on the asynchronous test wrapper.
- [x] Record the latch once, synchronously before lifecycle cleanup sets its stop
  flag. Use that value for the existing shutdown and MM drawdown checkpoints.
  Preserve explicit drawdown breaches, individual exited controllers and peaks.
- [x] Green: targeted native/PostgreSQL coverage for retry, genuine risk stop,
  drawdown breach and independent controller-stop state.
- [x] Complete native/PostgreSQL/Swagger regressions: 2,371 native tests plus
  11 subtests, 321 PostgreSQL tests; commit the source fix.
- [x] Package and verify the fixed runtime, run relevant packaged stop/restart
  cases and a stopped Demo upgrade/restart. Report image-specific load scope;
  earlier full load must not be relabelled as testing this later source.

Completed on image `51d4d879`, source `06ab51b17`: three packaged E2E groups,
stopped Demo upgrade/restart and unchanged order/fill/position/executor hashes.
See the [recovery report](../../reports/demo/okx-checkpoint-recovery.md).

- [x] Run full unchanged R1 and independent ordinary-entrypoint R2 on final
  image `51d4d879`; all healthy windows and six fault groups pass. Recheck the
  seven natural fills through read-only REST, restore the idle service and
  verify unchanged ledger hashes. [Final release](../../reports/release/mm-checkpoint-final-release.md).

The separately approved Demo checkpoint reset changes historical data only for
that reviewed incident; the source fix does not automatically reset stored HALT
or existing strategy-stop checkpoints. See the [review proposal](../../reports/demo/okx-checkpoint-stop-recovery-proposal.md).
