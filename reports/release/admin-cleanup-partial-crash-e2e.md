# Partial-close crash and rejected readback coverage

Additional behavioral tests exercise the existing cleanup implementation. This is
a test/docs-only follow-up; it introduces no production runtime change.

## Coverage

- **15 new native unit cases:** rejected exchange response, missing/ambiguous/wrong
  ticker, nonpositive/nonfinite/malformed prices, dependency timeout/transport loss,
  ownership loss after reading the ticker, and terminal partial closes on both
  LONG and SHORT. Invalid prices never reach placement; partial closes remain
  tracked and require reconciliation rather than immediate resubmission. Close
  authority does not leak into the caller's context.
- **Eight additional PostgreSQL cases:** stale/future/missing timestamps, wrong
  owner generation, missing terminal-order/account/checkpoint verification, and
  missing remaining-order count. Rejected proof preserves the original receipt,
  recovery state, unrelated risk HALT, ciphertext and audit count.
- **Two native-image E2E cases:** the venue fills 40% of the first close and drops
  WS connections; the service commits the partial fill, then receives SIGKILL.
  Test both a live remainder and an already cancelled remainder. Restart must
  recover the same operation with a new owner, cancel/read back any live remainder,
  close only the verified residual 60%, preserve spot, and complete key removal.
  Independent venue request truth counts every physical placement attempt.

## Verification scope

The unchanged shipping image is `89d5e7c61fcf…`, pinned by manifest
`sha256:33c374fa6983c618cd94065828259eb0de5156f1620e2be0200a47afba4e6de3`.
Tests use isolated disposable PostgreSQL, native connectors, a local KEK and an
independent exchange simulator. All fills are synthetic; existing Demo service,
credentials, account and database are untouched. Real Google/GCP remain deferred.

Related native tests pass **52 cases**; related PostgreSQL tests pass **68 cases**.
The focused runtime file's **21 cases** are included in the 52, not additional.
These checks do not claim a new full regression, actual OKX acceptance or hour-long
load gate. Intentional WS loss and SIGKILL are fault stimuli, not a zero-error-log
acceptance test. The previous image's qualified timing history remains unchanged.

Both E2E cases pass on the same unchanged image:

| Scenario | Committed venue/recorder fills | Physical close sends | Restart result |
| --- | --- | --- | --- |
| Partial live remainder + SIGKILL | 6 / 6 | 3 | Same operation, owner 1 → 2, residual only |
| Partial cancelled remainder + SIGKILL | 6 / 6 | 3 | Same operation, owner 1 → 2, residual only |

The three physical sends are the original close (40% filled), the verified 60%
residual and the other hedge leg. Each scenario verifies exact fees, no remaining
perpetual exposure, preserved spot, one accepted/succeeded root audit pair,
selected ciphertext removal, retained companion credentials and another stopped
restart with unchanged financial history. No Retry or direct ledger repair is
needed in these two cases. Synthetic seed fills are included in each total of six.

Raw results:

- `build/e2e-offline/20261106-160636-fd3be9/result.json`
- `build/e2e-offline/20261106-160819-077f75/result.json`

The [portable evidence](admin-cleanup-partial-crash-evidence.json) embeds both
results, source hashes and native/PostgreSQL command-result summaries. **25 new
cases** are added overall (15 unit, eight PostgreSQL, two native-image E2E).
No new runtime defect was reproduced. Disposable containers were removed by the
test harness; no new bot image was built, so no additional image cleanup is needed.
